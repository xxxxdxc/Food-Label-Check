"""Vision routing, immutable comparisons, and image-bound human decisions."""
import copy
import json
from types import SimpleNamespace

import httpx
import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app.config import Settings, get_settings
from app.db import Document, Task, Usage, init_db
from app.services.projection import project_fields, project_review_items
from app.services.samples import bind_document, merge_documents
from app.services.vision_client import import_candidates, propose_tables, should_use_vision
from app.services.ocr_client import ServiceError
from app.worker import Extraction, extract, run_one
from tests.helpers import image_bytes, synthetic_document
from tests.test_boundaries import proposals, structural_doc
from tests.test_workflow import client, create_upload


def basic_proposal(name, value, raw=None, unit=None, box=None):
    field = {"raw": raw or value, "value": value, "unit": unit}
    if box:
        field["boxPx"] = box
    return {"basic": {name: field}}


def field(doc, name):
    return next(f for f in project_fields(doc, 0) if f.field_id == name)


def request_body(queue, image_index=0):
    image = queue["images"][image_index]
    return {"base_version": queue["document_version"], "image_id": image["id"],
            "reviewSchemaVersion": "1.0", "documentId": queue["document_id"],
            "productId": queue["product_id"], "imageName": image["fileName"],
            "imageSha256": image["sha256"], "threshold": .96, "reviewer": "tester", "decisions": []}


def decision(item, value=None):
    return {"path": item["path"], "action": "correct" if value else "confirm",
            "originalRaw": item["raw"], "originalValue": item["value"], "originalUnit": item["unit"],
            "sourceEvidenceRefs": [e["id"] for e in item["evidence"]],
            "manualText": value or item["raw"], "correctedValue": value or item["value"],
            "correctedUnit": item["unit"]}


@pytest.mark.parametrize("value,unit,status,agreement", [
    ("100.0", "克", "ready", "agree"), ("101", "g", "needs_review", "disagree")])
def test_basic_decimal_alias_comparison_preserves_ocr(value, unit, status, agreement):
    doc = synthetic_document(False)
    original = copy.deepcopy(doc)
    updated = import_candidates(doc, image_bytes(), basic_proposal("netContent", value, unit=unit))
    result = field(updated, "netContent")
    assert result.value == "100" and result.status == status and result.agreement == agreement
    assert result.vision.value == value and result.vision.unit == "g"
    assert doc == original


def test_agree_does_not_promote_uncertain_ocr_and_missing_field_needs_human():
    doc = synthetic_document()
    proposal = basic_proposal("allergenText", "含大豆")
    updated = import_candidates(doc, image_bytes(), proposal)
    assert field(updated, "allergenText").status == "needs_review"
    assert field(updated, "allergenText").agreement == "vision_only"
    candidate = proposals()
    candidate["tables"][0]["rows"][0]["amount"].update(raw="32g", value="32")
    updated = import_candidates(structural_doc(), image_bytes(), candidate)
    result = next(f for f in project_fields(updated, 0) if f.field_id.endswith("protein.amount"))
    assert result.agreement == "agree" and result.value == "32"


def test_ocr_box_is_preferred_and_unmatched_text_uses_estimated_box():
    doc = synthetic_document(False)
    estimated = {"left": 0, "top": 0, "width": 100, "height": 80}
    updated = import_candidates(doc, image_bytes(), basic_proposal("productName", "测试食品", box=estimated))
    result = field(updated, "productName")
    assert next(e for e in result.evidence if e.id == result.vision.evidence_id).box_px.left == 5
    updated = import_candidates(doc, image_bytes(), basic_proposal("productName", "完全不同的内容", box=estimated))
    result = field(updated, "productName")
    assert next(e for e in result.evidence if e.id == result.vision.evidence_id).box_px.left == 0


@pytest.mark.parametrize("proposal", [
    {}, {"basic": {"netContent": {"raw": "NaN", "value": "NaN", "unit": "g"}}},
    {"basic": {"productName": {"raw": "x", "value": "x", "status": "ready"}}},
    {"basic": {"productName": {"raw": "x", "value": "x", "boxPx": {
        "left": 0, "top": 0, "width": 1, "height": 1, "status": "ready"}}}},
])
def test_malformed_candidates_are_rejected_atomically(proposal):
    doc = synthetic_document(False)
    before = copy.deepcopy(doc)
    with pytest.raises(ValueError):
        import_candidates(doc, image_bytes(), proposal)
    assert doc == before


def test_merged_routing_uses_global_missing_fields_and_local_pending_evidence():
    docs = []
    for index in range(2):
        doc = synthetic_document(False)
        # Ensure nothing is globally missing, including allergens.
        doc["facts"]["allergenText"] = copy.deepcopy(doc["facts"]["ingredientsText"])
        if index == 1:
            doc["facts"]["productName"]["status"] = "missing"
            doc["facts"]["productName"].update(raw=None, value=None, sourceRefs=[])
        docs.append(bind_document(doc, "task", SimpleNamespace(
            id=str(index), stored_name=str(index) + ".png", width_px=120, height_px=100)))
    merged = merge_documents(docs)
    assert not should_use_vision(merged, "0") and not should_use_vision(merged, "1")
    docs[1]["facts"]["nutritionTables"][0]["rows"][0]["amount"]["status"] = "needs_review"
    merged = merge_documents(docs)
    assert not should_use_vision(merged, "0") and should_use_vision(merged, "1")
    merged["facts"]["allergenText"].update(status="missing", raw=None, value=None, sourceRefs=[])
    assert should_use_vision(merged, "0") and should_use_vision(merged, "1")


def test_conflicting_images_keep_candidates_and_can_be_reviewed(client, monkeypatch):
    def extraction(task, _):
        docs = []
        for index, file in enumerate(task.files):
            doc = synthetic_document(False)
            if index:
                doc["facts"]["productName"].update(raw="另一个商品", value="另一个商品")
            docs.append(bind_document(doc, task.id, file))
        return merge_documents(docs)
    monkeypatch.setattr("app.worker.extract", extraction)
    task = create_upload(client, 2)
    assert run_one()
    prefix = "/api/audits/" + task["id"]
    queue = client.get(prefix + "/review-items").json()
    item = next(i for i in queue["items"] if i["path"] == "facts.productName")
    assert len(item["candidates"]) == 2 and item["value"] == "测试食品"
    body = request_body(queue, 1)
    body["decisions"] = [decision(item, "另一个商品")]
    response = client.post(prefix + "/corrections", json=body)
    assert response.status_code == 200, response.text
    current = client.get(prefix + "/fields").json()["fields"]
    result = next(f for f in current if f["field_id"] == "productName")
    assert result["value"] == "另一个商品" and result["status"] == "human_confirmed"
    assert len(result["candidates"]) == 2


def test_visual_disagreement_from_second_image_is_not_lost():
    original = synthetic_document(False)
    second = import_candidates(original, image_bytes(), basic_proposal("productName", "另一个商品"))
    docs = [bind_document(doc, "task", SimpleNamespace(
        id=str(i), stored_name=str(i) + ".png", width_px=120, height_px=100))
        for i, doc in enumerate((original, second))]
    result = field(merge_documents(docs), "productName")
    assert result.status == "needs_review" and result.agreement == "disagree"
    assert result.vision.evidence_id.startswith("1:")


def test_no_table_suggestion_requires_human_and_missing_nutrition_remains_unchecked(client, monkeypatch):
    doc = structural_doc()
    doc["facts"]["nutritionTables"] = []
    doc["tableDiagnostics"][0]["tableId"] = None
    def extraction(task, _):
        original = bind_document(doc, task.id, task.files[0])
        visual = bind_document(import_candidates(doc, image_bytes(), {"no_nutrition_table": True}), task.id, task.files[0])
        return Extraction(original, visual, [])
    monkeypatch.setattr("app.worker.extract", extraction)
    task = create_upload(client)
    assert run_one()
    prefix = "/api/audits/" + task["id"]
    queue = client.get(prefix + "/review-items").json()
    assert queue["table_items"][0]["vision_no_table"]
    assert client.post(prefix + "/resume").status_code == 409
    body = request_body(queue)
    body["no_table_images"] = ["someone-else"]
    assert client.post(prefix + "/corrections", json=body).status_code == 422
    body["no_table_images"] = [body["image_id"]]
    response = client.post(prefix + "/corrections", json=body)
    assert response.status_code == 200 and response.json()["review_pending"] == 0, response.text
    assert client.post(prefix + "/resume").status_code == 200
    assert run_one()
    assert client.get(prefix).json()["status"] == "PARTIAL"
    issues = client.get(prefix + "/issues?rule_id=OCR-DOWNSTREAM-GATE").json()
    assert issues[0]["status"] == "NOT_CHECKED"
    assert client.get(prefix + "/review-items").json()["table_items"] == []
    with init_db()() as db:
        history = db.scalars(select(Document).where(Document.task_id == task["id"]).order_by(Document.version)).all()
        assert [d.created_by for d in history] == ["ocr", "vision", "human"]


def test_visual_basic_correction_retains_comparison_and_original(client, monkeypatch):
    doc = synthetic_document(False)
    updated = import_candidates(doc, image_bytes(), basic_proposal("productName", "新名称"))
    monkeypatch.setattr("app.worker.extract", lambda task, _: Extraction(
        bind_document(doc, task.id, task.files[0]), bind_document(updated, task.id, task.files[0]), []))
    task = create_upload(client)
    assert run_one()
    prefix = "/api/audits/" + task["id"]
    queue = client.get(prefix + "/review-items").json()
    item = next(i for i in queue["items"] if i["path"] == "facts.productName")
    assert item["agreement"] == "disagree" and item["vision"]["value"] == "新名称"
    body = request_body(queue)
    body["decisions"] = [decision(item, "新名称")]
    assert client.post(prefix + "/corrections", json=body).status_code == 200
    current = next(f for f in client.get(prefix + "/fields").json()["fields"] if f["field_id"] == "productName")
    assert current["status"] == "human_confirmed" and current["vision"]["value"] == "新名称"
    original = next(f for f in client.get(prefix + "/fields?version=0").json()["fields"] if f["field_id"] == "productName")
    assert original["value"] == "测试食品" and original["vision"] is None


def test_multimage_zoning_keeps_images_separate_and_falls_back_per_image(client, monkeypatch):
    from audit_engine.label_check.shared import document_from_dict
    task = create_upload(client, 2)
    seen = []
    monkeypatch.setattr("app.services.regional_ocr.parse_dual", lambda *_args, **_kwargs: document_from_dict(structural_doc()))
    def zones(data):
        seen.append(data)
        if len(seen) == 2:
            raise ServiceError("ZONING_TIMEOUT", "测试分区失败")
        return {"complete": True, "regions": [{"kind": "nutrition_table", "box_px": {
            "left": 0., "top": 0., "width": 120., "height": 100.}}]}
    monkeypatch.setattr("app.services.regional_ocr.propose_regions", zones)
    monkeypatch.setattr("app.services.vision_client.propose_tables", lambda *_: pytest.fail("No VLM transcription"))
    class OCR:
        def recognize(self, *_args):
            return {}
    assert run_one(OCR())
    assert len(seen) == 2
    result = client.get("/api/audits/" + task["id"]).json()
    assert result["status"] == "WAITING_REVIEW" and result["error"] is None
    assert len(result["warnings"]) == 1 and result["warnings"][0]["image_id"] == task["files"][1]["id"]
    with init_db()() as db:
        history = db.scalars(select(Document).where(Document.task_id == task["id"])).all()
        assert len(history) == 1 and history[0].version == 0
        doc = json.loads(history[0].doc_json)
        assert len({e["id"] for e in doc["evidence"]}) == len(doc["evidence"])
        assert doc["_platform"]["pipeline"] == "vlm_zones_ocr_v1"
        assert [item["mode"] for item in doc["_platform"]["extraction"]] == ["regions", "whole_image_fallback"]
        assert {item["image_id"] for item in doc["_platform"]["extraction"]} == {f["id"] for f in task["files"]}


@pytest.mark.parametrize("failures,expected_calls", [(1, 2), (2, 2)])
def test_sdk_retries_timeout_once_and_counts_each_attempt(monkeypatch, failures, expected_calls):
    monkeypatch.setenv("VLM_ENABLED", "1")
    monkeypatch.setenv("VLM_MODEL", "gpt-6.1-sol")
    get_settings.cache_clear()
    calls = []
    def respond(request):
        calls.append(request)
        body = json.loads(request.content)
        assert body["model"] == "gpt-6.1-sol"
        assert body["max_completion_tokens"] == 6000 and body["reasoning_effort"] == "low"
        assert "temperature" not in body and "max_tokens" not in body
        if len(calls) <= failures:
            raise httpx.ReadTimeout("fake timeout", request=request)
        return httpx.Response(200, json={"id": "test", "object": "chat.completion", "created": 1,
            "model": "test-model", "choices": [{"index": 0, "message": {"role": "assistant",
            "content": json.dumps(basic_proposal("productName", "测试食品"))}, "finish_reason": "stop"}]})
    factory = httpx.Client
    class MockClient(factory):
        def __init__(self, **kwargs):
            super().__init__(transport=httpx.MockTransport(respond), **kwargs)
    monkeypatch.setattr(httpx, "Client", MockClient)
    if failures == 2:
        with pytest.raises(ServiceError):
            propose_tables(synthetic_document(False), image_bytes())
    else:
        assert propose_tables(synthetic_document(False), image_bytes())
    assert len(calls) == expected_calls
    with init_db()() as db:
        assert db.scalar(select(Usage.count).where(Usage.service == "vlm")) == expected_calls


def test_live_requires_vision_config_cache_does_not(monkeypatch):
    monkeypatch.setenv("VLM_API_KEY", "")
    monkeypatch.setenv("VLM_MODEL", "")
    assert Settings(ocr_mode="cache").vlm_enabled is False
    with pytest.raises(ValidationError):
        Settings(ocr_mode="live")


def test_samples_never_invoke_vision(client, monkeypatch):
    def forbidden(*_args):
        pytest.fail("offline samples must not invoke vision")
    monkeypatch.setattr("app.services.vision_client.propose_tables", forbidden)
    response = client.post("/api/audits", data={"source": "sample", "sample_id": "4260290260010"})
    assert response.status_code == 201
    assert run_one()
    assert client.get("/api/audits/" + response.json()["id"]).json()["warnings"] == []


def test_existing_database_adds_warning_column_and_migrates_only_vlm_warnings(client):
    import sqlite3
    import app.db as database
    first, second = create_upload(client), create_upload(client)
    root = get_settings().data_dir
    database._engine.dispose()
    with sqlite3.connect(root / "app.db") as connection:
        connection.execute("ALTER TABLE tasks DROP COLUMN warnings_json")
        connection.execute("UPDATE tasks SET status='WAITING_REVIEW', error_json=? WHERE id=?", (
            json.dumps({"code": "VLM_FALLBACK", "message": "旧警告", "retryable": False}), first["id"]))
        connection.execute("UPDATE tasks SET status='FAILED', error_json=? WHERE id=?", (
            json.dumps({"code": "OCR_AUTH", "message": "识别失败", "retryable": False}), second["id"]))
    database._engine = None
    database.init_db()
    result = client.get("/api/audits/" + first["id"]).json()
    assert result["error"] is None and result["warnings"][0]["code"] == "VLM_FALLBACK"
    result = client.get("/api/audits/" + second["id"]).json()
    assert result["error"]["code"] == "OCR_AUTH" and result["warnings"] == []


def test_no_table_operation_cannot_remove_detected_table(client, monkeypatch):
    monkeypatch.setattr("app.worker.extract", lambda task, _: bind_document(structural_doc(), task.id, task.files[0]))
    task = create_upload(client)
    assert run_one()
    prefix = "/api/audits/" + task["id"]
    queue = client.get(prefix + "/review-items").json()
    body = request_body(queue)
    body["no_table_images"] = [body["image_id"]]
    response = client.post(prefix + "/corrections", json=body)
    assert response.status_code == 422
    assert client.get(prefix).json()["document_version"] == 0


def test_visual_only_agreement_survives_single_image_merge():
    doc = import_candidates(synthetic_document(False), image_bytes(), basic_proposal("allergenText", "含大豆"))
    assert field(merge_documents([doc]), "allergenText").agreement == "vision_only"


def test_visual_nutrient_alias_uses_same_code_as_ocr():
    proposal = proposals()
    proposal["tables"][0]["rows"][0]["nutrient"].update(raw="糖", value="sugars")
    updated = import_candidates(structural_doc(), image_bytes(), proposal)
    assert updated["facts"]["nutritionTables"][0]["rows"][0]["nutrient"]["value"] == "sugar"


def test_matching_uncertain_values_across_images_can_be_reviewed(client, monkeypatch):
    def extraction(task, _):
        docs = []
        for file in task.files:
            doc = synthetic_document(False)
            doc["facts"]["productName"].update(status="needs_review", reviewReasons=["CHECK_NAME"])
            docs.append(bind_document(doc, task.id, file))
        return merge_documents(docs)
    monkeypatch.setattr("app.worker.extract", extraction)
    task = create_upload(client, 2)
    assert run_one()
    prefix = "/api/audits/" + task["id"]
    queue = client.get(prefix + "/review-items").json()
    item = next(i for i in queue["items"] if i["path"] == "facts.productName")
    body = request_body(queue)
    body["decisions"] = [decision(item)]
    response = client.post(prefix + "/corrections", json=body)
    assert response.status_code == 200, response.text

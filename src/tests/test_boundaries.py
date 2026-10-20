import copy
import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from app.config import get_settings
from app.db import Document, Job, Task, init_db
from app.main import app
from app.services.ocr_client import ServiceError, reserve_usage
from app.services.samples import bind_document, merge_documents
from app.services.vision_client import import_candidates
from app.worker import run_one
from tests.helpers import image_bytes, synthetic_document
from tests.test_workflow import client, create_upload, correction_body, extraction


def structural_doc():
    doc = synthetic_document(False)
    doc["tableDiagnostics"] = [{"code": "TABLE_V2_NOT_FOUND", "status": "needs_vision", "message": "请手动核对",
        "imageId": "front", "tableId": "table-1", "boxPx": {"left": 0, "top": 0, "width": 110, "height": 90},
        "evidenceRefs": [doc["evidence"][0]["id"]]}]
    return doc


def proposals():
    return {"tables": [{"tableId": "table-1", "boxPx": {"left": 0, "top": 0, "width": 110, "height": 90},
                       "basis": {"kind": "per_100g", "raw": "每100g"}, "rows": [
        {"nutrient": {"raw": "蛋白质", "value": "protein"}, "amount": {"raw": "3.2g", "value": "3.2", "unit": "g"},
         "nrvPercent": {"raw": "5%", "value": "5", "unit": "%"}}]}]}


def test_vision_import_keeps_ocr_and_requires_human_confirmation():
    doc = structural_doc()
    original = copy.deepcopy(doc)
    updated = import_candidates(doc, image_bytes(), proposals())
    assert doc == original
    assert updated["facts"]["nutritionTables"][0]["rows"][0]["amount"]["status"] == "needs_review"
    assert updated["tableDiagnostics"][0]["status"] == "needs_review"
    assert all(e in updated["evidence"] for e in original["evidence"])
    assert all(e["boxPx"] == updated["tableDiagnostics"][0]["boxPx"] for e in updated["evidence"] if e["sourceType"] == "codex_vision")


def test_vision_cannot_replace_confirmed_table_even_with_an_unresolved_other_table():
    doc = structural_doc()
    doc["tableDiagnostics"][0]["tableId"] = None
    before = copy.deepcopy(doc)
    with pytest.raises(ValueError, match="needs_vision"):
        import_candidates(doc, image_bytes(), proposals())
    assert doc == before


def test_vision_instruction_or_ready_status_in_output_is_rejected():
    candidate = proposals()
    candidate["tables"][0]["rows"][0]["amount"]["status"] = "ready"
    with pytest.raises(ValueError):
        import_candidates(structural_doc(), image_bytes(), candidate)


def test_legacy_extraction_never_triggers_vlm_transcription(client, monkeypatch):
    monkeypatch.setenv("VLM_ENABLED", "1")
    monkeypatch.setenv("VLM_MODEL", "test-model")
    monkeypatch.setenv("VLM_API_KEY", "test-key")
    monkeypatch.setenv("VLM_BASE_URL", "https://api-inference.modelscope.cn/v1")
    get_settings.cache_clear()
    monkeypatch.setattr("app.worker.extract", lambda task, _: bind_document(structural_doc(), task.id, task.files[0]))
    def failure(*_args):
        pytest.fail("layout-first workers must never perform VLM field transcription")
    monkeypatch.setattr("app.services.vision_client.propose_tables", failure)
    task = create_upload(client)
    assert run_one()
    result = client.get("/api/audits/" + task["id"]).json()
    assert result["status"] == "WAITING_REVIEW", result
    assert result["error"] is None
    assert result["warnings"] == []


def test_missing_table_can_be_entered_manually_and_checked(client, monkeypatch):
    doc = structural_doc()
    doc["facts"]["nutritionTables"] = []
    doc["tableDiagnostics"][0]["tableId"] = None
    monkeypatch.setattr("app.worker.extract", lambda task, _: bind_document(doc, task.id, task.files[0]))
    task = create_upload(client)
    assert run_one()
    prefix = "/api/audits/" + task["id"]
    queue = client.get(prefix + "/review-items").json()
    image, diagnostic = queue["images"][0], queue["table_items"][0]
    body = {"base_version": 0, "image_id": image["id"], "reviewSchemaVersion": "1.0",
            "documentId": queue["document_id"], "productId": queue["product_id"], "imageName": image["fileName"],
            "imageSha256": image["sha256"], "threshold": .96, "reviewer": "tester", "decisions": [],
            "table_checked_all": True, "manual_tables": [{"table_id": "manual-table-1",
            "original_code": diagnostic["code"], "source_evidence_refs": diagnostic["evidence_refs"],
            "basis": "per_100g", "basis_raw": "每100g", "rows": [
                {"nutrient": "protein", "amount": "3.2", "unit": "g", "nrv_percent": "5"}]}]}
    changed = client.post(prefix + "/corrections", json=body)
    assert changed.status_code == 200, changed.text
    assert changed.json()["review_pending"] == 0
    assert client.post(prefix + "/resume").status_code == 200
    assert run_one()
    result = client.get(prefix + "/issues?rule_id=NRV-100G-PROTEIN").json()
    assert result[0]["status"] == "PASS"
    assert client.get(prefix + "/fields?version=0").json()["document_version"] == 0


def test_multimage_review_is_bound_to_the_correct_image(client, monkeypatch):
    monkeypatch.setattr("app.worker.extract", lambda task, _: merge_documents([
        bind_document(synthetic_document(), task.id, file) for file in task.files]))
    task = create_upload(client, 2)
    assert run_one()
    prefix = "/api/audits/" + task["id"]
    body = correction_body(client, task["id"])
    queue = client.get(prefix + "/review-items").json()
    other = queue["images"][1]
    wrong = copy.deepcopy(body)
    wrong.update(image_id=other["id"], imageName=other["fileName"], imageSha256=other["sha256"])
    assert client.post(prefix + "/corrections", json=wrong).status_code == 422
    assert client.post(prefix + "/corrections", json=body).status_code == 200
    queue = client.get(prefix + "/review-items").json()
    item = queue["items"][0]
    wrong["base_version"] = 1
    wrong["decisions"][0].update(path=item["path"], originalRaw=item["raw"], originalValue=item["value"],
        originalUnit=item["unit"], sourceEvidenceRefs=[e["id"] for e in item["evidence"]])
    assert client.post(prefix + "/corrections", json=wrong).status_code == 200
    assert client.post(prefix + "/resume").status_code == 200
    assert run_one()
    result = client.get(prefix).json()
    assert result["status"] == "PARTIAL" and result["review_pending"] == 0
    assert client.get(prefix + "/issues?rule_id=OCR-DOWNSTREAM-GATE").json()[0]["status"] == "NOT_CHECKED"


@pytest.mark.parametrize("code,retryable", [("OCR_AUTH", False), ("OCR_QUOTA", False), ("OCR_BUDGET", True), ("OCR_RATE_LIMIT", True), ("OCR_TIMEOUT", True)])
def test_ocr_failures_are_classified_and_timeout_retries_only_once(client, monkeypatch, code, retryable):
    class Client:
        def recognize(self, *_args):
            raise ServiceError(code, "服务失败", retryable)
    task = create_upload(client)
    assert run_one(Client())
    if code == "OCR_TIMEOUT":
        assert run_one(Client())
    prefix = "/api/audits/" + task["id"]
    result = client.get(prefix).json()
    assert result["status"] == "FAILED" and result["error"]["code"] == code
    response = client.post(prefix + "/retry")
    assert response.status_code == (200 if retryable else 409)


def test_daily_budget_is_persistent_across_sessions():
    reserve_usage("ocr", 1)
    with pytest.raises(ServiceError) as caught:
        reserve_usage("ocr", 1)
    assert caught.value.code == "OCR_BUDGET"


def test_real_parser_path_saves_raw_responses_and_structural_review(client):
    class Client:
        def recognize(self, _data, kind):
            return ({"words_result": [{"words": "营养成分表", "location": {"left": 5, "top": 5, "width": 80, "height": 12},
                    "probability": {"average": .99}}]} if kind == "accurate" else {"table_num": 0, "tables_result": []})
    task = create_upload(client)
    assert run_one(Client())
    result = client.get("/api/audits/" + task["id"]).json()
    assert result["status"] == "WAITING_REVIEW", result
    raw = get_settings().data_dir / "ocr" / task["id"]
    assert len(list(raw.glob("r1-*.json"))) == 2
    plan = json.loads(next(raw.glob("z1-*.json")).read_text(encoding="utf-8"))
    assert plan["mode"] == "whole_image_fallback"
    assert all("response" in json.loads(p.read_text(encoding="utf-8")) for p in raw.glob("r1-*.json"))


def test_failed_logins_limit_and_tampered_cookie():
    with TestClient(app) as client:
        for _ in range(5):
            assert client.post("/api/auth/login", json={"passcode": "wrong"}).status_code == 401
        assert client.post("/api/auth/login", json={"passcode": "wrong"}).status_code == 429
        client.cookies.set("session", "9999999999.invalid")
        assert client.get("/api/audits").status_code == 401


def test_request_guard_rejects_cross_origin_and_oversized_body_before_parsing(client):
    assert client.post("/api/audits", headers={"origin": "https://untrusted.example"}).status_code == 403
    response = client.post("/api/audits", content=b"", headers={"content-length": str(40*1024*1024)})
    assert response.status_code == 413

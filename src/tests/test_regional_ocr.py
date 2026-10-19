"""Offline layout-first integration: no external images, OCR or model calls."""
import copy
import io
from types import SimpleNamespace

import pytest
from PIL import Image

from app.config import get_settings
from app.services.ocr_client import ServiceError
from app.services.regional_ocr import extract_file, map_accurate, map_table
from app.services.zoning import prepare_regions, validate_layout, propose_regions, encode_crop, PIPELINE
from audit_engine.label_check.shared import document_from_dict, validate_document
from audit_engine.label_check.shared.review import collect_review_items
from tests.test_workflow import client


def rectangle(x, y, w, h):
    return [{"x": x, "y": y}, {"x": x + w, "y": y},
            {"x": x + w, "y": y + h}, {"x": x, "y": y + h}]


def table_responses(score=.99, duplicate=False):
    rows = [["项目", "每100g", "NRV%"], ["能量", "800kJ", "10%"],
            ["蛋白质", "3.2g", "5%"], ["脂肪", "4g", "7%"],
            ["碳水化合物", "35g", "12%"], ["钠", "120mg", "6%"]]
    if duplicate:
        rows[-1][0] = "脂肪"
    accurate, body = [], []
    for r, row in enumerate(rows):
        for c, text in enumerate(row):
            x, y = 8 + c * 72, 8 + r * 30
            accurate.append({"words": text, "location": {"left": x, "top": y, "width": 65, "height": 22},
                             "probability": {"average": score if r == 2 and c == 1 else .99}})
            body.append({"words": text, "row_start": r, "row_end": r + 1, "col_start": c, "col_end": c + 1,
                         "cell_location": rectangle(x, y, 65, 22)})
    return {"words_result": accurate}, {"table_num": 1, "tables_result": [{
        "body": body, "table_location": rectangle(4, 4, 216, 190)}]}


def fixture(tmp_path):
    import hashlib
    root = get_settings().data_dir / "uploads"
    root.mkdir(parents=True, exist_ok=True)
    # file_path uses the configured files directory, not a caller-provided path.
    from app.services.storage import file_path
    file = SimpleNamespace(id="image1", task_id="task1", stored_name="image1.png", width_px=400, height_px=500)
    target = file_path(file)
    target.parent.mkdir(parents=True, exist_ok=True)
    stream = io.BytesIO()
    Image.new("RGB", (400, 500), "white").save(stream, format="PNG")
    target.write_bytes(stream.getvalue())
    file.sha256 = hashlib.sha256(stream.getvalue()).hexdigest()
    return SimpleNamespace(id="task1"), file


def layout():
    return {"complete": True, "regions": [
        {"kind": "product_name", "box_px": {"left": 10., "top": 20., "width": 100., "height": 24.}},
        {"kind": "net_content", "box_px": {"left": 10., "top": 70., "width": 100., "height": 24.}},
        {"kind": "nutrition_table", "box_px": {"left": 130., "top": 200., "width": 220., "height": 240.}},
    ]}


class OCR:
    def __init__(self, events, score=.99, duplicate=False):
        self.events, self.score, self.duplicate = events, score, duplicate
        self.text_calls = 0

    def recognize(self, data, kind):
        with Image.open(io.BytesIO(data)) as image:
            assert image.width < 400 and image.height < 500
        self.events.append(kind)
        if kind == "table":
            return table_responses(self.score, self.duplicate)[1]
        self.text_calls += 1
        if self.text_calls <= 2:
            text = "玉米片" if self.text_calls == 1 else "净含量：100g"
            return {"words_result": [{"words": text, "location": {"left": 8, "top": 8, "width": 80, "height": 16}, "probability": {"average": .99}}]}
        return table_responses(self.score, self.duplicate)[0]


def test_layout_first_crops_and_maps_to_original_without_vlm_values(tmp_path, monkeypatch):
    task, file = fixture(tmp_path)
    events = []
    def zoning(_):
        events.append("layout")
        return layout()
    monkeypatch.setattr("app.services.regional_ocr.propose_regions", zoning)
    client = OCR(events)
    doc, warnings = extract_file(task, file, client)
    typed = document_from_dict(doc)
    validate_document(typed)
    assert events == ["layout", "accurate", "accurate", "accurate", "table"]
    assert warnings == [] and doc["_platform"]["pipeline"] == PIPELINE
    assert doc["_platform"]["comparisons"] == {}
    assert typed.facts.productName.value == "玉米片"
    assert typed.facts.netContent.status == "ready" and typed.facts.netContent.value == "100"
    assert typed.tableDiagnostics[0].status == "ok"
    assert collect_review_items(typed) == []
    assert all(e.sourceType != "codex_vision" for e in typed.evidence)
    amount = typed.facts.nutritionTables[0].rows[1].amount
    evidence = next(e for e in typed.evidence if e.id in amount.sourceRefs and e.sourceType == "baidu_accurate")
    assert evidence.boxPx.left == 124 + 80
    assert evidence.boxPx.top == 194 + 68
    # Retrying reuses a versioned layout and the exact crop responses, with no calls.
    again, _ = extract_file(task, file, client)
    assert again == doc and len(events) == 5


@pytest.mark.parametrize("score", [.95, None])
def test_low_or_missing_confidence_never_auto_promotes(tmp_path, monkeypatch, score):
    task, file = fixture(tmp_path)
    monkeypatch.setattr("app.services.regional_ocr.propose_regions", lambda _: layout())
    client = OCR([], score=score)
    doc, _ = extract_file(task, file, client)
    items = collect_review_items(document_from_dict(doc))
    item = next(i for i in items if i["path"].endswith("rows[1].amount"))
    assert ("OCR_LOW_CONFIDENCE" if score is not None else "OCR_CONFIDENCE_MISSING") in item["reviewReasons"]


def test_structure_fault_is_table_level_not_23_generic_field_confirmations(tmp_path, monkeypatch):
    task, file = fixture(tmp_path)
    monkeypatch.setattr("app.services.regional_ocr.propose_regions", lambda _: layout())
    doc, _ = extract_file(task, file, OCR([], duplicate=True))
    typed = document_from_dict(doc)
    assert any(d.status != "ok" for d in typed.tableDiagnostics)
    assert all(i["reviewReasons"] != ["TABLE_STRUCTURE_UNCERTAIN"] for i in collect_review_items(typed))
    assert typed.facts.nutritionTables[0].rows[0].amount.status == "ready"


def test_bad_layout_falls_back_once_to_whole_image_ocr_and_is_cached(tmp_path, monkeypatch):
    task, file = fixture(tmp_path)
    events = []
    def fail(_):
        events.append("layout")
        raise ServiceError("ZONING_TIMEOUT", "测试分区超时")
    class FullOCR:
        def recognize(self, data, kind):
            events.append(kind)
            with Image.open(io.BytesIO(data)) as image:
                assert image.size == (400, 500)
            return table_responses()[0 if kind == "accurate" else 1]
    monkeypatch.setattr("app.services.regional_ocr.propose_regions", fail)
    doc, warnings = extract_file(task, file, FullOCR())
    assert events == ["layout", "accurate", "table"]
    assert warnings[0]["code"] == "ZONING_TIMEOUT"
    assert doc["_platform"]["extraction"][0]["mode"] == "whole_image_fallback"
    assert doc["_platform"]["comparisons"] == {}
    extract_file(task, file, FullOCR())
    assert len(events) == 3


@pytest.mark.parametrize("mutate", [
    lambda p: p["regions"][0].update(value="model text must never enter OCR facts"),
    lambda p: p["regions"][0]["box_px"].update(left=-1),
    lambda p: p["regions"][0]["box_px"].update(width=999),
    lambda p: p.update(complete=False),
    lambda p: p["regions"][0]["box_px"].update(left=True),
])
def test_layout_rejects_transcription_and_invalid_boxes(mutate):
    payload = layout()
    mutate(payload)
    with pytest.raises(ValueError):
        validate_layout(payload, 400, 500)


def test_overlaps_merge_and_outside_ocr_coordinates_are_rejected():
    payload = layout()
    payload["regions"].append(copy.deepcopy(payload["regions"][0]))
    assert len(prepare_regions(payload, 400, 500)) == 3
    transform = {"origin": [100, 200], "scale": [2, 3], "width": 100, "height": 100}
    source = {"words_result": [{"words": "x", "location": {"left": 2, "top": 3, "width": 4, "height": 5}}]}
    mapped = map_accurate(source, transform)
    assert mapped["words_result"][0]["location"] == {"left": 104, "top": 209, "width": 8, "height": 15}
    assert source["words_result"][0]["location"]["left"] == 2
    bad = copy.deepcopy(source)
    bad["words_result"][0]["location"]["width"] = 999
    with pytest.raises(ServiceError):
        map_accurate(bad, transform)
    table = {"tables_result": [{"table_location": rectangle(1, 2, 4, 5)}]}
    assert map_table(table, transform)["tables_result"][0]["table_location"][0] == {"x": 102, "y": 206}


def test_zoning_sdk_asks_for_regions_only_and_returns_original_coordinates(monkeypatch):
    import json
    import httpx
    from sqlalchemy import select
    from app.db import Usage, init_db
    monkeypatch.setenv("VLM_ENABLED", "1")
    get_settings.cache_clear()
    requests = []
    def respond(request):
        body = json.loads(request.content)
        requests.append(body)
        assert "tools" not in body
        assert "raw/value" in body["messages"][0]["content"][0]["text"]
        return httpx.Response(200, json={"id": "offline", "object": "chat.completion", "created": 1,
            "model": "test", "choices": [{"index": 0, "message": {"role": "assistant", "content": json.dumps(layout())}, "finish_reason": "stop"}]})
    factory = httpx.Client
    class MockClient(factory):
        def __init__(self, **kwargs):
            super().__init__(transport=httpx.MockTransport(respond), **kwargs)
    monkeypatch.setattr(httpx, "Client", MockClient)
    stream = io.BytesIO()
    Image.new("RGB", (400, 500), "white").save(stream, format="PNG")
    result = propose_regions(stream.getvalue())
    assert result == layout() and len(requests) == 1
    with init_db()() as db:
        assert db.scalar(select(Usage.count).where(Usage.service == "vlm")) == 1


def test_worker_automatically_checks_high_confidence_fields_without_human_confirmation(client, monkeypatch):
    from app.worker import run_one
    from app.db import Document, init_db
    from sqlalchemy import select
    import json
    monkeypatch.setattr("app.services.regional_ocr.propose_regions", lambda _: layout())
    stream = io.BytesIO()
    Image.new("RGB", (400, 500), "white").save(stream, format="PNG")
    response = client.post("/api/audits", data={"source": "upload"}, files={"files": ("offline.png", stream.getvalue(), "image/png")})
    assert response.status_code == 201
    task = response.json()
    assert run_one(OCR([]))
    prefix = "/api/audits/" + task["id"]
    state = client.get(prefix).json()
    assert state["status"] == "CHECKING" and state["review_pending"] == 0 and state["document_version"] == 0
    assert run_one()
    state = client.get(prefix).json()
    assert state["status"] in {"PARTIAL", "COMPLETED"} and state["review_pending"] == 0
    queue = client.get(prefix + "/review-items").json()
    assert queue["pipeline"] == PIPELINE and queue["items"] == [] and queue["table_items"] == []
    assert queue["extraction"][0]["ocr_calls"] == 4
    with init_db()() as db:
        history = db.scalars(select(Document).where(Document.task_id == task["id"])).all()
        assert len(history) == 1 and history[0].created_by == "ocr"
        doc = json.loads(history[0].doc_json)
        assert not any(e["sourceType"] == "codex_vision" for e in doc["evidence"])


def test_exact_region_quota_is_checked_before_any_ocr_call(tmp_path, monkeypatch):
    task, file = fixture(tmp_path)
    monkeypatch.setenv("DAILY_OCR_LIMIT", "3")
    get_settings.cache_clear()
    monkeypatch.setattr("app.services.regional_ocr.propose_regions", lambda _: layout())
    events = []
    with pytest.raises(ServiceError) as caught:
        extract_file(task, file, OCR(events))
    assert caught.value.code == "OCR_QUOTA" and events == []


def test_invalid_confidence_is_rejected_instead_of_coerced():
    transform = {"origin": [0, 0], "scale": [1, 1], "width": 100, "height": 100}
    for score in (True, "0.99", 1.1, float("nan")):
        with pytest.raises(ServiceError) as caught:
            map_accurate({"words_result": [{"words": "x", "probability": {"average": score}}]}, transform)
        assert caught.value.code == "OCR_CONFIDENCE"


def test_crop_resize_maps_axes_independently():
    stream = io.BytesIO()
    Image.new("RGB", (4000, 4000), "white").save(stream, format="PNG")
    data, transform = encode_crop(stream.getvalue(), [100, 200, 3500, 3700])
    with Image.open(io.BytesIO(data)) as image:
        assert max(image.size) == 2500
    raw = {"words_result": [{"words": "x", "location": {"left": 0, "top": 0,
             "width": transform["width"], "height": transform["height"]}}]}
    assert map_accurate(raw, transform)["words_result"][0]["location"] == {
        "left": 100, "top": 200, "width": 3400, "height": 3500}


def test_bad_nutrient_unit_remains_an_editable_field_not_just_a_table_warning(tmp_path, monkeypatch):
    task, file = fixture(tmp_path)
    monkeypatch.setattr("app.services.regional_ocr.propose_regions", lambda _: layout())
    class BadUnit(OCR):
        def recognize(self, data, kind):
            raw = super().recognize(data, kind)
            if kind == "table":
                raw["tables_result"][0]["body"][7]["words"] = "3.2mg"
            elif self.text_calls == 3:
                raw["words_result"][7]["words"] = "3.2mg"
            return raw
    doc, _ = extract_file(task, file, BadUnit([]))
    items = collect_review_items(document_from_dict(doc))
    amount = next(i for i in items if i["path"].endswith("rows[1].amount"))
    assert "NUTRIENT_UNIT_MISMATCH" in amount["reviewReasons"]
    assert len(items) < 4

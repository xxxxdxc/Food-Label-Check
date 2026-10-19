"""Deployment boundaries: persisted quota, data lifecycle and vision history."""
import json

import pytest
from sqlalchemy import select
from app.config import get_settings
from app.db import Document, Job, Task, init_db
from app.engine_bridge import checker_input, packaging_findings
from app.errors import AppError
from app.services.ocr_client import reserve_usage
from app.services.samples import bind_document
from app.services.vision_client import import_candidates
from app.worker import claim_job, run_one
from scripts.delete_audit import delete_audit
from tests.helpers import synthetic_document
from tests.test_boundaries import structural_doc, proposals
from tests.test_workflow import client, create_upload, extraction


def test_exhausted_ocr_budget_returns_429_without_files_or_task(client, monkeypatch):
    monkeypatch.setenv("DAILY_OCR_LIMIT", "2")
    get_settings.cache_clear()
    reserve_usage("ocr", 2)
    from tests.helpers import image_bytes
    response = client.post("/api/audits", files={"files": ("label.png", image_bytes(), "image/png")})
    assert response.status_code == 429 and response.json()["error"]["code"] == "OCR_QUOTA"
    assert client.get("/api/audits").json()["total"] == 0
    assert not list((get_settings().data_dir / "uploads").glob("*"))
    assert client.post("/api/audits", data={"source": "sample", "sample_id": "4260290260010"}).status_code == 201


def test_delete_preview_and_confirmation_only_remove_the_selected_task(client, monkeypatch):
    extraction(monkeypatch)
    first = create_upload(client)
    second = create_upload(client)
    assert run_one()
    first_id = first["id"]
    raw = get_settings().data_dir / "ocr" / first_id
    raw.mkdir(parents=True)
    response_path = raw / (first["files"][0]["id"] + ".accurate.json")
    response_path.write_text('{}')
    preview = delete_audit(first_id)
    assert not preview["deleted"] and response_path.is_file()
    assert client.get("/api/audits/" + first_id).status_code == 200
    with pytest.raises(ValueError):
        delete_audit(first_id, second["id"])
    result = delete_audit(first_id, first_id)
    assert result["deleted"] and not response_path.exists()
    assert client.get("/api/audits/" + first_id).status_code == 404
    assert client.get(first["files"][0]["url"]).status_code == 404
    assert client.get(second["files"][0]["url"]).status_code == 200


def test_delete_refuses_running_job(client):
    task = create_upload(client)
    assert claim_job()
    with pytest.raises(AppError) as caught:
        delete_audit(task["id"], task["id"])
    assert caught.value.code == "JOB_RUNNING"
    assert client.get(task["files"][0]["url"]).status_code == 200


def test_sample_report_preserves_off_attribution(client):
    task = client.post("/api/audits", data={"source": "sample", "sample_id": "4260290260010"}).json()
    assert run_one()
    prefix = "/api/audits/" + task["id"]
    report = client.get(prefix + "/report").json()
    assert report["sample_attribution"]["source_url"].endswith("4260290260010")
    assert "CC BY-SA" in report["sample_attribution"]["license"]
    html = client.get(prefix + "/report?format=html").text
    assert 'href="https://world.openfoodfacts.org/product/4260290260010"' in html


def test_vision_worker_preserves_v0_and_exposes_both_sources(client, monkeypatch):
    monkeypatch.setenv("VLM_ENABLED", "1")
    monkeypatch.setenv("VLM_API_KEY", "fake-test-key")
    monkeypatch.setenv("VLM_MODEL", "fake-model")
    get_settings.cache_clear()
    monkeypatch.setattr("app.worker.extract", lambda task, _: bind_document(structural_doc(), task.id, task.files[0]))
    def propose(doc, image):
        candidate = proposals()
        candidate["tables"][0]["tableId"] = doc["facts"]["nutritionTables"][0]["id"]
        return import_candidates(doc, image, candidate)
    monkeypatch.setattr("app.services.vision_client.propose_tables", propose)
    task = create_upload(client)
    assert run_one()
    prefix = "/api/audits/" + task["id"]
    state = client.get(prefix).json()
    assert state["status"] == "WAITING_REVIEW" and state["document_version"] == 1
    queue = client.get(prefix + "/review-items").json()
    assert queue["ocr_fields"] and queue["items"]
    assert any(e["source_type"] == "codex_vision" for f in queue["items"] for e in f["evidence"])
    with init_db()() as db:
        history = db.scalars(select(Document).where(Document.task_id == task["id"]).order_by(Document.version)).all()
        assert [d.created_by for d in history] == ["ocr", "vision"]
        assert json.loads(history[0].doc_json)["facts"]["nutritionTables"][0]["rows"][1]["amount"]["value"] == "32"


def test_bridge_does_not_claim_complete_table_and_preserves_confidence():
    payload, _ = checker_input(synthetic_document(False), "0")
    assert payload["nutrition_complete"] is False
    assert payload["nutrients"]["protein"]["confidence"] is not None


def test_cross_position_content_compares_decimal_values_in_common_units():
    doc = synthetic_document(False)
    for identifier, text in (("net-a", "净含量 1kg"), ("net-b", "净含量 1000.0g")):
        doc["evidence"].append({"id": identifier, "imageId": "front", "sourceType": "baidu_accurate",
            "text": text, "boxPx": {"left": 0, "top": 0, "width": 10, "height": 10}, "ocrConfidence": .99})
    cross = next(i for i in packaging_findings(doc) if i["rule_id"] == "CROSS-NET_CONTENT")
    assert cross["status"] == "PASS"
    assert len(cross["evidence"]) == 2

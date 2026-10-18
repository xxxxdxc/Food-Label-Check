import copy
import json
from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from app.config import get_settings
from app.db import CheckRun, Document, Job, Task, init_db, utcnow
from app.main import app
from app.worker import claim_job, recover_jobs, run_one
from app.services.samples import bind_document
from tests.helpers import image_bytes, synthetic_document


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("OCR_MODE", "live")
    get_settings.cache_clear()
    with TestClient(app) as session:
        assert session.post("/api/auth/login", json={"passcode": "test-passcode"}).status_code == 200
        yield session


def create_upload(client, count=1):
    result = client.post("/api/audits", data={"source": "upload"},
                         files=[("files", ("label.png", image_bytes(), "image/png")) for _ in range(count)])
    assert result.status_code == 201, result.text
    return result.json()


def extraction(monkeypatch, uncertain=True):
    monkeypatch.setattr("app.worker.extract", lambda task, _client: bind_document(
        synthetic_document(uncertain), task.id, task.files[0]))


def correction_body(client, task_id):
    queue = client.get(f"/api/audits/{task_id}/review-items").json()
    item = next(i for i in queue["items"] if i["path"].endswith("rows[1].amount"))
    image = queue["images"][0]
    return {"base_version": queue["document_version"], "image_id": image["id"], "reviewSchemaVersion": "1.0",
            "documentId": queue["document_id"], "productId": queue["product_id"], "imageName": image["fileName"],
            "imageSha256": image["sha256"], "threshold": .96, "reviewer": "tester",
            "decisions": [{"path": item["path"], "action": "correct", "originalRaw": item["raw"],
                           "originalValue": item["value"], "originalUnit": item["unit"],
                           "sourceEvidenceRefs": [e["id"] for e in item["evidence"]],
                           "manualText": "3.2g", "correctedValue": "3.2", "correctedUnit": "g"}]}


def test_correction_changes_engine_input_and_preserves_ocr_version(client, monkeypatch):
    extraction(monkeypatch)
    task = create_upload(client)
    identifier = task["id"]
    assert run_one()
    assert client.get(f"/api/audits/{identifier}").json()["status"] == "WAITING_REVIEW"
    issues = client.get(f"/api/audits/{identifier}/issues").json()
    assert not [i for i in issues if i["rule_id"] == "NRV-100G-PROTEIN" and i["status"] == "PASS"]
    assert any(i["rule_id"] == "NRV-100G-FAT" for i in issues)
    assert client.post(f"/api/audits/{identifier}/resume").status_code == 409
    body = correction_body(client, identifier)
    changed = client.post(f"/api/audits/{identifier}/corrections", json=body)
    assert changed.status_code == 200, changed.text
    assert changed.json()["stale"] and changed.json()["document_version"] == 1
    assert client.post(f"/api/audits/{identifier}/corrections", json=body).json()["error"]["code"] == "VERSION_CONFLICT"
    first = client.post(f"/api/audits/{identifier}/resume")
    second = client.post(f"/api/audits/{identifier}/resume")
    assert first.status_code == second.status_code == 200
    assert first.json()["job_id"] == second.json()["job_id"]
    assert run_one()
    assert not run_one()
    current = client.get(f"/api/audits/{identifier}").json()
    assert current["status"] == "PARTIAL" and not current["stale"] and current["check_doc_version"] == 1
    fields = client.get(f"/api/audits/{identifier}/fields").json()["fields"]
    protein = next(f for f in fields if f["field_id"].endswith("protein.amount"))
    assert protein["value"] == "3.2" and protein["status"] == "human_confirmed" and protein["version"] == 1
    original = client.get(f"/api/audits/{identifier}/fields?version=0").json()["fields"]
    assert next(f for f in original if f["field_id"].endswith("protein.amount"))["value"] == "32"
    result = client.get(f"/api/audits/{identifier}/issues?rule_id=NRV-100G-PROTEIN").json()[0]
    assert result["calculation"]["expected_percent"] == "5" and result["status"] == "PASS"
    assert result["regulation"]["clause_id"] is None and result["evidence"][0]["box_px"]
    decision = client.put(f"/api/audits/{identifier}/issues/{result['issue_id']}/decision", json={"decision": "confirmed"})
    assert decision.status_code == 200 and decision.json()["review_decision"] == "confirmed"
    report = client.get(f"/api/audits/{identifier}/report").json()
    assert report["confirmed_fields"] and report["unchecked"] and report["corrections"]
    html = client.get(f"/api/audits/{identifier}/report?format=html")
    assert html.status_code == 200 and "@media print" in html.text and "data:image/jpeg;base64" in html.text
    assert client.get(task["files"][0]["url"]).status_code == 200
    client.cookies.clear()
    assert client.get(task["files"][0]["url"]).status_code == 401
    with init_db()() as db:
        assert db.scalar(select(CheckRun).where(CheckRun.task_id == identifier).order_by(CheckRun.id.desc())).doc_version == 1


def test_public_samples_work_without_network(client):
    catalog = client.get("/api/samples").json()
    assert len(catalog) == 3
    response = client.post("/api/audits", data={"source": "sample", "sample_id": catalog[0]["id"]})
    assert response.status_code == 201
    assert run_one()
    task = client.get("/api/audits/" + response.json()["id"]).json()
    assert task["status"] in {"WAITING_REVIEW", "PARTIAL"}, task
    assert task["summary"]


def test_claim_is_atomic_and_stale_job_recovers(client):
    task = create_upload(client)
    with ThreadPoolExecutor(max_workers=2) as pool:
        claimed = list(pool.map(lambda _: claim_job(), range(2)))
    assert sum(job is not None for job in claimed) == 1
    with init_db()() as db:
        row = db.get(Job, next(job for job in claimed if job))
        row.locked_at = utcnow() - timedelta(minutes=6)
        db.commit()
    recover_jobs()
    assert claim_job() == next(job for job in claimed if job)


@pytest.mark.parametrize("filename,data,expected", [("fake.png", b"not-an-image", 415),
    ("label.gif", image_bytes(), 415), ("label.jpg", image_bytes(), 415), ("huge.png", b"x" * (10*1024*1024+1), 413)],
    ids=["invalid", "extension", "mismatch", "oversize"])
def test_invalid_upload_never_publishes_files(client, filename, data, expected):
    response = client.post("/api/audits", files={"files": (filename, data, "image/png")})
    assert response.status_code == expected
    assert client.get("/api/audits").json()["total"] == 0
    assert not list((get_settings().data_dir / "uploads").glob("*"))


def test_valid_first_image_invalid_second_leaves_nothing(client):
    response = client.post("/api/audits", files=[("files", ("ok.png", image_bytes(), "image/png")),
                                                               ("files", ("bad.png", b"invalid", "image/png"))])
    assert response.status_code == 415
    assert client.get("/api/audits").json()["total"] == 0
    assert not list((get_settings().data_dir / "uploads").glob("*"))

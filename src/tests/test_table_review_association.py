"""Historical fallback tables use one explicit, image-bound review submission."""
import copy

import pytest
from sqlalchemy import select

from app.db import Document, init_db
from app.services.projection import project_review_items
from app.services.samples import bind_document, merge_documents
from app.services.table_association import associate_review_tables
from app.worker import run_one
from tests.helpers import synthetic_document
from tests.test_workflow import client, create_upload, correction_body


def fallback_document():
    doc = synthetic_document()
    doc["tableDiagnostics"] = [{
        "code": "TABLE_V2_NOT_FOUND", "status": "needs_vision", "message": "OCR recovered candidate rows",
        "imageId": "front", "tableId": None, "boxPx": None,
        "evidenceRefs": doc["facts"]["nutritionTables"][0]["basis"]["sourceRefs"][:],
    }]
    return doc


def setup_review(client, monkeypatch):
    monkeypatch.setattr("app.worker.extract", lambda task, _: bind_document(
        fallback_document(), task.id, task.files[0]))
    task = create_upload(client)
    assert run_one()
    prefix = "/api/audits/" + task["id"]
    queue = client.get(prefix + "/review-items").json()
    body = correction_body(client, task["id"])
    diagnostic = queue["table_items"][0]
    body.update(table_checked_all=True, tableDecisions=[{
        "tableId": diagnostic["table_id"], "originalCode": diagnostic["code"],
        "sourceEvidenceRefs": diagnostic["evidence_refs"], "action": "confirm_complete",
    }])
    return task, prefix, queue, body


def original_json(task_id):
    with init_db()() as db:
        return db.scalar(select(Document.doc_json).where(Document.task_id == task_id, Document.version == 0))


def test_association_is_evidence_based_and_does_not_change_status_or_source():
    doc = fallback_document()
    before = copy.deepcopy(doc)
    linked = associate_review_tables(doc)
    assert linked["tableDiagnostics"][0]["tableId"] == "table-1"
    assert linked["tableDiagnostics"][0]["status"] == "needs_vision"
    assert doc == before
    assert project_review_items(doc, 0)["table_items"][0]["table_id"] == "table-1"
    assert merge_documents([doc])["tableDiagnostics"][0]["tableId"] == "table-1"
    # Ready and pending cells may coexist; status is not evidence of identity.
    doc["facts"]["nutritionTables"][0]["rows"][1]["amount"]["status"] = "ready"
    assert associate_review_tables(doc)["tableDiagnostics"][0]["tableId"] == "table-1"


@pytest.mark.parametrize("case", ["two_tables", "wrong_refs", "no_refs", "count", "omission", "other_image"])
def test_ambiguous_or_image_wide_diagnostics_stay_separate(case):
    doc = fallback_document()
    diagnostic = doc["tableDiagnostics"][0]
    if case == "two_tables":
        other = copy.deepcopy(doc["facts"]["nutritionTables"][0])
        other["id"] = "table-2"
        doc["facts"]["nutritionTables"].append(other)
    elif case == "wrong_refs":
        diagnostic["evidenceRefs"] = doc["facts"]["productName"]["sourceRefs"][:]
    elif case == "no_refs":
        diagnostic["evidenceRefs"] = []
    elif case == "other_image":
        diagnostic["imageId"] = "back"
    else:
        diagnostic["code"] = "TABLE_COUNT_MISMATCH" if case == "count" else "FULLTEXT_NUTRIENT_OMITTED"
    assert associate_review_tables(doc)["tableDiagnostics"][0]["tableId"] is None


def test_old_task_read_and_combined_confirmation_preserve_original(client, monkeypatch):
    task, prefix, queue, body = setup_review(client, monkeypatch)
    before = original_json(task["id"])
    assert '"tableId": null' in before
    assert queue["table_items"][0]["table_id"]
    assert original_json(task["id"]) == before
    response = client.post(prefix + "/corrections", json=body)
    assert response.status_code == 200, response.text
    assert response.json()["document_version"] == 1
    assert response.json()["review_pending"] == 0
    reviewed = client.get(prefix + "/review-items").json()
    assert reviewed["items"] == reviewed["table_items"] == []
    with init_db()() as db:
        import json
        latest = json.loads(db.scalar(select(Document.doc_json).where(Document.task_id == task["id"], Document.version == 1)))
    assert len(latest["facts"]["nutritionTables"]) == 1
    assert latest["tableDiagnostics"][0]["code"] == "HUMAN_REVIEWED_TABLE"
    assert original_json(task["id"]) == before
    assert client.post(prefix + "/corrections", json=body).status_code == 409


@pytest.mark.parametrize("case", ["unchecked", "fields_pending", "stale_refs", "stale_code", "wrong_image", "duplicate"])
def test_incomplete_or_mismatched_confirmation_is_atomic(client, monkeypatch, case):
    task, prefix, _, body = setup_review(client, monkeypatch)
    before = original_json(task["id"])
    if case == "unchecked":
        body["table_checked_all"] = False
    elif case == "fields_pending":
        body["decisions"] = []
    elif case == "stale_refs":
        body["tableDecisions"][0]["sourceEvidenceRefs"] = []
    elif case == "stale_code":
        body["tableDecisions"][0]["originalCode"] = "TABLE_NOT_DETECTED"
    elif case == "wrong_image":
        body["imageSha256"] = "0" * 64
    else:
        body["tableDecisions"].append(copy.deepcopy(body["tableDecisions"][0]))
    response = client.post(prefix + "/corrections", json=body)
    assert response.status_code == 422, response.text
    assert client.get(prefix).json()["document_version"] == 0
    assert original_json(task["id"]) == before


@pytest.mark.parametrize("use_existing_id", [False, True])
def test_structure_replacement_uses_existing_table_id(client, monkeypatch, use_existing_id):
    task, prefix, queue, body = setup_review(client, monkeypatch)
    diagnostic = queue["table_items"][0]
    body["decisions"] = []
    body["tableDecisions"] = []
    body["manual_tables"] = [{
        "table_id": diagnostic["table_id"] if use_existing_id else "manual-duplicate",
        "original_code": diagnostic["code"], "source_evidence_refs": diagnostic["evidence_refs"],
        "basis": "per_100g", "basis_raw": "每100g", "rows": [
            {"nutrient": "protein", "amount": "3.2", "unit": "g", "nrv_percent": "5"}],
    }]
    response = client.post(prefix + "/corrections", json=body)
    assert response.status_code == (200 if use_existing_id else 422), response.text
    with init_db()() as db:
        import json
        latest = json.loads(db.scalar(select(Document.doc_json).where(Document.task_id == task["id"]).order_by(Document.version.desc())))
    assert len(latest["facts"]["nutritionTables"]) == 1


def test_unbound_diagnostic_cannot_append_duplicate_table(client, monkeypatch):
    doc = fallback_document()
    doc["tableDiagnostics"][0]["code"] = "TABLE_COUNT_MISMATCH"
    monkeypatch.setattr("app.worker.extract", lambda task, _: bind_document(doc, task.id, task.files[0]))
    task = create_upload(client)
    assert run_one()
    prefix = "/api/audits/" + task["id"]
    body = correction_body(client, task["id"])
    queue = client.get(prefix + "/review-items").json()
    diagnostic = queue["table_items"][0]
    assert diagnostic["table_id"] is None
    body.update(decisions=[], table_checked_all=True, manual_tables=[{
        "table_id": "manual-duplicate", "original_code": diagnostic["code"],
        "source_evidence_refs": diagnostic["evidence_refs"], "basis": "per_100g", "basis_raw": "每100g",
        "rows": [{"nutrient": "protein", "amount": "3.2", "unit": "g"}],
    }])
    response = client.post(prefix + "/corrections", json=body)
    assert response.status_code == 422
    assert "重复表" in response.text
    assert client.get(prefix).json()["document_version"] == 0


def test_serving_size_must_be_reviewed_before_structure(client, monkeypatch):
    doc = fallback_document()
    basis = doc["facts"]["nutritionTables"][0]["basis"]
    basis.update(kind="per_serving", raw="每份32g", servingSize=copy.deepcopy(
        doc["facts"]["nutritionTables"][0]["rows"][1]["amount"]))
    monkeypatch.setattr("app.worker.extract", lambda task, _: bind_document(doc, task.id, task.files[0]))
    task = create_upload(client)
    assert run_one()
    prefix = "/api/audits/" + task["id"]
    body = correction_body(client, task["id"])
    diagnostic = client.get(prefix + "/review-items").json()["table_items"][0]
    body.update(table_checked_all=True, tableDecisions=[{
        "tableId": diagnostic["table_id"], "originalCode": diagnostic["code"],
        "sourceEvidenceRefs": diagnostic["evidence_refs"], "action": "confirm_complete",
    }])
    response = client.post(prefix + "/corrections", json=body)
    assert response.status_code == 422
    assert "Confirm all table fields" in response.text
    assert client.get(prefix).json()["document_version"] == 0

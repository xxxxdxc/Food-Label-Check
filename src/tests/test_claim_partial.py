"""Partial validation, scoped retries and immutable offline recovery."""
import copy
import json

import pytest
from sqlalchemy import select, func

from app.claim_worker import run_one as run_claim
from app.db import ClaimPrecheck, ClaimPrecheckAttempt, ClaimPrecheckValidation, Task, Usage, init_db
from app.errors import AppError
from app.services.claim_precheck import validate_opinions
from app.services.claim_revalidation import revalidate_saved
from app.services.samples import bind_document
from app.services.ocr_client import ServiceError
from app.worker import run_one
from tests.test_claim_precheck import claim_doc, good_reply
from tests.test_workflow import client, create_upload


def setup_claims(client, monkeypatch):
    doc = claim_doc()
    other = copy.deepcopy(doc["facts"]["claims"][0])
    other["id"] = "claim:2"
    other["category"] = "quantity"
    other["text"].update(raw="每袋356kcal", value="每袋356kcal")
    doc["facts"]["claims"].append(other)
    monkeypatch.setattr("app.worker.extract", lambda task, _: bind_document(doc, task.id, task.files[0]))
    task = create_upload(client)
    assert run_one()
    return task, "/api/audits/" + task["id"]


def mixed_reply(payload, _files=None):
    reply, model = good_reply(payload)
    reply["items"][-1].update(verdict="contradiction", reference_ids=[])
    return reply, model


def test_partial_response_preserves_valid_items_and_retries_only_failures(client, monkeypatch):
    _, prefix = setup_claims(client, monkeypatch)
    audit_before = client.get(prefix).json()
    fields_before = client.get(prefix + "/fields").json()
    issues_before = client.get(prefix + "/issues").json()
    assert run_claim(mixed_reply)
    result = client.get(prefix + "/claim-prechecks").json()
    assert result["status"] == "partial"
    accepted, rejected = result["items"]
    assert accepted["status"] == "completed" and accepted["verdict"] == "insufficient_evidence"
    assert rejected["status"] == "unavailable" and rejected["verdict"] is None
    assert rejected["error_code"] == "UNSUPPORTED_VERDICT"
    queued = client.post(prefix + "/claim-prechecks/retry?version=0").json()
    assert queued["status"] == "pending"
    assert queued["items"][0] == accepted
    assert queued["items"][1]["status"] == "pending"
    assert client.post(prefix + "/claim-prechecks/retry?version=0").json()["status"] == "pending"
    called = []
    def retry_failed(payload, files):
        called.append([c["claim_id"] for c in payload["claims"]])
        return good_reply(payload)
    assert run_claim(retry_failed)
    assert called == [[rejected["claim_id"]]]
    final = client.get(prefix + "/claim-prechecks").json()
    assert final["status"] == "completed" and final["items"][0] == accepted
    assert client.get(prefix).json() == audit_before
    assert client.get(prefix + "/fields").json() == fields_before
    assert client.get(prefix + "/issues").json() == issues_before
    assert not run_claim(lambda *_: pytest.fail("no automatic retries"))


@pytest.mark.parametrize("failure", ["network", "version", "extra_id", "missing_id", "model"])
def test_retry_failure_cannot_overwrite_completed_opinions(client, monkeypatch, failure):
    _, prefix = setup_claims(client, monkeypatch)
    assert run_claim(mixed_reply)
    accepted = client.get(prefix + "/claim-prechecks").json()["items"][0]
    client.post(prefix + "/claim-prechecks/retry?version=0")
    def fail(payload, _files):
        if failure == "network":
            raise ServiceError("MODEL_API_ERROR", "测试网络故障")
        raw, model = good_reply(payload)
        if failure == "version": raw["document_version"] += 1
        if failure == "extra_id": raw["items"].append({**raw["items"][0], "claim_id": "invented"})
        if failure == "missing_id": raw["items"] = []
        if failure == "model": model = "wrong-model"
        return raw, model
    assert run_claim(fail)
    result = client.get(prefix + "/claim-prechecks").json()
    assert result["status"] == "partial" and result["items"][0] == accepted
    assert result["items"][1]["status"] == "unavailable" and result["items"][1]["verdict"] is None


@pytest.mark.parametrize("bad_item", ["schema", "citation", "missing_materials"])
def test_item_errors_are_isolated_after_identity_validation(client, monkeypatch, bad_item):
    setup_claims(client, monkeypatch)
    with init_db()() as db:
        payload = json.loads(db.scalar(select(ClaimPrecheck)).input_json)
    raw, model = good_reply(payload)
    if bad_item == "schema": del raw["items"][1]["reason"]
    if bad_item == "citation": raw["items"][1]["supporting_evidence_refs"] = ["invented"]
    if bad_item == "missing_materials": raw["items"][1]["missing_materials"] = []
    result = validate_opinions(raw, payload, model)
    assert result["status"] == "partial"
    assert result["items"][0]["status"] == "completed" and result["items"][1]["verdict"] is None


def test_offline_recovery_keeps_original_attempt_and_is_idempotent(client, monkeypatch):
    _, prefix = setup_claims(client, monkeypatch)
    with init_db()() as db:
        row = db.scalar(select(ClaimPrecheck))
        raw, model = mixed_reply(json.loads(row.input_json))
        original_audit = json.dumps({"raw_response": raw, "model_actual": model})
        row.status, row.error_code, row.error_message, row.attempts = "unavailable", "INVALID_RESPONSE", "旧版整批拒绝", 1
        db.add(ClaimPrecheckAttempt(batch_id=row.id, attempt=1, result_json=original_audit, error_code="INVALID_RESPONSE"))
        batch_id = row.id
        db.commit()
    with init_db()() as db:
        recovered = revalidate_saved(db, batch_id)
        db.commit()
    assert recovered["status"] == "partial"
    with init_db()() as db:
        assert revalidate_saved(db, batch_id) == recovered
        assert db.scalar(select(func.count()).select_from(ClaimPrecheckValidation)) == 1
        assert db.scalar(select(func.count()).select_from(ClaimPrecheckAttempt)) == 1
        assert db.scalar(select(ClaimPrecheckAttempt)).result_json == original_audit
        assert db.scalar(select(ClaimPrecheckAttempt)).error_code == "INVALID_RESPONSE"
        assert db.get(ClaimPrecheck, batch_id).attempts == 1
        assert db.scalar(select(func.count()).select_from(Usage)) == 0
    result = client.get(prefix + "/claim-prechecks").json()
    assert result["status"] == "partial" and result["revalidated_from_attempt_id"]
    assert result["items"][0]["verdict"] == "insufficient_evidence"
    assert result["items"][1]["verdict"] is None
    from scripts.delete_audit import delete_audit
    task_id = prefix.rsplit("/", 1)[1]
    assert delete_audit(task_id, task_id)["deleted"]
    with init_db()() as db:
        assert db.scalar(select(func.count()).select_from(ClaimPrecheckValidation)) == 0
        assert db.scalar(select(func.count()).select_from(ClaimPrecheckAttempt)) == 0


def test_offline_recovery_rejects_changed_document(client, monkeypatch):
    task, _ = setup_claims(client, monkeypatch)
    assert run_claim(mixed_reply)
    with init_db()() as db:
        batch = db.scalar(select(ClaimPrecheck))
        db.get(Task, task["id"]).document_version += 1
        db.commit()
        with pytest.raises(AppError, match="当前版本"):
            revalidate_saved(db, batch.id)


def test_late_old_attempt_cannot_be_selected_for_offline_recovery(client, monkeypatch):
    setup_claims(client, monkeypatch)
    assert run_claim(mixed_reply)
    with init_db()() as db:
        batch = db.scalar(select(ClaimPrecheck))
        current = db.scalar(select(ClaimPrecheckAttempt))
        current_source = current.id
        raw, model = good_reply(json.loads(batch.input_json))
        raw["items"][0]["reason"] = "迟到的旧尝试，不能恢复"
        db.add(ClaimPrecheckAttempt(batch_id=batch.id, attempt=0,
            result_json=json.dumps({"raw_response": raw, "model_actual": model})))
        batch_id = batch.id
        db.commit()
    with init_db()() as db:
        result = revalidate_saved(db, batch_id)
        assert result["revalidated_from_attempt_id"] == current_source
        assert result["status"] == "partial"
        assert result["items"][0]["reason"] != "迟到的旧尝试，不能恢复"

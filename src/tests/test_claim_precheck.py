import copy
import json
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from sqlalchemy import select, func

from app.config import get_settings
from app.db import ClaimPrecheck, ClaimPrecheckAttempt, Document, Task, Usage, init_db
from app.claim_worker import run_one as run_claim
from app.worker import run_one
from app.services.claim_precheck import (
    call_model, eligible_claims, make_input, reserve_call, validate_opinions,
)
from app.services.ocr_client import ServiceError
from app.services.samples import bind_document
from tests.helpers import synthetic_document
from tests.test_workflow import client, create_upload, correction_body


def claim_doc(text="非油炸", status="ready"):
    doc = synthetic_document()
    ref = "claim:source"
    doc["evidence"].append({"id": ref, "imageId": "front", "sourceType": "baidu_accurate", "text": text,
        "boxPx": {"left": 0, "top": 0, "width": 100, "height": 20},
        "ocrConfidence": .99 if status == "ready" else .5})
    doc["facts"]["claims"] = [{"id": "claim:1", "category": "process", "basis": "unknown", "quantity": None,
        "text": {"raw": text, "value": text, "unit": None, "sourceRefs": [ref],
                 "status": status, "reviewReasons": [] if status == "ready" else ["LOW_OCR_CONFIDENCE"]}}]
    return doc


def extracted_task(client, monkeypatch, text="非油炸", status="ready"):
    monkeypatch.setattr("app.worker.extract", lambda task, _: bind_document(claim_doc(text, status), task.id, task.files[0]))
    task = create_upload(client)
    assert run_one()
    return task, "/api/audits/" + task["id"]


def good_reply(payload, _files=None):
    return {"document_version": payload["document_version"], "items": [
        {"claim_id": c["claim_id"], "verdict": "insufficient_evidence", "reason": "包装自述不能证明生产工艺。",
         "supporting_evidence_refs": [], "reference_ids": [], "missing_materials": ["该产品工艺证明"]}
        for c in payload["claims"]]}, payload["model_requested"]


def test_auto_schedule_read_is_free_dedup_and_opinion_does_not_mutate_audit(client, monkeypatch):
    task, prefix = extracted_task(client, monkeypatch)
    before_task = client.get(prefix).json()
    before_fields = client.get(prefix + "/fields").json()
    before_issues = client.get(prefix + "/issues").json()
    unknown = next(i for i in before_issues if i["rule_id"] == "CLAIM-UNKNOWN")
    assert unknown["claim_id"] and unknown["evidence"]
    assert unknown["regulation"]["verification_status"] == "not_applicable" and unknown["suggestion"] is None
    for _ in range(3):
        body = client.get(prefix + "/claim-prechecks").json()
        assert body["status"] == "pending" and not body["stale"]
        assert body["items"][0]["claim_id"].startswith(task["files"][0]["id"])
    with init_db()() as db:
        assert db.scalar(select(func.count()).select_from(ClaimPrecheck)) == 1
        assert db.scalar(select(func.count()).select_from(Usage)) == 0
    assert run_claim(good_reply)
    assert not run_claim(lambda *_: pytest.fail("duplicate call"))
    body = client.get(prefix + "/claim-prechecks").json()
    assert body["status"] == "completed" and body["items"][0]["verdict"] == "insufficient_evidence"
    assert body["items"][0]["reference_citations"] == []
    assert client.get(prefix).json() == before_task
    assert client.get(prefix + "/fields").json() == before_fields
    assert client.get(prefix + "/issues").json() == before_issues


@pytest.mark.parametrize("text,status", [("高蛋白", "ready"), ("high_fiber", "ready"), ("非油炸", "needs_review")])
def test_covered_or_unconfirmed_claims_make_zero_calls(client, monkeypatch, text, status):
    _, prefix = extracted_task(client, monkeypatch, text, status)
    assert client.get(prefix + "/claim-prechecks").json()["items"] == []
    assert not run_claim(lambda *_: pytest.fail("excluded claim must not call model"))


def test_model_failure_is_isolated_and_only_post_can_retry(client, monkeypatch):
    _, prefix = extracted_task(client, monkeypatch)
    before = client.get(prefix).json()
    def fail(*_):
        raise ServiceError("MODEL_API_ERROR", "测试超时")
    assert run_claim(fail)
    for _ in range(3):
        result = client.get(prefix + "/claim-prechecks").json()
        assert result["status"] == "unavailable" and result["items"][0]["verdict"] is None
    assert not run_claim(lambda *_: pytest.fail("no auto retry"))
    assert client.get(prefix).json() == before
    assert client.post(prefix + "/claim-prechecks/retry?version=0").json()["status"] == "pending"
    assert client.post(prefix + "/claim-prechecks/retry?version=0").json()["status"] == "pending"
    assert run_claim(good_reply)
    with init_db()() as db:
        assert db.scalar(select(func.count()).select_from(ClaimPrecheckAttempt)) == 2
    assert client.post(prefix + "/claim-prechecks/retry?version=0").json()["status"] == "completed"
    assert not run_claim(lambda *_: pytest.fail("completed opinions are cached"))


def test_new_document_version_does_not_reuse_or_send_old_batch(client, monkeypatch):
    _, prefix = extracted_task(client, monkeypatch)
    body = correction_body(client, prefix.rsplit("/", 1)[1])
    assert client.post(prefix + "/corrections", json=body).status_code == 200
    assert client.get(prefix + "/claim-prechecks?version=0").json()["stale"]
    assert client.get(prefix + "/claim-prechecks").json()["status"] == "unavailable"
    assert run_claim(lambda *_: pytest.fail("stale batch must not be sent"))
    assert client.post(prefix + "/claim-prechecks/retry?version=0").status_code == 409
    assert client.post(prefix + "/claim-prechecks/retry?version=1").status_code == 409
    assert client.post(prefix + "/resume").status_code == 200
    assert run_one()
    assert client.get(prefix + "/claim-prechecks").json()["status"] == "pending"
    assert run_claim(good_reply)
    assert client.get(prefix + "/claim-prechecks").json()["document_version"] == 1


def test_manifest_change_invalidates_cache_without_get_triggering_call(client, monkeypatch, tmp_path):
    _, prefix = extracted_task(client, monkeypatch)
    assert run_claim(good_reply)
    from app.services import claim_precheck as service
    changed = json.loads(service.MANIFEST.read_text(encoding="utf-8"))
    changed["version"] += "-updated"
    path = tmp_path / "refs.json"
    path.write_text(json.dumps(changed), encoding="utf-8")
    monkeypatch.setattr(service, "MANIFEST", path)
    assert client.get(prefix + "/claim-prechecks").json()["stale"]
    assert not run_claim(lambda *_: pytest.fail("GET must not create a batch"))
    assert client.post(prefix + "/claim-prechecks/retry?version=0").json()["status"] == "pending"


@pytest.mark.parametrize("mutation", ["version", "claim_id", "citation", "evidence", "self_proof", "process_proof", "model"])
def test_invalid_opinions_and_fake_citations_rejected(client, monkeypatch, mutation):
    _, _prefix = extracted_task(client, monkeypatch)
    with init_db()() as db:
        row = db.scalar(select(ClaimPrecheck))
        payload = json.loads(row.input_json)
    reply, model = good_reply(payload)
    item = reply["items"][0]
    if mutation == "version": reply["document_version"] += 1
    if mutation == "claim_id": item["claim_id"] = "invented"
    if mutation == "citation": item["reference_ids"] = ["invented"]
    if mutation == "evidence": item["supporting_evidence_refs"] = ["invented"]
    if mutation == "model": model = "different-model"
    if mutation in {"self_proof", "process_proof"}:
        item["verdict"] = "supported"
        item["supporting_evidence_refs"] = (payload["claims"][0]["source_evidence_refs"] if mutation == "self_proof"
            else [e["id"] for e in payload["evidence"]])
    if mutation in {"version", "claim_id", "model"}:
        with pytest.raises(ValueError):
            validate_opinions(reply, payload, model)
    else:
        rejected = validate_opinions(reply, payload, model)
        assert rejected["status"] == "unavailable"
        assert rejected["items"][0]["verdict"] is None
        assert rejected["items"][0]["error_code"]


def test_quota_reservations_are_atomic_and_independent(monkeypatch):
    monkeypatch.setenv("DAILY_CLAIM_PRECHECK_LIMIT", "2")
    get_settings.cache_clear()
    reserve_call(); reserve_call()
    with pytest.raises(ServiceError, match="额度"):
        reserve_call()
    with init_db()() as db:
        assert sum(db.scalars(select(Usage.count).where(Usage.service == "vlm"))) == 2
        assert sum(db.scalars(select(Usage.count).where(Usage.service == "claim_precheck"))) == 2


def test_two_executors_do_not_send_same_batch_twice(client, monkeypatch):
    extracted_task(client, monkeypatch)
    calls = []
    def respond(payload, files):
        calls.append(payload)
        return good_reply(payload, files)
    with ThreadPoolExecutor(2) as pool:
        list(pool.map(lambda _: run_claim(respond), range(2)))
    assert len(calls) == 1


def test_real_sdk_contract_uses_existing_gateway_records_model_and_never_retries(client, monkeypatch):
    task, _ = extracted_task(client, monkeypatch)
    monkeypatch.setenv("VLM_ENABLED", "1")
    get_settings.cache_clear()
    with init_db()() as db:
        row = db.scalar(select(ClaimPrecheck))
        payload = json.loads(row.input_json)
        files = list(db.get(Task, task["id"]).files)
    captured = []
    def respond(request):
        assert request.url.host == "api.example.test"
        assert request.headers["authorization"] == "Bearer test-key"
        if request.method == "GET":
            return httpx.Response(200, json={"object": "list", "data": [{"id": payload["model_requested"], "object": "model", "created": 1, "owned_by": "test"}]})
        body = json.loads(request.content)
        captured.append(body)
        assert body["model"] == "gpt-6.1-sol" and "temperature" not in body and "tools" not in body
        reply, model = good_reply(payload)
        return httpx.Response(200, json={"id": "test", "object": "chat.completion", "created": 1, "model": model,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": json.dumps(reply)}, "finish_reason": "stop"}]})
    factory = httpx.Client
    class MockClient(factory):
        def __init__(self, **kwargs):
            super().__init__(transport=httpx.MockTransport(respond), **kwargs)
    monkeypatch.setattr(httpx, "Client", MockClient)
    raw, model = call_model(payload, files)
    assert validate_opinions(raw, payload, model)["items"]
    assert len(captured) == 1
    assert len(captured[0]["messages"][1]["content"]) == 2


def test_references_are_relevant_prompts_not_unverified_rules(client, monkeypatch):
    _, prefix = extracted_task(client, monkeypatch, "无添加")
    with init_db()() as db:
        payload = json.loads(db.scalar(select(ClaimPrecheck)).input_json)
    assert len(payload["references"]) == 1
    ref = payload["references"][0]
    assert not ref["can_support_verdict"] and ref["source_type"] == "internal_guidance"
    assert ref["source_verification"] == "source_unverified"


def test_invalid_json_retains_model_identity_in_attempt_audit(client, monkeypatch):
    extracted_task(client, monkeypatch)
    assert run_claim(lambda payload, _: ("not json", payload["model_requested"]))
    with init_db()() as db:
        row = db.scalar(select(ClaimPrecheckAttempt))
        audit = json.loads(row.result_json)
        assert audit["model_actual"] == "gpt-6.1-sol" and audit["raw_response"] == "not json"
        assert row.error_code == "INVALID_RESPONSE"


@pytest.mark.parametrize("verdict", ["supported", "contradiction"])
def test_internal_material_prompts_cannot_be_used_as_product_proof(client, monkeypatch, verdict):
    extracted_task(client, monkeypatch, "无添加")
    with init_db()() as db:
        payload = json.loads(db.scalar(select(ClaimPrecheck)).input_json)
    raw, actual = good_reply(payload)
    raw["items"][0].update(verdict=verdict,
        supporting_evidence_refs=[e["id"] for e in payload["evidence"]],
        reference_ids=[r["id"] for r in payload["references"]])
    rejected = validate_opinions(raw, payload, actual)
    assert rejected["items"][0]["error_code"] == "UNSUPPORTED_VERDICT"
    assert "准入资料" in rejected["items"][0]["error_message"]
    assert rejected["items"][0]["verdict"] is None


def test_expired_attempt_cannot_overwrite_newer_retry(client, monkeypatch):
    extracted_task(client, monkeypatch)
    def paused_then_returns(payload, files):
        with init_db()() as db:
            row = db.scalar(select(ClaimPrecheck))
            row.attempts = 2
            row.status = "completed"
            row.result_json = json.dumps({"items": [], "newer_attempt": True})
            db.commit()
        return good_reply(payload, files)
    assert run_claim(paused_then_returns)
    with init_db()() as db:
        row = db.scalar(select(ClaimPrecheck))
        assert row.status == "completed" and row.attempts == 2
        assert json.loads(row.result_json)["newer_attempt"]
        assert db.scalar(select(func.count()).select_from(ClaimPrecheckAttempt)) == 1

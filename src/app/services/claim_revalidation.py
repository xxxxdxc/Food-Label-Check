"""Explicit offline recovery from an immutable model attempt; no network calls."""
import hashlib
import json

from sqlalchemy import select

from app.db import ClaimPrecheck, ClaimPrecheckAttempt, ClaimPrecheckValidation, Task
from app.deps import begin_write
from app.errors import AppError
from app.services.audit_service import current_document
from app.services.claim_precheck import VALIDATOR_VERSION, canonical, digest, make_input, merge_results, validate_opinions


def revalidate_saved(db, batch_id):
    begin_write(db)
    row = db.get(ClaimPrecheck, batch_id)
    if not row:
        raise AppError(404, "PRECHECK_NOT_FOUND", "初审批次不存在")
    task = db.get(Task, row.task_id)
    latest = db.scalar(select(ClaimPrecheck.id).where(ClaimPrecheck.task_id == task.id,
        ClaimPrecheck.doc_version == row.doc_version).order_by(ClaimPrecheck.id.desc()).limit(1))
    if (task.document_version != row.doc_version or latest != row.id
            or row.status in {"pending", "running"}
            or row.fingerprint != digest(make_input(task, current_document(db, task), row.doc_version))):
        raise AppError(409, "STALE_INPUT", "只允许恢复当前版本、输入未变且未运行的最新初审批次")
    source = db.scalar(select(ClaimPrecheckAttempt).where(ClaimPrecheckAttempt.batch_id == row.id,
                       ClaimPrecheckAttempt.attempt == row.attempts)
                       .order_by(ClaimPrecheckAttempt.id.desc()).limit(1))
    if not source or not source.result_json:
        raise AppError(409, "NO_SAVED_RESPONSE", "没有可重新校验的模型响应")
    existing = db.scalar(select(ClaimPrecheckValidation).where(
        ClaimPrecheckValidation.source_attempt_id == source.id,
        ClaimPrecheckValidation.validator_version == VALIDATOR_VERSION))
    if existing:
        return json.loads(existing.result_json)
    audit = json.loads(source.result_json)
    full = json.loads(row.input_json)
    requested = audit.get("request_payload", full)
    full_claims = {c["claim_id"]: c for c in full["claims"]}
    if (digest({**requested, "claims": full["claims"]}) != row.fingerprint
            or not requested["claims"]
            or any(c != full_claims.get(c.get("claim_id")) for c in requested["claims"])):
        raise AppError(409, "STALE_INPUT", "已保存响应的请求上下文与当前批次不符")
    if not audit.get("raw_response"):
        raise AppError(409, "NO_SAVED_RESPONSE", "这次调用未保存可用模型响应，未发起新调用")
    checked = validate_opinions(audit["raw_response"], requested, audit.get("model_actual"))
    previous = json.loads(row.result_json) if row.result_json else None
    result = merge_results(full, previous, checked["items"], audit.get("model_actual"))
    result["revalidated_from_attempt_id"] = source.id
    db.add(ClaimPrecheckValidation(batch_id=row.id, source_attempt_id=source.id,
        validator_version=VALIDATOR_VERSION, input_fingerprint=row.fingerprint,
        raw_sha256=hashlib.sha256((audit["raw_response"] if isinstance(audit["raw_response"], str)
                                  else canonical(audit["raw_response"])).encode()).hexdigest(), result_json=canonical(result)))
    row.result_json, row.status = canonical(result), result["status"]
    row.error_code = "PARTIAL_RESPONSE" if row.status == "partial" else "INVALID_RESPONSE" if row.status == "unavailable" else None
    row.error_message = "已按项重新校验，请查看失败项具体原因；未新增模型调用" if row.status != "completed" else None
    db.flush()
    return result

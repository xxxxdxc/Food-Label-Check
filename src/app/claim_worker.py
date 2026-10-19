"""Independent claim opinion executor. Main audit state is never changed."""
import json
import logging
from datetime import timedelta
from threading import Event

from sqlalchemy import select, update

from app.db import ClaimPrecheck, ClaimPrecheckAttempt, Task, init_db, utcnow
from app.deps import begin_write
from app.services.claim_precheck import call_model, validate_opinions, make_input, digest, retry_input, merge_results, item_base
from app.services.audit_service import current_document
from app.services.ocr_client import ServiceError

log = logging.getLogger("claim_precheck_worker")


def run_one(caller=None, *, batch_id=None):
    with init_db()() as db:
        begin_write(db)
        # Never resend an interrupted request automatically: it may have been billed.
        db.execute(update(ClaimPrecheck).where(ClaimPrecheck.status == "running",
            ClaimPrecheck.started_at < utcnow() - timedelta(minutes=5)).values(
                status="unavailable", error_code="INTERRUPTED", error_message="初审执行中断，请主动重试"))
        query = select(ClaimPrecheck).where(ClaimPrecheck.status == "pending")
        if batch_id is not None:
            query = query.where(ClaimPrecheck.id == batch_id)
        row = db.scalar(query.order_by(ClaimPrecheck.id).limit(1))
        if row is None:
            db.commit()
            return False
        task = db.get(Task, row.task_id)
        if task.document_version != row.doc_version:
            row.status, row.error_code, row.error_message = "unavailable", "STALE_DOCUMENT", "文档已更新，旧版本未发送模型"
            db.commit()
            return True
        current = make_input(task, current_document(db, task, row.doc_version), row.doc_version)
        if digest(current) != row.fingerprint:
            row.status, row.error_code, row.error_message = "unavailable", "STALE_INPUT", "初审输入或配置已变化，请主动重试"
            db.commit()
            return True
        full_payload = json.loads(row.input_json)
        previous = json.loads(row.result_json) if row.result_json else None
        payload = retry_input(full_payload, previous)
        if not payload["claims"]:
            row.status, row.error_code, row.error_message = "completed", None, None
            db.commit()
            return True
        row.status, row.started_at = "running", utcnow()
        row.attempts += 1
        identifier, attempt = row.id, row.attempts
        files = list(task.files)
        db.commit()
    result, error, raw, actual_model = None, None, None, None
    try:
        raw, actual_model = (caller or call_model)(payload, files)
        result = validate_opinions(raw, payload, actual_model)
    except ServiceError as exc:
        error = (exc.code, exc.message)
    except (ValueError, KeyError, TypeError, IndexError):
        error = ("INVALID_RESPONSE", "本次响应的结构、版本、模型或声称标识校验失败，未采纳本次意见")
    except Exception as exc:
        log.error("claim precheck %s unavailable: %s", identifier, type(exc).__name__)
        error = ("PRECHECK_ERROR", "模型初审暂不可用，可主动重试")
    new_items = result["items"] if result else [{**item_base(c, payload, actual_model), "status": "unavailable",
        "verdict": None, "error_code": error[0], "error_message": error[1]} for c in payload["claims"]]
    result = merge_results(full_payload, previous, new_items, actual_model)
    if not error and result["status"] != "completed":
        error = ("PARTIAL_RESPONSE" if result["status"] == "partial" else "INVALID_RESPONSE",
                 "部分意见未通过校验，请查看对应声称的具体原因" if result["status"] == "partial" else "本次意见均未通过校验，请查看各项具体原因")
    with init_db()() as db:
        begin_write(db)
        row = db.get(ClaimPrecheck, identifier)
        if row is None:
            return True
        encoded = json.dumps(result, ensure_ascii=False) if result else None
        audit = json.dumps({"model_requested": payload["model_requested"], "model_actual": actual_model,
                            "raw_response": raw, "request_payload": payload, "validated_result": result}, ensure_ascii=False)
        db.add(ClaimPrecheckAttempt(batch_id=identifier, attempt=attempt, result_json=audit,
                                  error_code=error[0] if error else None))
        # A paused/expired executor may return after a newer explicit retry.
        # Retain its audit, but never let the old generation overwrite the new one.
        if row.status == "running" and row.attempts == attempt:
            row.result_json = encoded
            row.status = result["status"]
            row.error_code, row.error_message = error if error else (None, None)
        db.commit()
    return True


def run_forever(stop):
    while not stop.is_set():
        try:
            worked = run_one()
        except Exception as exc:
            log.error("claim executor unavailable: %s", type(exc).__name__)
            worked = False
        if not worked:
            stop.wait(1)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    if args.once:
        run_one()
    else:
        try:
            run_forever(Event())
        except KeyboardInterrupt:
            pass

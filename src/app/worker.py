"""Run the persistent queue separately with python -m app.worker [--once]."""
import argparse
import json
import logging
import time
from datetime import timedelta
from dataclasses import dataclass
from threading import Event, Thread

from sqlalchemy import select, text, update
from audit_engine.label_check.shared import document_from_dict, validate_document
from audit_engine.label_check_v2.channel import ChannelStore
from app.config import get_settings
from app.db import CheckRun, Document, Issue, Job, RuleClause, Task, init_db, utcnow
from app.deps import begin_write
from app.engine_bridge import check
from app.services.audit_service import append_document, current_document, enqueue, set_status
from app.services.ocr_client import BaiduClient, ServiceError
from app.services.projection import project_issues
from app.services.review_service import pending
from app.services.samples import SAMPLE_ROOT, bind_document, merge_documents

log = logging.getLogger("food_label_worker")


@dataclass
class Extraction:
    document: dict
    vision_document: dict | None
    warnings: list


def recover_jobs():
    with init_db()() as db:
        db.execute(update(Job).where(Job.status == "running", Job.locked_at < utcnow() - timedelta(minutes=5))
                   .values(status="queued", locked_at=None))
        db.commit()


def claim_job():
    with init_db()() as db:
        begin_write(db)
        job_id = db.execute(text("UPDATE jobs SET status='running', attempts=attempts+1, locked_at=:now "
            "WHERE id=(SELECT id FROM jobs WHERE status='queued' ORDER BY created_at,id LIMIT 1) "
            "AND NOT EXISTS(SELECT 1 FROM jobs WHERE status='running') RETURNING id"),
            {"now": utcnow().replace(tzinfo=None).isoformat(sep=" ")}).scalar()
        db.commit()
        return job_id


def heartbeat(job_id, stop):
    while not stop.wait(30):
        with init_db()() as db:
            db.execute(update(Job).where(Job.id == job_id, Job.status == "running").values(locked_at=utcnow()))
            db.commit()


def perform_check(document, version, channel):
    store = ChannelStore(get_settings().data_dir / "channels.sqlite") if channel else None
    try:
        return check(document, str(version), channel, store)
    finally:
        if store:
            store.close()


def save_check(db, task, document, version, result):
    payload, output, _gate = result
    row = CheckRun(task_id=task.id, doc_version=version,
                   engine_input_json=json.dumps(payload, ensure_ascii=False),
                   engine_output_json=json.dumps(output, ensure_ascii=False), policy_version=output["policy_version"])
    db.add(row)
    db.flush()
    clauses = {r.rule_id: {"rule_id": r.rule_id, "standard": r.standard, "edition": r.edition,
                           "clause_id": r.clause_id, "verified": r.verified, "source_note": r.source_note}
               for r in db.scalars(select(RuleClause))}
    issues = project_issues(output, document, version, clauses)
    for item in issues:
        db.add(Issue(task_id=task.id, check_run_id=row.id, issue_json=item.model_dump_json()))
    task.check_doc_version = version
    task.summary_json = json.dumps({s: sum(i.status == s for i in issues) for s in ("PASS", "FAIL", "REVIEW", "NOT_CHECKED")})
    # Independent queue: scheduling failure must not roll back deterministic checks.
    try:
        from app.services.claim_precheck import schedule
        with db.begin_nested():
            schedule(db, task, document, version)
    except Exception as exc:
        log.error("claim precheck scheduling unavailable: %s", type(exc).__name__)
    return any(i.status == "NOT_CHECKED" for i in issues)


def extract(task, client):
    settings = get_settings()
    docs, warnings = [], []
    for file in task.files:
        if task.source == "sample":
            doc = json.loads((SAMPLE_ROOT / (task.sample_id + ".json")).read_text(encoding="utf-8"))
        else:
            if settings.ocr_mode != "live":
                raise ServiceError("CACHE_MODE", "缓存模式无法识别新上传图片")
            from app.services.regional_ocr import extract_file
            doc, file_warnings = extract_file(task, file, client)
            warnings.extend(file_warnings)
        docs.append(bind_document(doc, task.id, file))
    document = merge_documents(docs)
    validate_document(document_from_dict(document))
    return Extraction(document, None, warnings)


def run_one(client=None):
    recover_jobs()
    job_id = claim_job()
    if not job_id:
        return False
    stop = Event()
    thread = Thread(target=heartbeat, args=(job_id, stop), daemon=True)
    thread.start()
    try:
        with init_db()() as db:
            job = db.get(Job, job_id)
            task = db.get(Task, job.task_id)
            kind, task_id = job.kind, task.id
            set_status(task, "EXTRACTING" if kind == "extract" else "CHECKING")
            db.commit()
            files = list(task.files)
            if kind == "extract":
                extracted = extract(task, client or BaiduClient())
                document = extracted.document if isinstance(extracted, Extraction) else extracted
            else:
                document = current_document(db, task)
            version, channel = task.document_version, task.channel
        warnings = extracted.warnings if kind == "extract" and isinstance(extracted, Extraction) else []
        vision_document = extracted.vision_document if kind == "extract" and isinstance(extracted, Extraction) else None
        if kind == "extract":
            with init_db()() as db:
                begin_write(db)
                task = db.get(Task, task_id)
                if db.scalar(select(Document.id).where(Document.task_id == task_id).limit(1)) is None:
                    append_document(db, task, document, "ocr")
                    db.flush()
                    if vision_document:
                        append_document(db, task, vision_document, "vision")
                document = current_document(db, task)
                version = task.document_version
                task.review_pending = pending(document)
                task.error_json = None
                task.warnings_json = json.dumps(warnings, ensure_ascii=False)
                if task.review_pending == 0:
                    job = db.get(Job, job_id)
                    job.status, job.locked_at = "done", None
                    db.flush()
                    set_status(task, "CHECKING")
                    enqueue(db, task, "check")
                    db.commit()
                    return True
                db.commit()
        result = perform_check(document, version, channel)
        with init_db()() as db:
            begin_write(db)
            task, job = db.get(Task, task_id), db.get(Job, job_id)
            partial = save_check(db, task, document, version, result)
            job.status, job.locked_at, job.error = "done", None, None
            db.flush()
            if task.document_version != version:
                set_status(task, "CHECKING")
                enqueue(db, task, "check")
            elif task.review_pending:
                set_status(task, "WAITING_REVIEW")
            else:
                if task.status == "EXTRACTING":
                    set_status(task, "CHECKING")
                set_status(task, "PARTIAL" if partial else "COMPLETED")
            db.commit()
    except Exception as exc:
        error = exc if isinstance(exc, ServiceError) else ServiceError("WORKER_ERROR", "处理失败，请检查 Worker 日志", True)
        log.error("job %s failed: %s", job_id, type(exc).__name__)
        with init_db()() as db:
            begin_write(db)
            job = db.get(Job, job_id)
            task = db.get(Task, job.task_id)
            job.error = json.dumps(error.as_dict(), ensure_ascii=False)
            job.locked_at = None
            if error.code == "OCR_TIMEOUT" and job.attempts < 2:
                job.status = "queued"
            else:
                job.status = "failed"
                task.error_json = job.error
                set_status(task, "FAILED")
            db.commit()
    finally:
        stop.set()
        thread.join(timeout=2)
    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    get_settings()
    client = BaiduClient()
    claim_stop = Event()
    if not args.once:
        from app.claim_worker import run_forever
        init_db()
        Thread(target=run_forever, args=(claim_stop,), daemon=True).start()
    try:
        while True:
            worked = run_one(client)
            if args.once:
                break
            if not worked:
                time.sleep(get_settings().worker_poll_s)
    except KeyboardInterrupt:
        pass
    finally:
        claim_stop.set()


if __name__ == "__main__":
    main()

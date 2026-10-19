"""Transactional task commands, independently testable before HTTP activation."""
import json
from sqlalchemy import select
from app.db import Document, IssueDecision, Job, Task
from app.deps import begin_write
from app.errors import AppError
from app.services.audit_service import current_document, enqueue, get_task, job_receipt, set_status, task_schema
from app.services.report import issues_for_task
from app.services.review_service import apply_corrections
from app.services.storage import file_path
from app.services.ocr_client import require_ocr_capacity


def create_task(db, task_id, source, sample_id, channel, prepared):
    written = []
    committed = False
    try:
        begin_write(db)
        if source == "upload":
            # Only a minimum is known before zoning. The worker checks the exact
            # uncached region-call count once the layout has been validated.
            require_ocr_capacity(db, len(prepared))
        task = Task(id=task_id, status="QUEUED", standard_mode="gb28050_2025_precheck",
                    channel=channel, source=source, sample_id=sample_id, document_version=0,
                    review_pending=0, files=[record for record, _ in prepared])
        db.add(task)
        for record, data in prepared:
            path = file_path(record)
            with path.open("xb") as output:
                written.append(path)
                output.write(data)
        enqueue(db, task, "extract")
        db.flush()
        result = task_schema(task)
        db.commit()
        committed = True
        return result
    except Exception:
        db.rollback()
        if not committed:
            for path in written:
                path.unlink(missing_ok=True)
        raise


def correct_task(db, audit_id, body):
    begin_write(db)
    task = get_task(db, audit_id)
    apply_corrections(db, task, body)
    db.flush()
    result = task_schema(task)
    db.commit()
    return result


def resume_task(db, audit_id):
    begin_write(db)
    task = get_task(db, audit_id)
    active = db.scalar(select(Job).where(Job.task_id == audit_id, Job.status.in_(["queued", "running"])))
    if active:
        return job_receipt(active)
    if task.status not in {"WAITING_REVIEW", "COMPLETED", "PARTIAL"}:
        raise AppError(409, "STATE_CONFLICT", "当前状态不能重新检查")
    if task.review_pending:
        error = AppError(409, "REVIEW_PENDING", "仍有待复核项，请先完成复核")
        error.review_pending = task.review_pending
        raise error
    current_document(db, task)
    set_status(task, "CHECKING")
    job = enqueue(db, task, "check")
    db.commit()
    return job_receipt(job)


def retry_task(db, audit_id):
    begin_write(db)
    task = get_task(db, audit_id)
    if task.status != "FAILED" or not task.error_json or not json.loads(task.error_json).get("retryable"):
        raise AppError(409, "NOT_RETRYABLE", "此任务当前不可重试")
    has_doc = db.scalar(select(Document.id).where(Document.task_id == audit_id, Document.version == task.document_version))
    if has_doc:
        current_document(db, task, task.document_version)
    elif task.document_version != 0:
        raise AppError(409, "DOCUMENT_NOT_FOUND", "当前文档版本不存在，不能重试")
    task.error_json = None
    set_status(task, "QUEUED")
    job = enqueue(db, task, "check" if has_doc else "extract")
    db.commit()
    return job_receipt(job)


def decide_issue(db, audit_id, issue_id, body):
    begin_write(db)
    task = get_task(db, audit_id)
    if task.check_doc_version != task.document_version:
        raise AppError(409, "STALE_RESULT", "结果已过期，请等待重新检查")
    issue = next((i for i in issues_for_task(db, task) if i.issue_id == issue_id), None)
    if not issue:
        raise AppError(404, "ISSUE_NOT_FOUND", "问题不存在")
    row = db.get(IssueDecision, (audit_id, issue_id, issue.doc_version))
    if body.decision == "open":
        if row:
            db.delete(row)
    elif row:
        row.decision, row.note = body.decision, body.note
    else:
        db.add(IssueDecision(task_id=audit_id, issue_id=issue_id, doc_version=issue.doc_version,
                             decision=body.decision, note=body.note))
    db.commit()
    issue.review_decision = body.decision
    return issue

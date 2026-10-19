"""Task state, immutable documents, and job enqueueing."""
from __future__ import annotations

import json
import uuid
from datetime import timezone
from app.errors import AppError
from sqlalchemy import func, select
from app.db import Task, Document, Job, utcnow
from app.schemas.contract import AuditTask, AuditFile, JobReceipt

TRANSITIONS = {
    "QUEUED": {"EXTRACTING", "CHECKING", "FAILED"},
    "EXTRACTING": {"WAITING_REVIEW", "CHECKING", "FAILED"},
    "WAITING_REVIEW": {"CHECKING", "FAILED"},
    "CHECKING": {"COMPLETED", "PARTIAL", "FAILED", "WAITING_REVIEW"},
    "COMPLETED": {"CHECKING"}, "PARTIAL": {"CHECKING"}, "FAILED": {"QUEUED"},
}


def set_status(task: Task, status: str) -> None:
    if status != task.status and status not in TRANSITIONS.get(task.status, set()):
        raise AppError(409, "STATE_CONFLICT", f"任务不能从 {task.status} 转为 {status}")
    task.status = status
    task.updated_at = utcnow()


def get_task(db, task_id: str) -> Task:
    task = db.get(Task, task_id)
    if task is None or ("visitor_id" in db.info and task.owner_id != db.info["visitor_id"]):
        raise AppError(404, "AUDIT_NOT_FOUND", "任务不存在")
    return task


def current_document(db, task: Task, version: int | None = None) -> dict:
    target = task.document_version if version is None else version
    row = db.scalar(select(Document).where(Document.task_id == task.id, Document.version == target))
    if row is None:
        raise AppError(404, "DOCUMENT_NOT_FOUND", "文档版本不存在或仍在抽取")
    return json.loads(row.doc_json)


def append_document(db, task: Task, doc: dict, created_by: str) -> int:
    exists = db.scalar(select(func.count()).select_from(Document).where(Document.task_id == task.id))
    version = task.document_version + 1 if exists else 0
    db.add(Document(task_id=task.id, version=version, doc_json=json.dumps(doc, ensure_ascii=False), created_by=created_by))
    task.document_version = version
    return version


def enqueue(db, task: Task, kind: str) -> Job:
    db.flush()
    existing = db.scalar(select(Job).where(Job.task_id == task.id, Job.status.in_(["queued", "running"])))
    if existing:
        return existing
    job = Job(id=str(uuid.uuid4()), task_id=task.id, kind=kind, status="queued", attempts=0)
    db.add(job)
    return job


def job_receipt(job: Job) -> JobReceipt:
    return JobReceipt(job_id=job.id, task_id=job.task_id, kind=job.kind, status=job.status)


def task_schema(task: Task) -> AuditTask:
    files = [AuditFile(
        id=file.id, display_name=file.display_name, width_px=file.width_px,
        height_px=file.height_px, sha256=file.sha256,
        url=f"/api/audits/{task.id}/files/{file.id}",
    ) for file in task.files]
    return AuditTask(
        id=task.id, status=task.status, standard_mode=task.standard_mode,
        channel=task.channel, source=task.source, files=files,
        document_version=task.document_version, check_doc_version=task.check_doc_version,
        stale=task.check_doc_version is not None and task.document_version != task.check_doc_version,
        review_pending=task.review_pending,
        summary=json.loads(task.summary_json) if task.summary_json else None,
        error=json.loads(task.error_json) if task.error_json else None,
        warnings=json.loads(task.warnings_json) if task.warnings_json else [],
        created_at=task.created_at.replace(tzinfo=timezone.utc),
        updated_at=task.updated_at.replace(tzinfo=timezone.utc),
    )

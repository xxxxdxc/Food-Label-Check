"""V1 endpoints; all OCR and rule execution belongs to the separate Worker."""
from __future__ import annotations

import hmac
import json
import uuid
from typing import Literal
from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from sqlalchemy import func, select

from app.config import Settings, get_settings
from app.db import Document, File as StoredFile, Task
from app.deps import SESSION_TTL_SECONDS, get_db, make_session, rate_limit, require_session
from app.engine_bridge import SCOPE
from app.errors import AppError
from app.schemas.contract import (
    AuditIssue, AuditList, AuditReport, AuditTask, CorrectionsRequest, ErrorResponse,
    FieldsResponse, IssueDecisionRequest, JobReceipt, LoginRequest, LoginResponse,
    ReviewItemsResponse, SampleSummary, StandardOption,
)
from app.services import samples as sample_service
from app.services.audit_service import current_document, get_task, task_schema
from app.services.projection import project_fields, project_review_items
from app.services.report import issues_for_task, report_html, report_json
from app.services.review_service import apply_corrections
from app.services.storage import file_path, prepare_upload
from app.services import commands

public_router = APIRouter(
    prefix="/api", responses={422: {"model": ErrorResponse}},
)
router = APIRouter(
    prefix="/api",
    dependencies=[Depends(require_session)],
    responses={
        code: {"model": ErrorResponse}
        for code in (401, 404, 409, 413, 415, 422, 429)
    },
)


@public_router.post("/auth/login", response_model=LoginResponse, responses={401: {"model": ErrorResponse}})
def login(body: LoginRequest, request: Request, settings: Settings = Depends(get_settings)) -> Response:
    rate_limit(request, "login", 5, 600, record=False)
    if not hmac.compare_digest(body.passcode.encode(), settings.access_passcode.encode()):
        rate_limit(request, "login", 5, 600)
        raise AppError(401, "INVALID_CREDENTIALS", "口令无效")
    response = JSONResponse(content={"authenticated": True})
    response.set_cookie("session", make_session(settings), max_age=SESSION_TTL_SECONDS,
                        httponly=True, secure=request.url.scheme == "https", samesite="lax", path="/")
    return response


@router.get("/standards", response_model=list[StandardOption])
def standards():
    return [StandardOption(mode="gb28050_2025_precheck",
            label="GB 28050-2025 新版预检（2027-03-16 起实施）", coverage=SCOPE)]


@router.get("/samples", response_model=list[SampleSummary])
def samples():
    return [SampleSummary(id=r["code"], label=r["productName"] or r["code"], source_url=r["productUrl"],
                          image_url=r["imageUrl"], license=r["license"],
                          description="Open Food Facts 缓存样本；" + r["license"]) for r in sample_service.catalog()]


@router.post("/audits", response_model=AuditTask, status_code=201)
async def create_audit(
    request: Request,
    source: Literal["upload", "sample"] = Form("upload"),
    files: list[UploadFile] | None = File(default=None),
    sample_id: str | None = Form(default=None),
    channel: str | None = Form(default=None, max_length=100),
    db=Depends(get_db),
):
    rate_limit(request, "create", 5, 60)
    identifier = str(uuid.uuid4())
    try:
        if source == "sample":
            if not sample_id or files:
                raise AppError(422, "INVALID_SOURCE", "样本任务需要 sample_id 且不能附带上传")
            prepared = [sample_service.prepare_sample(sample_id, identifier)]
        else:
            if sample_id or not files or not 1 <= len(files) <= 3:
                raise AppError(422, "INVALID_FILES", "请上传1至3张图片")
            if get_settings().ocr_mode == "cache":
                raise AppError(409, "CACHE_MODE", "缓存模式只接受预置样本")
            prepared = [await prepare_upload(file, identifier) for file in files]
        return commands.create_task(db, identifier, source, sample_id, channel, prepared)
    finally:
        for file in files or []:
            await file.close()


@router.get("/audits", response_model=AuditList)
def list_audits(limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0), db=Depends(get_db)):
    total = db.scalar(select(func.count()).select_from(Task))
    tasks = db.scalars(select(Task).order_by(Task.created_at.desc(), Task.id).offset(offset).limit(limit)).all()
    return AuditList(items=[task_schema(t) for t in tasks], total=total, limit=limit, offset=offset)


@router.get("/audits/{audit_id}", response_model=AuditTask)
def get_audit(audit_id: str, db=Depends(get_db)):
    return task_schema(get_task(db, audit_id))


@router.get("/audits/{audit_id}/files/{file_id}",
            responses={200: {"content": {"image/png": {}, "image/jpeg": {}}}})
def get_file(audit_id: str, file_id: str, db=Depends(get_db)):
    get_task(db, audit_id)
    file = db.get(StoredFile, file_id)
    if not file or file.task_id != audit_id or not file_path(file).is_file():
        raise AppError(404, "FILE_NOT_FOUND", "文件不存在")
    return FileResponse(file_path(file), media_type=file.mime,
                        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})


@router.get("/audits/{audit_id}/fields", response_model=FieldsResponse)
def get_fields(audit_id: str, version: int | None = Query(None, ge=0), db=Depends(get_db)):
    task = get_task(db, audit_id)
    target = task.document_version if version is None else version
    fields = project_fields(current_document(db, task, target), target)
    history = db.scalars(select(Document).where(Document.task_id == audit_id, Document.version <= target)
                         .order_by(Document.version)).all()
    previous, changed = {}, {}
    for row in history:
        for field in project_fields(json.loads(row.doc_json), row.version):
            data = field.model_dump(exclude={"version"})
            if previous.get(field.field_id) != data:
                changed[field.field_id] = row.version
            previous[field.field_id] = data
    for field in fields:
        field.version = changed.get(field.field_id, target)
    return FieldsResponse(document_version=target, fields=fields)


@router.get("/audits/{audit_id}/review-items", response_model=ReviewItemsResponse)
def get_review_items(audit_id: str, db=Depends(get_db)):
    task = get_task(db, audit_id)
    result = project_review_items(current_document(db, task), task.document_version)
    if db.scalar(select(Document.id).where(Document.task_id == audit_id, Document.created_by == "vision").limit(1)):
        result["ocr_fields"] = project_fields(current_document(db, task, 0), 0)
    for image in result["images"]:
        file = db.get(StoredFile, image["id"])
        image["sha256"] = file.sha256
        image["display_name"] = file.display_name
    return result


@router.post("/audits/{audit_id}/corrections", response_model=AuditTask)
def submit_corrections(audit_id: str, body: CorrectionsRequest, db=Depends(get_db)):
    return commands.correct_task(db, audit_id, body)


@router.post("/audits/{audit_id}/resume", response_model=JobReceipt)
def resume_audit(audit_id: str, db=Depends(get_db)):
    return commands.resume_task(db, audit_id)


@router.get("/audits/{audit_id}/issues", response_model=list[AuditIssue])
def get_issues(audit_id: str, status: Literal["PASS", "FAIL", "REVIEW", "NOT_CHECKED"] | None = None,
               layer: Literal["L1", "L2", "L3"] | None = None, rule_id: str | None = None, db=Depends(get_db)):
    result = issues_for_task(db, get_task(db, audit_id))
    return [i for i in result if (status is None or i.status == status) and
            (layer is None or i.layer == layer) and (rule_id is None or i.rule_id == rule_id)]


@router.put("/audits/{audit_id}/issues/{issue_id}/decision", response_model=AuditIssue)
def set_issue_decision(
    audit_id: str, issue_id: str, body: IssueDecisionRequest, db=Depends(get_db),
):
    return commands.decide_issue(db, audit_id, issue_id, body)


@router.get("/audits/{audit_id}/report", response_model=AuditReport,
            responses={200: {"content": {"text/html": {"schema": {"type": "string"}}}}},
            description="JSON 报告；format=html 时返回可打印的独立 HTML 页面。")
def get_report(audit_id: str, format: Literal["json", "html"] = "json", db=Depends(get_db)):
    task = get_task(db, audit_id)
    report = report_json(db, task)
    if format == "html":
        return HTMLResponse(report_html(report, task), headers={"Cache-Control": "no-store",
            "Content-Security-Policy": "default-src 'none'; img-src data:; style-src 'unsafe-inline'"})
    return report


@router.post("/audits/{audit_id}/retry", response_model=JobReceipt)
def retry_audit(audit_id: str, db=Depends(get_db)):
    return commands.retry_task(db, audit_id)

"""D0 route contract. Business routes return 501 until their services are built."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from fastapi.responses import JSONResponse, Response
from fastapi.security import APIKeyCookie

from app.schemas.contract import (
    AuditIssue,
    AuditList,
    AuditReport,
    AuditTask,
    CorrectionsRequest,
    ErrorResponse,
    FieldsResponse,
    IssueDecisionRequest,
    JobReceipt,
    LoginRequest,
    LoginResponse,
    ReviewItemsResponse,
    SampleSummary,
    StandardOption,
)


session_cookie = APIKeyCookie(name="session", auto_error=False)
public_router = APIRouter(
    prefix="/api", responses={422: {"model": ErrorResponse}, 501: {"model": ErrorResponse}},
)
router = APIRouter(
    prefix="/api",
    dependencies=[Depends(session_cookie)],
    responses={
        code: {"model": ErrorResponse}
        for code in (401, 404, 409, 413, 415, 422, 429, 501)
    },
)


def unavailable() -> JSONResponse:
    return JSONResponse(
        status_code=501,
        content={"error": {"code": "NOT_IMPLEMENTED", "message": "接口正在开发中"}},
    )


@public_router.post("/auth/login", response_model=LoginResponse)
def login(body: LoginRequest) -> Response:
    return unavailable()


@router.get("/standards", response_model=list[StandardOption])
def standards() -> Response:
    return unavailable()


@router.get("/samples", response_model=list[SampleSummary])
def samples() -> Response:
    return unavailable()


@router.post("/audits", response_model=AuditTask, status_code=201)
def create_audit(
    source: Literal["upload", "sample"] = Form("upload"),
    files: list[UploadFile] | None = File(default=None),
    sample_id: str | None = Form(default=None),
) -> Response:
    return unavailable()


@router.get("/audits", response_model=AuditList)
def list_audits(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> Response:
    return unavailable()


@router.get("/audits/{audit_id}", response_model=AuditTask)
def get_audit(audit_id: str) -> Response:
    return unavailable()


@router.get(
    "/audits/{audit_id}/files/{file_id}",
    responses={200: {"content": {"image/png": {}, "image/jpeg": {}}}},
)
def get_file(audit_id: str, file_id: str) -> Response:
    return unavailable()


@router.get("/audits/{audit_id}/fields", response_model=FieldsResponse)
def get_fields(audit_id: str, version: int | None = Query(default=None, ge=0)) -> Response:
    return unavailable()


@router.get("/audits/{audit_id}/review-items", response_model=ReviewItemsResponse)
def get_review_items(audit_id: str) -> Response:
    return unavailable()


@router.post("/audits/{audit_id}/corrections", response_model=AuditTask)
def submit_corrections(audit_id: str, body: CorrectionsRequest) -> Response:
    return unavailable()


@router.post("/audits/{audit_id}/resume", response_model=JobReceipt)
def resume_audit(audit_id: str) -> Response:
    return unavailable()


@router.get("/audits/{audit_id}/issues", response_model=list[AuditIssue])
def get_issues(
    audit_id: str,
    status: Literal["PASS", "FAIL", "REVIEW", "NOT_CHECKED"] | None = None,
    layer: Literal["L1", "L2", "L3"] | None = None,
    rule_id: str | None = None,
) -> Response:
    return unavailable()


@router.put("/audits/{audit_id}/issues/{issue_id}/decision", response_model=AuditIssue)
def set_issue_decision(
    audit_id: str, issue_id: str, body: IssueDecisionRequest,
) -> Response:
    return unavailable()


@router.get(
    "/audits/{audit_id}/report",
    response_model=AuditReport,
    responses={200: {"content": {"text/html": {"schema": {"type": "string"}}}}},
    description="JSON 报告；format=html 时返回可打印的 HTML 页面。",
)
def get_report(
    audit_id: str, format: Literal["json", "html"] = "json",
) -> Response:
    return unavailable()


@router.post("/audits/{audit_id}/retry", response_model=JobReceipt)
def retry_audit(audit_id: str) -> Response:
    return unavailable()

"""D0 route contract. Business routes return 501 until their services are built."""

from __future__ import annotations

import hashlib
import hmac
import time
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from fastapi.responses import JSONResponse, Response
from fastapi.security import APIKeyCookie

from app.config import Settings, get_settings
from app.errors import AppError
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
SESSION_TTL_SECONDS = 12 * 60 * 60


def make_session(settings: Settings) -> str:
    expires = str(int(time.time()) + SESSION_TTL_SECONDS)
    signature = hmac.new(
        settings.session_secret.encode(), expires.encode(), hashlib.sha256,
    ).hexdigest()
    return f"{expires}.{signature}"


def require_session(
    token: str | None = Depends(session_cookie),
    settings: Settings = Depends(get_settings),
) -> None:
    try:
        expires, signature = (token or "").split(".", 1)
        expected = hmac.new(
            settings.session_secret.encode(), expires.encode(), hashlib.sha256,
        ).hexdigest()
        valid = int(expires) >= time.time() and hmac.compare_digest(signature, expected)
    except ValueError:
        valid = False
    if not valid:
        raise AppError(401, "UNAUTHORIZED", "请先登录")


public_router = APIRouter(
    prefix="/api", responses={422: {"model": ErrorResponse}},
)
router = APIRouter(
    prefix="/api",
    dependencies=[Depends(require_session)],
    responses={
        code: {"model": ErrorResponse}
        for code in (401, 404, 409, 413, 415, 422, 429, 501)
    },
)


def unavailable() -> None:
    raise AppError(501, "NOT_IMPLEMENTED", "接口正在开发中")


@public_router.post(
    "/auth/login",
    response_model=LoginResponse,
    responses={401: {"model": ErrorResponse}},
)
def login(
    body: LoginRequest,
    request: Request,
    settings: Settings = Depends(get_settings),
) -> Response:
    if not hmac.compare_digest(body.passcode, settings.access_passcode):
        raise AppError(401, "INVALID_CREDENTIALS", "口令无效")

    response = JSONResponse(content={"authenticated": True})
    response.set_cookie(
        "session",
        make_session(settings),
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        secure=request.url.scheme == "https",
        samesite="lax",
        path="/",
    )
    return response


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

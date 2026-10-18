"""Public API models for the V1 audit workflow.

The OCR LabelDocument remains the source of truth. These models describe only
the HTTP projection and request/response contract.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


TaskStatus = Literal[
    "QUEUED", "EXTRACTING", "WAITING_REVIEW", "CHECKING",
    "COMPLETED", "PARTIAL", "FAILED",
]
IssueStatus = Literal["PASS", "FAIL", "REVIEW", "NOT_CHECKED"]
FieldStatus = Literal["ready", "needs_review", "missing", "human_confirmed"]
SourceType = Literal["baidu_accurate", "baidu_table_v2", "manual", "codex_vision"]


class TaskError(BaseModel):
    code: str
    message: str
    retryable: bool


class ErrorBody(BaseModel):
    code: str
    message: str
    review_pending: int | None = None


class ErrorResponse(BaseModel):
    error: ErrorBody


class BoxPx(BaseModel):
    left: float = Field(ge=0)
    top: float = Field(ge=0)
    width: float = Field(gt=0)
    height: float = Field(gt=0)


class Evidence(BaseModel):
    id: str
    image_id: str | None
    source_type: SourceType
    text: str
    box_px: BoxPx | None
    ocr_confidence: float | None = Field(default=None, ge=0, le=1)


class AuditFile(BaseModel):
    id: str
    display_name: str
    width_px: int = Field(gt=0)
    height_px: int = Field(gt=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    url: str


class AuditTask(BaseModel):
    id: str
    status: TaskStatus
    standard_mode: Literal["gb28050_2025_precheck"]
    channel: str | None
    source: Literal["upload", "sample"]
    files: list[AuditFile]
    document_version: int = Field(ge=0)
    check_doc_version: int | None = Field(default=None, ge=0)
    stale: bool
    review_pending: int = Field(ge=0)
    summary: dict[str, int] | None
    error: TaskError | None
    created_at: datetime
    updated_at: datetime


class AuditField(BaseModel):
    field_id: str
    group: Literal["basic", "nutrition"]
    label: str
    raw: str | None
    value: str | None
    unit: str | None
    status: FieldStatus
    review_reasons: list[str]
    evidence: list[Evidence]
    version: int = Field(ge=0)


class Regulation(BaseModel):
    standard: str
    edition: str
    clause_id: str | None
    verification_status: Literal["verified", "pending", "course_requirement"]
    note: str | None


class AuditIssue(BaseModel):
    issue_id: str
    rule_id: str
    layer: Literal["L1", "L2", "L3"]
    status: IssueStatus
    title: str
    message: str
    evidence: list[Evidence]
    calculation: dict[str, Any] | None
    regulation: Regulation
    suggestion: str | None
    doc_version: int = Field(ge=0)
    review_decision: Literal["open", "confirmed", "false_positive"] = "open"


class LoginRequest(BaseModel):
    passcode: str


class LoginResponse(BaseModel):
    authenticated: bool


class HealthResponse(BaseModel):
    status: Literal["ok"]
    ocr_configured: bool


class StandardOption(BaseModel):
    mode: Literal["gb28050_2025_precheck"]
    label: str
    coverage: str


class SampleAttribution(BaseModel):
    source_url: str
    image_url: str
    license: str


class SampleSummary(SampleAttribution):
    id: str
    label: str
    description: str


class AuditList(BaseModel):
    items: list[AuditTask]
    total: int = Field(ge=0)
    limit: int = Field(gt=0)
    offset: int = Field(ge=0)


class FieldsResponse(BaseModel):
    document_version: int = Field(ge=0)
    fields: list[AuditField]


class ReviewItem(BaseModel):
    path: str
    raw: str | None
    value: str | None
    unit: str | None
    review_reasons: list[str]
    evidence: list[Evidence]


class TableReviewItem(BaseModel):
    table_id: str | None
    code: str
    message: str
    image_id: str | None
    box_px: BoxPx | None
    evidence_refs: list[str]


class ReviewItemsResponse(BaseModel):
    document_version: int = Field(ge=0)
    items: list[ReviewItem]
    table_items: list[TableReviewItem]
    document_id: str
    product_id: str | None
    images: list[dict[str, Any]]
    threshold: float = 0.96
    ocr_fields: list[AuditField] = Field(default_factory=list)


class FieldDecision(BaseModel):
    path: str
    action: Literal["confirm", "correct"]
    original_raw: str | None = Field(alias="originalRaw")
    original_value: str | None = Field(alias="originalValue")
    original_unit: str | None = Field(alias="originalUnit")
    source_evidence_refs: list[str] = Field(alias="sourceEvidenceRefs")
    manual_text: str = Field(alias="manualText", min_length=1)
    corrected_value: str = Field(alias="correctedValue", min_length=1)
    corrected_unit: str | None = Field(alias="correctedUnit")
    note: str | None = None


class TableDecision(BaseModel):
    table_id: str = Field(alias="tableId")
    original_code: str = Field(alias="originalCode")
    source_evidence_refs: list[str] = Field(alias="sourceEvidenceRefs")
    action: Literal["confirm_complete"]


class CorrectionsRequest(BaseModel):
    base_version: int = Field(ge=0)
    image_id: str
    review_schema_version: Literal["1.0"] = Field(alias="reviewSchemaVersion")
    document_id: str = Field(alias="documentId")
    product_id: str | None = Field(alias="productId")
    image_name: str = Field(alias="imageName")
    image_sha256: str = Field(alias="imageSha256", pattern=r"^[0-9a-f]{64}$")
    threshold: float = Field(ge=0, le=1)
    reviewer: str = Field(min_length=1)
    reviewed_at: datetime | None = Field(default=None, alias="reviewedAt")
    decisions: list[FieldDecision]
    table_decisions: list[TableDecision] = Field(default_factory=list, alias="tableDecisions")
    table_checked_all: bool = False
    manual_tables: list[ManualTable] = Field(default_factory=list)


class ManualRow(BaseModel):
    nutrient: str = Field(min_length=1, max_length=100)
    amount: str = Field(pattern=r"^\d+(?:\.\d+)?$")
    unit: str = Field(min_length=1, max_length=20)
    nrv_percent: str | None = Field(default=None, pattern=r"^\d+(?:\.\d+)?$")


class ManualTable(BaseModel):
    table_id: str = Field(min_length=1, max_length=100)
    original_code: str
    source_evidence_refs: list[str]
    basis: Literal["per_100g", "per_100ml", "per_serving"]
    basis_raw: str = Field(min_length=1, max_length=200)
    serving_g: str | None = Field(default=None, pattern=r"^\d+(?:\.\d+)?$")
    box_px: BoxPx | None = None
    rows: list[ManualRow] = Field(min_length=1, max_length=100)


CorrectionsRequest.model_rebuild()


class JobReceipt(BaseModel):
    job_id: str
    task_id: str
    kind: Literal["extract", "check"]
    status: Literal["queued", "running", "done", "failed"]


class IssueDecisionRequest(BaseModel):
    decision: Literal["confirmed", "false_positive"]
    note: str | None = None


class AuditReport(BaseModel):
    task: AuditTask
    policy_version: str
    scope: str
    fields: list[AuditField]
    issues: list[AuditIssue]
    unchecked: list[AuditIssue]
    confirmed_fields: list[str]
    corrections: list[dict[str, Any]] = Field(default_factory=list)
    sample_attribution: SampleAttribution | None = None

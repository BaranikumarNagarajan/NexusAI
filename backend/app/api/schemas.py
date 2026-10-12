"""Response models for the NexusAI API (also drive the OpenAPI docs at /docs)."""

from typing import Any, Literal

from pydantic import BaseModel, Field


class ErrorResponse(BaseModel):
    detail: str


class Persistence(BaseModel):
    status: str = Field(description="saved | failed | disabled")
    review_id: str | None = None
    detail: str | None = None
    reasoning_saved: bool | None = Field(None, description="Agent A's action + reasoning stored on the row")
    reasoning_detail: str | None = None


class ReviewSubmission(BaseModel):
    """Result of submitting an invoice for review (client -> API -> Agent A -> Agent B)."""

    task_id: str = Field(description="Agent A (Procurement Coordinator) task ID")
    task_state: str = Field(description="Agent A final task state, e.g. TASK_STATE_COMPLETED")
    recommended_action: str = Field(
        description="ROUTE_TO_APPROVER | HOLD_AND_QUERY_SUPPLIER | REQUEST_MISSING_INFORMATION | CORRECT_AND_RESUBMIT"
    )
    verification_result: str = Field(description="MATCH | DISCREPANCY_FOUND | NEEDS_REVIEW | INVALID_REQUEST")
    invoice_number: str | None = None
    po_number: str | None = None
    summary: str
    action_items: list[str]
    verifier_task_id: str = Field(description="Agent B task ID; use it with GET /api/reviews/{task_id}")
    persistence: Persistence
    verification: dict[str, Any] = Field(description="Agent B's full verification_result (findings, assumptions)")
    reasoning: dict[str, Any] | None = Field(
        None, description="Agent A's explanation: source (llm | built_in), model, explanation, risk_level, "
                          "risk_points, supplier_email, fallback_reason")
    disclaimer: str


class ReviewSummary(BaseModel):
    id: str
    task_id: str = Field(description="Agent B task ID")
    task_state: str
    verification_status: str
    invoice_number: str | None = None
    po_number: str | None = None
    supplier_name: str | None = None
    currency: str | None = None
    invoice_total: str | None = Field(None, description="Exact decimal as a string, e.g. '880.00'")
    summary: str
    created_at: str


class ReviewDetail(ReviewSummary):
    context_id: str | None = None
    po_reference: str | None = None
    findings: list[dict[str, Any]]
    validation_errors: list[dict[str, Any]]
    assumptions: list[str]
    recommended_action: str | None = None
    risk_level: str | None = None
    reasoning_source: str | None = None
    reasoning: dict[str, Any] | None = None


class ReviewPage(BaseModel):
    items: list[ReviewSummary]
    total: int
    limit: int
    offset: int


class ReviewStats(BaseModel):
    total: int
    by_status: dict[str, int]


class SkillInfo(BaseModel):
    id: str
    name: str
    description: str


class AgentInfo(BaseModel):
    role: Literal["coordinator", "verifier"]
    url: str
    online: bool
    name: str | None = None
    version: str | None = None
    skills: list[SkillInfo] = []
    error: str | None = None


class Health(BaseModel):
    status: Literal["ok", "degraded"]
    api: Literal["ok"] = "ok"
    coordinator: Literal["online", "offline"]
    verifier: Literal["online", "offline"]
    storage: Literal["connected", "not_configured", "error"]
    ocr: Literal["available", "not_installed"] = "not_installed"
    reasoning: Literal["llm", "built_in", "unknown"] = "unknown"
    reasoning_model: str | None = None
    reasoning_provider: str | None = None
    reasoning_detail: str | None = None


class DailyCount(BaseModel):
    date: str
    counts: dict[str, int]


class IssueCount(BaseModel):
    check: str
    mismatch: int
    missing: int


class SupplierStats(BaseModel):
    supplier: str
    reviews: int
    match: int
    issues: int


class Analytics(BaseModel):
    total: int
    by_status: dict[str, int]
    match_rate: float | None = Field(None, description="MATCH share of valid (non-INVALID_REQUEST) reviews, 0-1")
    daily: list[DailyCount]
    top_issues: list[IssueCount]
    suppliers: list[SupplierStats]
    value_by_currency: dict[str, str] = Field(description="Sum of invoice totals per currency (exact decimals)")
    sample_size: int
    by_risk: dict[str, int] = Field(default_factory=dict)
    by_action: dict[str, int] = Field(default_factory=dict)
    reasoning_columns: bool = Field(True, description="False until migration 002 is applied")
    latest: dict[str, Any] | None = Field(None, description="Most recent review incl. Agent A reasoning")


class ExtractedField(BaseModel):
    value: str | None
    confidence: Literal["high", "medium", "missing"]
    source: str | None = Field(None, description="The line of text the value was read from")


class DocumentExtraction(BaseModel):
    filename: str
    document_type: Literal["invoice", "purchase_order", "unknown"]
    pages: int
    fields: dict[str, ExtractedField]
    line_items: list[dict[str, str]]
    line_items_confidence: Literal["high", "medium", "missing"]
    warnings: list[str]
    method: Literal["text", "ocr"] = Field("text", description="'text' = PDF text layer, 'ocr' = Tesseract OCR")
    data: dict[str, Any] = Field(description="Fragment ready to place under 'invoice' or 'purchase_order'")


class SamplePdf(BaseModel):
    scenario: str
    title: str
    invoice_url: str
    purchase_order_url: str


class SampleInfo(BaseModel):
    name: str
    title: str
    expected_result: str

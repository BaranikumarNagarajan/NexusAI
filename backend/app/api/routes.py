"""API endpoints. Every agent call goes over A2A; every stored review comes from Supabase."""

import json
import logging
import os
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi import Path as PathParam
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import HTTPBearer

from app.agents.invoice_verifier.money import fmt_amount, minor_units
from app.api.gateway import AgentGateway, AgentUnavailableError
from app.api.schemas import (
    AgentInfo,
    Analytics,
    AuthStatus,
    DocumentExtraction,
    ErrorResponse,
    Health,
    LoginRequest,
    LoginResponse,
    Persistence,
    ReviewDetail,
    ReviewPage,
    ReviewStats,
    ReviewSubmission,
    ReviewSummary,
    SampleInfo,
    SamplePdf,
)
from app.api.security import request_token
from app.ingestion.kaggle_import import DATASETS, MAX_PER_CALL, KaggleError, folder, load_manifest, refresh
from app.ingestion.ocr import ocr_status
from app.ingestion.pdf_extract import ExtractionError, detect_kind, extract_document
from app.persistence.reviews import STATUSES, PersistenceError, ReviewRepository

logger = logging.getLogger(__name__)
# The access check itself is the security middleware; this only adds the "Authorize" button to /docs.
_bearer = HTTPBearer(auto_error=False, description="Token from POST /api/auth/login (only when ACCESS_PASSWORD is set)")
router = APIRouter(prefix="/api", dependencies=[Depends(_bearer)])

SAMPLES_DIR = Path(__file__).resolve().parents[2] / "samples"
SAMPLES = {
    "matching": ("Matching invoice and purchase order", "MATCH"),
    "supplier_mismatch": ("Supplier mismatch", "DISCREPANCY_FOUND"),
    "currency_mismatch": ("Currency mismatch", "DISCREPANCY_FOUND"),
    "quantity_total_mismatch": ("Quantity and total mismatch", "DISCREPANCY_FOUND"),
    "missing_information": ("Missing information", "NEEDS_REVIEW"),
    "invalid_request": ("Malformed request", "INVALID_REQUEST"),
}
_TASK_ID = Annotated[str, PathParam(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")]
SAMPLE_PDF_DIR = SAMPLES_DIR / "pdfs"
_DATASET_KEY = Annotated[str, Query(pattern=r"^[a-z]+$")]
MAX_UPLOAD_BYTES = 5 * 1024 * 1024


def _load_sample(name: str) -> dict[str, Any]:
    return json.loads((SAMPLES_DIR / "json" / f"{name}.json").read_text(encoding="utf-8"))


def _gateway(request: Request) -> AgentGateway:
    return request.app.state.gateway


def _repository(request: Request) -> ReviewRepository:
    repository = request.app.state.reviews
    if repository is None:
        raise HTTPException(503, "Review storage is not configured (set SUPABASE_URL and SUPABASE_SECRET_KEY).")
    return repository


async def _storage_call(func, *args, **kwargs):
    """Run a blocking Supabase call off the event loop; map failures to 502."""
    try:
        return await run_in_threadpool(func, *args, **kwargs)
    except PersistenceError as exc:
        logger.error("Review storage error: %s", exc)
        raise HTTPException(502, "Review storage is unavailable. Check the server log for details.") from exc


def _format_total(value: Any, currency: str | None) -> str | None:
    """Supabase returns numeric columns as JSON numbers (880.0); show them at currency precision."""
    if value is None:
        return None
    try:
        amount = Decimal(str(value))
    except InvalidOperation:
        return str(value)
    return fmt_amount(amount, minor_units(currency)[0])


def _summary(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "task_id": row["a2a_task_id"],
        "task_state": row["task_state"],
        "verification_status": row["verification_status"],
        "invoice_number": row.get("invoice_number"),
        "po_number": row.get("po_number"),
        "supplier_name": row.get("supplier_name"),
        "currency": row.get("currency"),
        "invoice_total": _format_total(row.get("invoice_total"), row.get("currency")),
        "summary": row["summary"],
        "created_at": str(row["created_at"]),
    }


# --- Status -------------------------------------------------------------------------------


@router.get("/health", response_model=Health, tags=["status"], summary="Health of the API, agents and storage")
async def health(request: Request) -> Health:
    agents = {a["role"]: a for a in await _gateway(request).agent_cards()}
    repository = request.app.state.reviews
    storage = "not_configured"
    if repository is not None:
        try:
            await run_in_threadpool(repository.check_ready)
            storage = "connected"
        except PersistenceError as exc:
            logger.error("Storage health check failed: %s", exc)
            storage = "error"
    coordinator = "online" if agents["coordinator"]["online"] else "offline"
    verifier = "online" if agents["verifier"]["online"] else "offline"
    healthy = coordinator == verifier == "online" and storage != "error"
    reasoning = (await _gateway(request).coordinator_info()).get("reasoning") or {} if coordinator == "online" else {}
    return Health(
        status="ok" if healthy else "degraded",
        coordinator=coordinator,
        verifier=verifier,
        storage=storage,
        ocr=ocr_status(),
        reasoning=reasoning.get("mode", "unknown"),
        reasoning_model=reasoning.get("model"),
        reasoning_provider=reasoning.get("provider"),
        reasoning_detail=reasoning.get("detail"),
        auth_required=request.app.state.guard.enabled,
    )


@router.post("/auth/login", response_model=LoginResponse, tags=["status"], summary="Exchange the access password for a token",
             responses={401: {"model": ErrorResponse}, 404: {"model": ErrorResponse}, 429: {"model": ErrorResponse}})
async def login(request: Request, body: LoginRequest) -> LoginResponse:
    guard = request.app.state.guard
    if not guard.enabled:
        raise HTTPException(404, "No access password is configured; the API is open.")
    if not guard.check_password(body.password):
        logger.warning("Failed sign-in attempt")
        raise HTTPException(401, "Wrong password.")
    token, expires_in = guard.issue()
    return LoginResponse(token=token, expires_in=expires_in)


@router.get("/auth/status", response_model=AuthStatus, tags=["status"], summary="Is a password needed, and is this token valid?")
async def auth_status(request: Request) -> AuthStatus:
    guard = request.app.state.guard
    return AuthStatus(auth_required=guard.enabled, signed_in=guard.verify(request_token(request)))


@router.get("/agents", response_model=list[AgentInfo], tags=["status"], summary="Discovered A2A agents")
async def agents(request: Request) -> list[dict]:
    """Fetches both Agent Cards (Agent A and Agent B) live from their discovery endpoints."""
    return await _gateway(request).agent_cards()


# --- Samples (demo data) -------------------------------------------------------------------


@router.get("/samples", response_model=list[SampleInfo], tags=["samples"], summary="Built-in synthetic scenarios")
async def list_samples() -> list[SampleInfo]:
    return [SampleInfo(name=n, title=t, expected_result=r) for n, (t, r) in SAMPLES.items()]


@router.get(
    "/samples/{name}",
    tags=["samples"],
    summary="Request payload for one scenario",
    responses={404: {"model": ErrorResponse}},
)
async def get_sample(name: str) -> dict[str, Any]:
    if name not in SAMPLES:  # whitelist: never build file paths from user input
        raise HTTPException(404, f"Unknown sample '{name}'.")
    return _load_sample(name)


@router.get("/samples/pdfs/list", response_model=list[SamplePdf], tags=["samples"], summary="Synthetic sample PDFs")
async def list_sample_pdfs() -> list[SamplePdf]:
    out = []
    for name, (title, _result) in SAMPLES.items():
        if (SAMPLE_PDF_DIR / f"{name}_invoice.pdf").is_file() and (SAMPLE_PDF_DIR / f"{name}_po.pdf").is_file():
            out.append(SamplePdf(scenario=name, title=title,
                                 invoice_url=f"/api/samples/pdfs/{name}_invoice.pdf",
                                 purchase_order_url=f"/api/samples/pdfs/{name}_po.pdf"))
    return out


@router.get(
    "/samples/pdfs/{filename}",
    tags=["samples"],
    summary="Download one synthetic sample PDF",
    response_class=FileResponse,
    responses={404: {"model": ErrorResponse}},
)
async def get_sample_pdf(filename: Annotated[str, PathParam(pattern=r"^[a-z_]+_(invoice|po)\.pdf$")]) -> FileResponse:
    scenario = filename.rsplit("_", 1)[0]
    path = SAMPLE_PDF_DIR / filename
    if scenario not in SAMPLES or not path.is_file():  # whitelist + existence; never trust the raw name
        raise HTTPException(404, f"Unknown sample PDF '{filename}'.")
    return FileResponse(path, media_type="application/pdf", filename=filename,
                        content_disposition_type="inline")


def _kaggle_files(key: str) -> list[dict[str, Any]]:
    manifest = load_manifest(key) or {}
    return [f for f in manifest.get("files", []) if (folder(key) / f["file"]).is_file()]


@router.get("/samples/kaggle/datasets", tags=["samples"], summary="The Kaggle invoice datasets you can load from")
async def list_kaggle_datasets() -> list[dict[str, Any]]:
    return [{"key": d.key, "title": d.title, "kind": d.kind, "description": d.description, "license": d.license,
             "labelled": d.labelled, "source": f"https://www.kaggle.com/datasets/{d.ref}",
             "loaded": len(_kaggle_files(d.key))} for d in DATASETS.values()]


@router.get("/samples/kaggle/list", tags=["samples"], summary="The current set of real invoices from one dataset",
            responses={404: {"model": ErrorResponse}})
async def list_kaggle_samples(dataset: _DATASET_KEY = "hq") -> dict[str, Any]:
    if dataset not in DATASETS:
        raise HTTPException(404, f"Unknown dataset '{dataset}'.")
    d = DATASETS[dataset]
    return {
        "dataset": d.key, "title": d.title, "kind": d.kind, "labelled": d.labelled,
        "source": f"https://www.kaggle.com/datasets/{d.ref}", "license": d.license, "attribution": d.attribution,
        "configured": bool(os.getenv("KAGGLE_API_KEY") or os.getenv("KAGGLE_API_TOKEN")),
        "files": [{"file": f["file"], "invoice_number": f.get("invoice_number"), "seller": f.get("seller"),
                   "total": f.get("total"), "items": f.get("items"),
                   "url": f"/api/samples/kaggle/{d.key}/{f['file']}"} for f in _kaggle_files(d.key)],
    }


@router.post("/samples/kaggle/refresh", tags=["samples"], summary="Load a fresh random set of invoices from Kaggle",
             responses={400: {"model": ErrorResponse}, 502: {"model": ErrorResponse}})
async def refresh_kaggle(dataset: _DATASET_KEY = "hq",
                         count: Annotated[int, Query(ge=1, le=MAX_PER_CALL)] = 6) -> dict[str, Any]:
    """Downloads `count` random invoices from the chosen dataset with KAGGLE_API_KEY (server-side; the token
    never reaches the browser) and replaces that dataset's previous set. Only those files are fetched."""
    try:
        result = await run_in_threadpool(refresh, dataset, count)
    except KaggleError as exc:
        status = 400 if "Set KAGGLE" in str(exc) or "Unknown dataset" in str(exc) else 502
        raise HTTPException(status, str(exc)) from exc
    logger.info("Loaded %d invoices from Kaggle dataset %s", result["added"], dataset)
    return result


@router.get("/samples/kaggle/{dataset}/{filename}", tags=["samples"], summary="One imported Kaggle invoice image",
            response_class=FileResponse, responses={404: {"model": ErrorResponse}})
async def get_kaggle_sample(dataset: Annotated[str, PathParam(pattern=r"^[a-z]+$")],
                            filename: Annotated[str, PathParam(pattern=r"^[\w.-]+\.(jpg|jpeg|png)$")]) -> FileResponse:
    if dataset not in DATASETS or filename not in {f["file"] for f in _kaggle_files(dataset)}:
        raise HTTPException(404, f"Unknown Kaggle sample '{filename}'.")  # whitelist via the manifest
    media = "image/png" if filename.endswith(".png") else "image/jpeg"
    return FileResponse(folder(dataset) / filename, media_type=media, content_disposition_type="inline")


# --- Documents -----------------------------------------------------------------------------------


@router.post(
    "/documents/extract",
    response_model=DocumentExtraction,
    tags=["documents"],
    summary="Extract fields from an invoice or purchase-order PDF or image",
    responses={413: {"model": ErrorResponse}, 415: {"model": ErrorResponse}, 422: {"model": ErrorResponse}},
)
async def extract_document_route(
    file: Annotated[UploadFile, File(description="PDF (text or scanned) or PNG/JPEG/TIFF image, max 5 MB")],
    expected_type: Annotated[str | None, Form(pattern=r"^(invoice|purchase_order)$")] = None,
) -> DocumentExtraction:
    """Reads a PDF's text layer, or uses **OCR** (Tesseract) for scanned PDFs and images, and returns
    the fields it found, each with a confidence level (OCR values are never above `medium`).

    Nothing is submitted for verification: the caller reviews and corrects the values first,
    then sends them with `POST /api/reviews`.
    """
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    await file.close()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "The PDF is larger than 5 MB.")
    if not data:
        raise HTTPException(422, "The uploaded file is empty.")
    if detect_kind(data) is None:
        raise HTTPException(415, "Unsupported file type. Upload a PDF, PNG, JPEG or TIFF.")
    try:
        extraction = await run_in_threadpool(extract_document, data, expected_type)
    except ExtractionError as exc:
        raise HTTPException(422, str(exc)) from exc
    safe_name = Path(file.filename or "document.pdf").name[:120]
    logger.info("Extracted %s (%s via %s, %d line items)", safe_name, extraction.document_type, extraction.method,
                len(extraction.line_items))
    return DocumentExtraction(filename=safe_name, **extraction.to_dict())


# --- Reviews -----------------------------------------------------------------------------------


_SUBMIT_EXAMPLES = {
    name: {"summary": title, "value": _load_sample(name)} for name, (title, _r) in SAMPLES.items()
}


@router.post(
    "/reviews",
    response_model=ReviewSubmission,
    tags=["reviews"],
    summary="Submit an invoice and purchase order for review",
    responses={
        422: {"model": ReviewSubmission, "description": "Request rejected as malformed (INVALID_REQUEST)"},
        502: {"model": ErrorResponse, "description": "An agent failed (e.g. Agent B unavailable)"},
        503: {"model": ErrorResponse, "description": "Agent A unavailable"},
    },
)
async def submit_review(
    request: Request,
    payload: Annotated[dict[str, Any], Body(openapi_examples=_SUBMIT_EXAMPLES)],
) -> Any:
    """Sends the request to **Agent A** over A2A; Agent A delegates verification to **Agent B** over A2A.

    Returns Agent A's recommended next step together with Agent B's full findings.
    NexusAI never approves invoices for payment.
    """
    try:
        outcome = await _gateway(request).submit_review(payload)
    except AgentUnavailableError as exc:
        logger.error("Review submission failed: %s", exc)
        raise HTTPException(503, str(exc)) from exc

    if outcome.review is None:
        # Agent A failed (e.g. Agent B down) or rejected the request without a review.
        code = 422 if outcome.state == "TASK_STATE_REJECTED" else 502
        raise HTTPException(code, outcome.message or f"Agent A task ended in {outcome.state}.")

    review = outcome.review
    delegation = review.get("delegation", {})
    body = ReviewSubmission(
        task_id=outcome.task.id,
        task_state=outcome.state,
        recommended_action=review["recommended_action"],
        verification_result=review["verification_result"],
        invoice_number=review.get("invoice_number"),
        po_number=review.get("po_number"),
        summary=review["summary"],
        action_items=review.get("action_items", []),
        verifier_task_id=delegation.get("task_id", ""),
        persistence=delegation.get("persistence") or {"status": "unknown"},
        verification=review.get("verification", {}),
        reasoning=review.get("reasoning"),
        disclaimer=review["disclaimer"],
    )
    body.persistence = await _save_coordinator_outcome(request, body, review)
    status_code = 422 if outcome.state == "TASK_STATE_REJECTED" else 200
    return JSONResponse(body.model_dump(mode="json"), status_code=status_code)


async def _save_coordinator_outcome(request: Request, body: ReviewSubmission, review: dict) -> Persistence:
    """Store Agent A's action + reasoning on Agent B's saved row so the dashboard can show it."""
    persistence = body.persistence
    repository = request.app.state.reviews
    if repository is None or persistence.status != "saved" or not body.verifier_task_id:
        return persistence
    reasoning = review.get("reasoning") or {}
    outcome = {
        "coordinator_task_id": body.task_id,
        "recommended_action": body.recommended_action,
        "risk_level": reasoning.get("risk_level"),
        "reasoning_source": reasoning.get("source"),
        "reasoning": {k: reasoning.get(k) for k in
                      ("explanation", "risk_points", "likely_causes", "next_steps", "supplier_email", "model",
                       "provider", "fallback_reason", "financial_impact", "supplier_history", "fact_check", "trace")}
        if reasoning else None,
    }
    try:
        await run_in_threadpool(repository.save_coordinator_outcome, body.verifier_task_id, outcome)
        persistence.reasoning_saved = True
    except PersistenceError as exc:
        logger.warning("Agent A outcome not saved for %s: %s", body.verifier_task_id, exc)
        persistence.reasoning_saved = False
        persistence.reasoning_detail = str(exc)
    return persistence


@router.get(
    "/reviews",
    response_model=ReviewPage,
    tags=["reviews"],
    summary="Saved reviews, newest first",
    responses={502: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
)
async def list_reviews(
    request: Request,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
    status: Annotated[str | None, Query(description=f"Filter: {' | '.join(STATUSES)}")] = None,
) -> ReviewPage:
    if status is not None and status not in STATUSES:
        raise HTTPException(422, f"status must be one of: {', '.join(STATUSES)}")
    rows, total = await _storage_call(_repository(request).list_reviews, limit=limit, offset=offset, status=status)
    return ReviewPage(items=[ReviewSummary(**_summary(r)) for r in rows], total=total, limit=limit, offset=offset)


@router.get(
    "/reviews/{task_id}",
    response_model=ReviewDetail,
    tags=["reviews"],
    summary="One saved review by Agent B task ID",
    responses={404: {"model": ErrorResponse}, 502: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
)
async def get_review(request: Request, task_id: _TASK_ID) -> ReviewDetail:
    row = await _storage_call(_repository(request).get_by_task_id, task_id)
    if row is None:
        raise HTTPException(404, f"No saved review for task '{task_id}'.")
    return ReviewDetail(
        **_summary(row),
        context_id=row.get("a2a_context_id"),
        po_reference=row.get("po_reference"),
        findings=row.get("findings") or [],
        validation_errors=row.get("validation_errors") or [],
        assumptions=row.get("assumptions") or [],
        recommended_action=row.get("recommended_action"),
        risk_level=row.get("risk_level"),
        reasoning_source=row.get("reasoning_source"),
        reasoning=row.get("reasoning"),
    )


@router.get(
    "/stats",
    response_model=ReviewStats,
    tags=["reviews"],
    summary="Number of saved reviews per result",
    responses={502: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
)
async def stats(request: Request) -> ReviewStats:
    counts = await _storage_call(_repository(request).count_by_status)
    return ReviewStats(total=sum(counts.values()), by_status=counts)


@router.get(
    "/analytics",
    response_model=Analytics,
    tags=["reviews"],
    summary="Dashboard aggregates over recent saved reviews",
    responses={502: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
)
async def analytics(request: Request, days: Annotated[int, Query(ge=1, le=90)] = 14) -> Analytics:
    repository = _repository(request)
    rows, has_reasoning = await _storage_call(repository.analytics_rows, 1000)
    latest = await _storage_call(repository.latest)
    data = _aggregate(rows, days)
    data["reasoning_columns"] = has_reasoning
    data["latest"] = _latest(latest) if latest else None
    return Analytics(**data)


def _latest(row: dict[str, Any]) -> dict[str, Any]:
    return {**_summary(row), "recommended_action": row.get("recommended_action"),
            "risk_level": row.get("risk_level"), "reasoning_source": row.get("reasoning_source"),
            "reasoning": row.get("reasoning")}


def _aggregate(rows: list[dict[str, Any]], days: int) -> dict[str, Any]:
    from collections import Counter, defaultdict
    from datetime import date, datetime, timedelta

    by_status = Counter({s: 0 for s in STATUSES})
    today = date.today()
    window = [today - timedelta(days=offset) for offset in range(days - 1, -1, -1)]
    daily = {d.isoformat(): Counter({s: 0 for s in STATUSES}) for d in window}
    issues: dict[str, Counter] = defaultdict(Counter)
    suppliers: dict[str, Counter] = defaultdict(Counter)
    value: dict[str, Decimal] = defaultdict(Decimal)

    for row in rows:
        status = row.get("verification_status")
        by_status[status] += 1
        try:
            day = datetime.fromisoformat(str(row["created_at"]).replace("Z", "+00:00")).date().isoformat()
        except (KeyError, ValueError):
            day = None
        if day in daily:
            daily[day][status] += 1
        for finding in row.get("findings") or []:
            outcome = finding.get("outcome")
            if outcome in ("MISMATCH", "MISSING_INFORMATION"):
                issues[finding.get("check", "other")]["mismatch" if outcome == "MISMATCH" else "missing"] += 1
        if row.get("supplier_name") and status != "INVALID_REQUEST":
            entry = suppliers[row["supplier_name"]]
            entry["reviews"] += 1
            entry["match"] += status == "MATCH"
            entry["issues"] += status in ("DISCREPANCY_FOUND", "NEEDS_REVIEW")
        if row.get("invoice_total") is not None and row.get("currency"):
            try:
                value[row["currency"]] += Decimal(str(row["invoice_total"]))
            except InvalidOperation:
                pass

    by_risk = Counter(r.get("risk_level") for r in rows if r.get("risk_level"))
    by_action = Counter(r.get("recommended_action") for r in rows if r.get("recommended_action"))
    valid = sum(by_status.values()) - by_status["INVALID_REQUEST"]
    top = sorted(issues.items(), key=lambda kv: -(kv[1]["mismatch"] + kv[1]["missing"]))[:8]
    return {
        "total": len(rows),
        "by_status": dict(by_status),
        "match_rate": round(by_status["MATCH"] / valid, 4) if valid else None,
        "daily": [{"date": d, "counts": dict(c)} for d, c in daily.items()],
        "top_issues": [{"check": k, "mismatch": v["mismatch"], "missing": v["missing"]} for k, v in top],
        "suppliers": sorted(
            ({"supplier": k, "reviews": v["reviews"], "match": v["match"], "issues": v["issues"]} for k, v in suppliers.items()),
            key=lambda x: -x["reviews"],
        )[:8],
        "value_by_currency": {cur: fmt_amount(total, minor_units(cur)[0]) for cur, total in sorted(value.items())},
        "by_risk": {k: by_risk.get(k, 0) for k in ("low", "medium", "high")},
        "by_action": dict(by_action),
        "sample_size": len(rows),
    }

"""Verification-review persistence.

* :func:`build_review_row` maps a :class:`VerificationResult` (plus the original
  request payload, for invoice metadata) to a ``verification_reviews`` row.
* :class:`ReviewRepository` reads and writes those rows through a Supabase
  client. It knows nothing about A2A or the verification rules.

The A2A task ID is the idempotency key: saving the same task twice updates the
one existing row (``INSERT ... ON CONFLICT (a2a_task_id) DO UPDATE``) instead of
creating a duplicate.
"""

import logging
from typing import Any

import httpx
from postgrest.exceptions import APIError
from postgrest.types import CountMethod
from pydantic import ValidationError

from app.config import SupabaseSettings
from app.models.invoice import OverallResult, VerificationRequest, VerificationResult

logger = logging.getLogger(__name__)

TABLE = "verification_reviews"
IDEMPOTENCY_COLUMN = "a2a_task_id"
MIGRATION_FILE = "supabase/migrations/001_create_verification_reviews.sql"
STATUSES = ("MATCH", "DISCREPANCY_FOUND", "NEEDS_REVIEW", "INVALID_REQUEST")
# Columns returned by list views (findings and other large JSON are fetched per review).
LIST_COLUMNS = (
    "id,a2a_task_id,task_state,verification_status,invoice_number,po_number,"
    "supplier_name,currency,invoice_total,summary,created_at"
)

# PostgREST / Postgres codes meaning "the table does not exist yet".
_MISSING_TABLE_CODES = {"PGRST205", "42P01"}
# ... and "a column does not exist yet" (migration 002 not applied).
_MISSING_COLUMN_CODES = {"PGRST204", "42703"}
MIGRATION_002 = "supabase/migrations/002_add_coordinator_reasoning.sql"
ANALYTICS_COLUMNS = "created_at,verification_status,supplier_name,currency,invoice_total,findings"
ANALYTICS_COLUMNS_V2 = ANALYTICS_COLUMNS + ",recommended_action,risk_level"


class MissingColumnsError(Exception):
    """Migration 002 has not been applied yet."""


class PersistenceError(Exception):
    """A review could not be written or read. Messages never contain credentials."""


def task_state_for(result: VerificationResult) -> str:
    """The final A2A task state the executor publishes for this result."""
    return "REJECTED" if result.result is OverallResult.INVALID_REQUEST else "COMPLETED"


def build_review_row(
    *, task_id: str, context_id: str | None, result: VerificationResult, payload: Any
) -> dict[str, Any]:
    """Map a final verification result to a ``verification_reviews`` row.

    Invoice metadata is taken from the request only when the request is valid;
    for INVALID_REQUEST results those columns stay null so that malformed input
    is never stored as if it were trusted data. Line items and other document
    contents are not stored (findings carry only the evidence for each check).
    """
    invoice = None
    if result.result is not OverallResult.INVALID_REQUEST and isinstance(payload, dict):
        try:
            invoice = VerificationRequest.model_validate(payload).invoice
        except ValidationError:  # pragma: no cover - verify_payload already accepted it
            invoice = None

    return {
        "a2a_task_id": task_id,
        "a2a_context_id": context_id,
        "task_state": task_state_for(result),
        "verification_status": result.result.value,
        "invoice_number": result.invoice_number,
        "po_reference": invoice.po_reference if invoice else None,
        "po_number": result.po_number,
        "supplier_name": invoice.supplier_name if invoice else None,
        "currency": invoice.currency if invoice else None,
        # Sent as a string so the exact decimal reaches the numeric column (no float rounding).
        "invoice_total": str(invoice.total_amount) if invoice and invoice.total_amount is not None else None,
        "summary": result.summary,
        "findings": [f.model_dump(mode="json") for f in result.findings],
        "validation_errors": [e.model_dump(mode="json") for e in result.validation_errors],
        "assumptions": list(result.assumptions),
    }


class ReviewRepository:
    """Supabase-backed store for verification reviews (synchronous client)."""

    def __init__(self, client: Any, table: str = TABLE) -> None:
        self._client = client
        self._table = table

    def save(self, row: dict[str, Any]) -> dict[str, Any]:
        """Insert or update the review for ``row['a2a_task_id']`` and return the stored row."""
        response = self._run(
            "save review",
            lambda: self._client.table(self._table).upsert(row, on_conflict=IDEMPOTENCY_COLUMN).execute(),
        )
        if not response.data:
            raise PersistenceError("Supabase returned no row for the review upsert; the write is unconfirmed.")
        return response.data[0]

    def get_by_task_id(self, task_id: str) -> dict[str, Any] | None:
        """Return the stored review for an A2A task ID, or None if there is none."""
        response = self._run(
            "read review",
            lambda: self._client.table(self._table).select("*").eq(IDEMPOTENCY_COLUMN, task_id).maybe_single().execute(),
        )
        return response.data if response is not None else None

    def list_reviews(
        self, *, limit: int = 20, offset: int = 0, status: str | None = None
    ) -> tuple[list[dict[str, Any]], int]:
        """Newest-first page of reviews (summary columns) and the total matching count."""

        def query():
            q = self._client.table(self._table).select(LIST_COLUMNS, count=CountMethod.exact)
            if status:
                q = q.eq("verification_status", status)
            return q.order("created_at", desc=True).range(offset, offset + limit - 1).execute()

        response = self._run("list reviews", query)
        return list(response.data or []), int(response.count or 0)

    def count_by_status(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for status in STATUSES:
            response = self._run(
                "count reviews",
                lambda s=status: self._client.table(self._table)
                .select("id", count=CountMethod.exact, head=True)
                .eq("verification_status", s)
                .execute(),
            )
            counts[status] = int(response.count or 0)
        return counts

    def analytics_rows(self, limit: int = 1000) -> tuple[list[dict[str, Any]], bool]:
        """Most recent reviews with the columns the dashboard aggregates.

        Returns (rows, has_reasoning_columns); falls back to the base columns when
        migration 002 has not been applied yet.
        """
        def query(columns: str):
            return lambda: (self._client.table(self._table).select(columns)
                            .order("created_at", desc=True).limit(limit).execute())
        try:
            return list(self._run_raw(query(ANALYTICS_COLUMNS_V2)).data or []), True
        except MissingColumnsError:
            return list(self._run("load analytics", query(ANALYTICS_COLUMNS)).data or []), False

    def latest(self) -> dict[str, Any] | None:
        response = self._run(
            "load latest review",
            lambda: self._client.table(self._table).select("*").order("created_at", desc=True).limit(1).execute(),
        )
        return (response.data or [None])[0]

    def supplier_reviews(self, supplier: str, limit: int = 20) -> list[dict[str, Any]]:
        """Newest-first earlier reviews for one supplier (case-insensitive exact name)."""
        pattern = supplier.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        response = self._run(
            "load supplier history",
            lambda: self._client.table(self._table)
            .select("a2a_task_id,verification_status,invoice_number,invoice_total,currency,created_at")
            .ilike("supplier_name", pattern)
            .order("created_at", desc=True)
            .limit(limit)
            .execute(),
        )
        return list(response.data or [])

    def save_coordinator_outcome(self, task_id: str, outcome: dict[str, Any]) -> None:
        """Add Agent A's outcome (action, risk, reasoning) to the review saved by Agent B."""
        try:
            self._run_raw(lambda: self._client.table(self._table).update(outcome)
                          .eq(IDEMPOTENCY_COLUMN, task_id).execute())
        except MissingColumnsError as exc:
            raise PersistenceError(f"Reasoning columns are missing. Apply {MIGRATION_002} in the Supabase SQL Editor.") from exc

    def check_ready(self) -> None:
        """Cheap read that fails if the URL, key or table is wrong."""
        self._run("check table", lambda: self._client.table(self._table).select("id").limit(1).execute())

    def _run_raw(self, call):
        """Like _run, but surfaces missing-column errors as MissingColumnsError."""
        try:
            return call()
        except APIError as exc:
            if exc.code in _MISSING_COLUMN_CODES:
                raise MissingColumnsError(str(exc.message)) from exc
            raise PersistenceError(f"Supabase rejected the request ({exc.code}): {exc.message}") from exc
        except httpx.HTTPError as exc:
            raise PersistenceError(f"Could not reach Supabase ({type(exc).__name__})") from exc

    def _run(self, action: str, call):
        try:
            return call()
        except APIError as exc:
            hint = f" Has {MIGRATION_FILE} been applied in the Supabase SQL Editor?" if exc.code in _MISSING_TABLE_CODES else ""
            raise PersistenceError(f"Supabase rejected '{action}' ({exc.code}): {exc.message}.{hint}") from exc
        except httpx.HTTPError as exc:
            raise PersistenceError(f"Could not reach Supabase to {action}: {type(exc).__name__}") from exc


def create_review_repository(settings: SupabaseSettings) -> ReviewRepository:
    """Build a repository with a server-side Supabase client (no session persistence)."""
    from supabase import create_client
    from supabase.lib.client_options import SyncClientOptions

    options = SyncClientOptions(
        postgrest_client_timeout=settings.timeout_seconds,
        auto_refresh_token=False,
        persist_session=False,
    )
    return ReviewRepository(create_client(settings.url, settings.secret_key, options=options))

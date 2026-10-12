"""Invoice-verification request and result schema.

Validation is split in two layers so that *invalid* data is never confused
with *missing* data:

* **Schema layer (this module).** Values that are present must be well formed:
  correct types, valid ISO 4217 currency codes, non-negative finite amounts,
  positive quantities, no unknown fields, no duplicate SKUs. Any violation makes
  the request ``INVALID_REQUEST``.
* **Verification layer.** Business fields may be absent (``None``). Blank
  strings are treated as absent. The verifier reports each absent field as a
  ``MISSING_INFORMATION`` finding and never treats it as a match.
"""

from decimal import Decimal
from enum import Enum
from typing import Annotated, Any

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
)

MAX_LINE_ITEMS = 500


def _blank_to_none(value: Any) -> Any:
    if isinstance(value, str) and not value.strip():
        return None
    return value


def _normalise_currency(value: Any) -> Any:
    value = _blank_to_none(value)
    return value.strip().upper() if isinstance(value, str) else value


Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
_DocumentNumber = Annotated[
    str, StringConstraints(strip_whitespace=True, pattern=r"^[A-Za-z0-9][A-Za-z0-9 ._/#-]{0,63}$")
]
_CurrencyCode = Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")]
_Amount = Annotated[Decimal, Field(ge=0, allow_inf_nan=False, max_digits=20, decimal_places=6)]
_Quantity = Annotated[Decimal, Field(gt=0, allow_inf_nan=False, max_digits=20, decimal_places=6)]

# Optional business fields. The BeforeValidator wraps the whole ``X | None`` union so a
# blank string becomes None (missing) before type checks run.
OptText = Annotated[Text | None, BeforeValidator(_blank_to_none)]
OptDocumentNumber = Annotated[_DocumentNumber | None, BeforeValidator(_blank_to_none)]
# Case is normalised before the pattern check, so " gbp " is accepted as GBP.
OptCurrencyCode = Annotated[_CurrencyCode | None, BeforeValidator(_normalise_currency)]
OptAmount = Annotated[_Amount | None, BeforeValidator(_blank_to_none)]
OptQuantity = Annotated[_Quantity | None, BeforeValidator(_blank_to_none)]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LineItem(_StrictModel):
    sku: Text  # line-matching key: structurally required
    description: OptText = None
    quantity: OptQuantity = None
    unit_price: OptAmount = None
    line_total: OptAmount = None


def _reject_duplicate_skus(items: list[LineItem] | None) -> list[LineItem] | None:
    if items:
        seen: set[str] = set()
        for item in items:
            key = item.sku.casefold()
            if key in seen:
                raise ValueError(f"duplicate sku '{item.sku}' in line_items")
            seen.add(key)
    return items


class Invoice(_StrictModel):
    invoice_number: OptDocumentNumber = None
    supplier_name: OptText = None
    currency: OptCurrencyCode = None
    po_reference: OptDocumentNumber = None
    line_items: list[LineItem] | None = Field(default=None, max_length=MAX_LINE_ITEMS)
    tax_amount: OptAmount = None
    total_amount: OptAmount = None

    _unique_skus = field_validator("line_items")(_reject_duplicate_skus)


class PurchaseOrder(_StrictModel):
    po_number: OptDocumentNumber = None
    supplier_name: OptText = None
    currency: OptCurrencyCode = None
    line_items: list[LineItem] | None = Field(default=None, max_length=MAX_LINE_ITEMS)
    total_amount: OptAmount = None

    _unique_skus = field_validator("line_items")(_reject_duplicate_skus)


class VerificationRequest(_StrictModel):
    invoice: Invoice  # without an invoice there is nothing to verify
    purchase_order: PurchaseOrder | None = None


# --- Result -----------------------------------------------------------------


class CheckOutcome(str, Enum):
    PASS = "PASS"
    MISMATCH = "MISMATCH"
    MISSING_INFORMATION = "MISSING_INFORMATION"


class OverallResult(str, Enum):
    # Every check ran and passed.
    MATCH = "MATCH"
    # At least one check found a confirmed discrepancy.
    DISCREPANCY_FOUND = "DISCREPANCY_FOUND"
    # No confirmed discrepancy, but at least one check lacked information.
    NEEDS_REVIEW = "NEEDS_REVIEW"
    # The request was malformed; no checks were run.
    INVALID_REQUEST = "INVALID_REQUEST"


DISCLAIMER = (
    "Automated consistency check only. This result is not an approval for payment "
    "and must be reviewed by an authorised person."
)


class Finding(BaseModel):
    check: str
    outcome: CheckOutcome
    explanation: str
    evidence: dict[str, Any] = Field(default_factory=dict)


class ValidationIssue(BaseModel):
    field: str
    message: str


class VerificationResult(BaseModel):
    result: OverallResult
    invoice_number: str | None = None
    po_number: str | None = None
    summary: str
    findings: list[Finding] = Field(default_factory=list)
    validation_errors: list[ValidationIssue] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    disclaimer: str = DISCLAIMER

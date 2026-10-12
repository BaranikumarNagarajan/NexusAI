"""Deterministic invoice vs. purchase-order verification.

This module knows nothing about A2A. It takes a plain ``dict`` payload and
returns a :class:`VerificationResult`, so it can be tested in isolation and
reused by any transport.
"""

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from pydantic import ValidationError

from app.agents.invoice_verifier.money import fmt_amount, fmt_quantity, minor_units, round_money
from app.models.invoice import (
    CheckOutcome,
    Finding,
    Invoice,
    LineItem,
    OverallResult,
    PurchaseOrder,
    ValidationIssue,
    VerificationRequest,
    VerificationResult,
)

BASE_ASSUMPTIONS = [
    "Monetary values use exact decimal arithmetic; a difference of any size is reported (no tolerance).",
    "Amounts in different currencies are never converted or treated as equivalent.",
    "Line items are matched between invoice and purchase order by SKU, ignoring case.",
    "Supplier names are compared ignoring case, punctuation and repeated spaces.",
]


def verify_payload(payload: Any) -> VerificationResult:
    """Validate a raw request payload and verify it."""
    if not isinstance(payload, dict):
        return _invalid([ValidationIssue(field="(root)", message="request must be a JSON object")])
    try:
        request = VerificationRequest.model_validate(payload)
    except ValidationError as exc:
        return _invalid(_to_issues(exc), invoice_number=_peek_invoice_number(payload))
    return verify(request)


def verify(request: VerificationRequest) -> VerificationResult:
    """Run every check on an already-validated request."""
    run = _Run(request.invoice, request.purchase_order)
    run.check_invoice_number()
    run.check_po_reference()
    run.check_supplier()
    run.check_currency()
    run.check_line_items()
    run.check_line_totals()
    run.check_invoice_total_arithmetic()
    run.check_total_against_po()

    result = _overall(run.findings)
    return VerificationResult(
        result=result,
        invoice_number=request.invoice.invoice_number,
        po_number=request.purchase_order.po_number if request.purchase_order else None,
        summary=_summary(result, run.findings),
        findings=run.findings,
        assumptions=[*BASE_ASSUMPTIONS, *run.assumptions],
    )


@dataclass
class _Run:
    invoice: Invoice
    po: PurchaseOrder | None
    findings: list[Finding] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.places, assumed = minor_units(self.invoice.currency)
        if assumed:
            label = self.invoice.currency or "currency not provided"
            self.assumptions.append(
                f"Minor unit for {label} is not known to the verifier; assumed {self.places} decimal places."
            )
        self.assumptions.append(
            f"Computed line amounts are quantity x unit_price rounded half-up to {self.places} decimal places."
        )

    # -- helpers ---------------------------------------------------------------

    def _add(self, check: str, outcome: CheckOutcome, explanation: str, **evidence: Any) -> None:
        self.findings.append(Finding(check=check, outcome=outcome, explanation=explanation, evidence=evidence))

    def _po_field(self, name: str) -> tuple[Any, str]:
        """Value of a PO field and the path to report if it is missing."""
        if self.po is None:
            return None, "purchase_order"
        return getattr(self.po, name), f"purchase_order.{name}"

    def _compare_money(self, check: str, what: str, inv_value: Decimal, po_value: Decimal, evidence: dict) -> None:
        """Compare an invoice amount with a PO amount, respecting currencies."""
        inv_cur = self.invoice.currency
        po_cur = self.po.currency if self.po else None
        if inv_cur and po_cur and inv_cur != po_cur:
            evidence.update({"invoice.currency": inv_cur, "purchase_order.currency": po_cur})
            self._add(
                check,
                CheckOutcome.MISMATCH,
                f"Cannot confirm {what}: the documents use different currencies ({inv_cur} vs {po_cur}).",
                **evidence,
            )
            return
        note = "" if inv_cur and po_cur else " (assumes both documents use the same currency, which could not be confirmed)"
        if inv_value == po_value:
            self._add(check, CheckOutcome.PASS, f"Invoice and purchase order agree on {what}{note}.", **evidence)
        else:
            evidence["difference"] = fmt_amount(inv_value - po_value)
            self._add(check, CheckOutcome.MISMATCH, f"Invoice {what} differs from the purchase order{note}.", **evidence)

    def _missing(self, check: str, what: str, missing: list[str]) -> None:
        self._add(
            check,
            CheckOutcome.MISSING_INFORMATION,
            f"Cannot verify {what}: {', '.join(missing)} not provided.",
            missing_fields=missing,
        )

    def _compare(self, check: str, what: str, inv_field: str, po_field: str, normalise=lambda v: v) -> None:
        inv_value = getattr(self.invoice, inv_field)
        po_value, po_path = self._po_field(po_field)
        missing = [p for p, v in ((f"invoice.{inv_field}", inv_value), (po_path, po_value)) if v is None]
        if missing:
            self._missing(check, what, missing)
            return
        evidence = {f"invoice.{inv_field}": inv_value, po_path: po_value}
        if normalise(inv_value) == normalise(po_value):
            self._add(check, CheckOutcome.PASS, f"Invoice and purchase order agree on {what}.", **evidence)
        else:
            self._add(check, CheckOutcome.MISMATCH, f"Invoice {what} differs from the purchase order.", **evidence)

    # -- checks ----------------------------------------------------------------

    def check_invoice_number(self) -> None:
        if self.invoice.invoice_number is None:
            self._missing("invoice_number", "invoice identity", ["invoice.invoice_number"])
        else:
            self._add(
                "invoice_number",
                CheckOutcome.PASS,
                "Invoice number is present and well formed.",
                **{"invoice.invoice_number": self.invoice.invoice_number},
            )

    def check_po_reference(self) -> None:
        self._compare("po_reference", "purchase-order reference", "po_reference", "po_number", str.casefold)

    def check_supplier(self) -> None:
        self._compare("supplier", "supplier", "supplier_name", "supplier_name", _normalise_name)

    def check_currency(self) -> None:
        self._compare("currency", "currency", "currency", "currency")

    def check_line_items(self) -> None:
        po_lines, po_path = self._po_field("line_items")
        missing = [p for p, v in (("invoice.line_items", self.invoice.line_items), (po_path, po_lines)) if v is None]
        if missing:
            self._missing("line_items", "line items against the purchase order", missing)
            return

        invoiced = {i.sku.casefold(): i for i in self.invoice.line_items or []}
        ordered = {i.sku.casefold(): i for i in po_lines}
        for key in [*invoiced, *(k for k in ordered if k not in invoiced)]:
            inv_item, po_item = invoiced.get(key), ordered.get(key)
            if inv_item is None or po_item is None:
                self._line_presence(inv_item, po_item)
                continue
            self._line_value(inv_item, po_item, "quantity", "line_item_quantity", fmt_quantity)
            self._line_value(inv_item, po_item, "unit_price", "line_item_unit_price", fmt_amount)

    def _line_presence(self, inv_item: LineItem | None, po_item: LineItem | None) -> None:
        if inv_item is not None:
            self._add(
                "line_item_presence",
                CheckOutcome.MISMATCH,
                f"SKU {inv_item.sku} is invoiced but does not appear on the purchase order.",
                sku=inv_item.sku,
            )
        elif po_item is not None:
            self._add(
                "line_item_presence",
                CheckOutcome.MISMATCH,
                f"SKU {po_item.sku} was ordered but is not on this invoice "
                "(possibly a partial invoice; confirm with the supplier).",
                sku=po_item.sku,
            )

    def _line_value(self, inv_item: LineItem, po_item: LineItem, attr: str, check: str, fmt) -> None:
        inv_value, po_value = getattr(inv_item, attr), getattr(po_item, attr)
        sku = inv_item.sku
        missing = [
            p
            for p, v in ((f"invoice.line_items[{sku}].{attr}", inv_value), (f"purchase_order.line_items[{sku}].{attr}", po_value))
            if v is None
        ]
        if missing:
            self._missing(check, f"{attr.replace('_', ' ')} for SKU {sku}", missing)
            return
        evidence = {"sku": sku, f"invoice.{attr}": fmt(inv_value), f"purchase_order.{attr}": fmt(po_value)}
        if attr == "unit_price":
            self._compare_money(check, f"unit price for SKU {sku}", inv_value, po_value, evidence)
            return
        if inv_value == po_value:
            self._add(check, CheckOutcome.PASS, f"Invoiced {attr.replace('_', ' ')} for SKU {sku} matches the order.", **evidence)
        else:
            evidence["difference"] = fmt(inv_value - po_value)
            self._add(
                check,
                CheckOutcome.MISMATCH,
                f"Invoiced {attr.replace('_', ' ')} for SKU {sku} differs from the ordered value.",
                **evidence,
            )

    def check_line_totals(self) -> None:
        """Each stated invoice line_total must equal round(quantity x unit_price)."""
        for item in self.invoice.line_items or []:
            if item.line_total is None:
                continue  # nothing claimed, nothing to verify; the total check uses the computed value
            if item.quantity is None or item.unit_price is None:
                missing = [
                    f"invoice.line_items[{item.sku}].{a}" for a in ("quantity", "unit_price") if getattr(item, a) is None
                ]
                self._missing("line_total_arithmetic", f"line total for SKU {item.sku}", missing)
                continue
            expected = round_money(item.quantity * item.unit_price, self.places)
            evidence = {
                "sku": item.sku,
                "calculation": f"{fmt_quantity(item.quantity)} x {fmt_amount(item.unit_price)}",
                "expected_line_total": fmt_amount(expected, self.places),
                "invoice.line_total": fmt_amount(item.line_total),
            }
            if item.line_total == expected:
                self._add("line_total_arithmetic", CheckOutcome.PASS, f"Line total for SKU {item.sku} is correct.", **evidence)
            else:
                evidence["difference"] = fmt_amount(item.line_total - expected)
                self._add(
                    "line_total_arithmetic",
                    CheckOutcome.MISMATCH,
                    f"Stated line total for SKU {item.sku} does not equal quantity x unit price.",
                    **evidence,
                )

    def check_invoice_total_arithmetic(self) -> None:
        """invoice.total_amount must equal the sum of line amounts plus tax."""
        check, what = "invoice_total_arithmetic", "invoice total arithmetic"
        lines = self.invoice.line_items
        missing: list[str] = []
        if self.invoice.total_amount is None:
            missing.append("invoice.total_amount")
        if not lines:
            missing.append("invoice.line_items")
        else:
            for item in lines:
                if item.line_total is None and (item.quantity is None or item.unit_price is None):
                    missing.append(f"invoice.line_items[{item.sku}].unit_price/quantity")
        if missing:
            self._missing(check, what, missing)
            return

        # Use the invoice's own stated line totals where given (their arithmetic is checked separately).
        line_amounts = [
            item.line_total if item.line_total is not None else round_money(item.quantity * item.unit_price, self.places)  # type: ignore[operator]
            for item in lines  # type: ignore[union-attr]
        ]
        subtotal = sum(line_amounts, Decimal(0))
        tax = self.invoice.tax_amount
        if tax is None:
            tax = Decimal(0)
            self.assumptions.append(
                "invoice.tax_amount was not provided; treated as 0 in the invoice total arithmetic check."
            )
        expected = subtotal + tax
        total = self.invoice.total_amount
        evidence = {
            "line_subtotal": fmt_amount(subtotal, self.places),
            "tax_amount": fmt_amount(tax, self.places),
            "expected_total": fmt_amount(expected, self.places),
            "invoice.total_amount": fmt_amount(total),  # type: ignore[arg-type]
        }
        if total == expected:
            self._add(check, CheckOutcome.PASS, "Invoice total equals the sum of line amounts plus tax.", **evidence)
        else:
            evidence["difference"] = fmt_amount(total - expected)  # type: ignore[operator]
            self._add(check, CheckOutcome.MISMATCH, "Invoice total does not equal the sum of line amounts plus tax.", **evidence)

    def check_total_against_po(self) -> None:
        inv_total = self.invoice.total_amount
        po_total, po_path = self._po_field("total_amount")
        missing = [p for p, v in (("invoice.total_amount", inv_total), (po_path, po_total)) if v is None]
        if missing:
            self._missing("total_amount", "invoice total against the purchase order", missing)
            return
        evidence = {"invoice.total_amount": fmt_amount(inv_total), po_path: fmt_amount(po_total)}
        self._compare_money("total_amount", "total amount", inv_total, po_total, evidence)


# --- Helpers -----------------------------------------------------------------


def _normalise_name(name: str) -> str:
    return " ".join(name.casefold().replace(",", " ").replace(".", " ").split())


def _overall(findings: list[Finding]) -> OverallResult:
    outcomes = {f.outcome for f in findings}
    if CheckOutcome.MISMATCH in outcomes:
        return OverallResult.DISCREPANCY_FOUND
    if CheckOutcome.MISSING_INFORMATION in outcomes:
        return OverallResult.NEEDS_REVIEW
    return OverallResult.MATCH


def _summary(result: OverallResult, findings: list[Finding]) -> str:
    def checks(outcome: CheckOutcome) -> str:
        # Readable check names in the summary ("line item quantity"); findings keep the exact ids.
        return ", ".join(dict.fromkeys(f.check.replace("_", " ") for f in findings if f.outcome is outcome))

    if result is OverallResult.DISCREPANCY_FOUND:
        text = f"Discrepancies found in: {checks(CheckOutcome.MISMATCH)}."
        if any(f.outcome is CheckOutcome.MISSING_INFORMATION for f in findings):
            text += f" Not verifiable due to missing information: {checks(CheckOutcome.MISSING_INFORMATION)}."
        return text
    if result is OverallResult.NEEDS_REVIEW:
        return (
            "No confirmed discrepancies, but these checks could not be completed due to missing information: "
            f"{checks(CheckOutcome.MISSING_INFORMATION)}."
        )
    return "All checks passed: invoice is consistent with the purchase order."


def _invalid(issues: list[ValidationIssue], invoice_number: str | None = None) -> VerificationResult:
    return VerificationResult(
        result=OverallResult.INVALID_REQUEST,
        invoice_number=invoice_number,
        summary="Request is malformed; no verification checks were run.",
        validation_errors=issues,
    )


def _to_issues(exc: ValidationError) -> list[ValidationIssue]:
    # Deliberately omit pydantic's "input" echo so payload values are not reflected back.
    return [
        ValidationIssue(field=".".join(str(p) for p in err["loc"]) or "(root)", message=err["msg"])
        for err in exc.errors(include_input=False, include_url=False)
    ]


def _peek_invoice_number(payload: dict) -> str | None:
    invoice = payload.get("invoice")
    if isinstance(invoice, dict) and isinstance(invoice.get("invoice_number"), str):
        return invoice["invoice_number"][:64]
    return None

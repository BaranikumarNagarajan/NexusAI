"""Extra, verified context Agent A gathers before it explains a result.

Both values are computed in code, never by the language model, so they can be quoted as facts:
  * financial_impact - how much money the differences put at stake (from Agent B's evidence);
  * supplier_history - how this supplier's earlier invoices turned out (from Supabase).
"""

import logging
from decimal import Decimal, InvalidOperation
from typing import Any

logger = logging.getLogger(__name__)

HISTORY_LIMIT = 20  # distinct earlier invoices summarised
HISTORY_SCAN = 200  # rows read to find them


def _dec(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _money(value: Decimal) -> str:
    return f"{value.quantize(Decimal('0.01')):,.2f}"


def financial_impact(verification: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any] | None:
    """Invoice total vs PO total, plus the money effect of each line-level mismatch."""
    invoice = payload.get("invoice") if isinstance(payload.get("invoice"), dict) else {}
    po = payload.get("purchase_order") if isinstance(payload.get("purchase_order"), dict) else {}
    inv_total, po_total = _dec(invoice.get("total_amount")), _dec(po.get("total_amount"))
    if inv_total is None and po_total is None:
        return None
    inv_lines = {str(i.get("sku")).casefold(): i for i in invoice.get("line_items") or [] if isinstance(i, dict)}
    po_lines = {str(i.get("sku")).casefold(): i for i in po.get("line_items") or [] if isinstance(i, dict)}

    lines = []
    for finding in verification.get("findings", []):
        evidence = finding.get("evidence") or {}
        sku = evidence.get("sku")
        diff = _dec(evidence.get("difference"))
        if finding.get("outcome") != "MISMATCH" or not sku or diff is None:
            continue
        key = str(sku).casefold()
        if finding.get("check") == "line_item_unit_price":
            qty = _dec((inv_lines.get(key) or {}).get("quantity"))
            if qty is not None:
                lines.append({"sku": sku, "issue": "unit price", "detail": f"{_money(diff)} per unit x {qty.normalize():f} units",
                              "amount": _money(diff * qty)})
        elif finding.get("check") == "line_item_quantity":
            price = _dec((po_lines.get(key) or {}).get("unit_price"))
            if price is not None:
                lines.append({"sku": sku, "issue": "quantity", "detail": f"{diff.normalize():f} units x {_money(price)} PO price",
                              "amount": _money(diff * price)})

    result: dict[str, Any] = {"currency": invoice.get("currency") or po.get("currency"),
                              "invoice_total": _money(inv_total) if inv_total is not None else None,
                              "po_total": _money(po_total) if po_total is not None else None,
                              "lines": lines}
    inv_cur, po_cur = invoice.get("currency"), po.get("currency")
    if inv_cur and po_cur and inv_cur != po_cur:
        # 600 USD vs 600 EUR is not "equal": amounts in different currencies cannot be compared.
        result["direction"] = "different_currency"
        result["po_currency"] = po_cur
    elif inv_total is not None and po_total is not None:
        difference = inv_total - po_total
        result["difference"] = _money(abs(difference))
        result["direction"] = "over" if difference > 0 else "under" if difference < 0 else "equal"
        if po_total:
            result["difference_percent"] = f"{abs(difference) / po_total * 100:.1f}"
    return result


def supplier_history(repository: Any, supplier: str | None, current_task_id: str | None,
                     invoice_number: str | None = None) -> dict[str, Any] | None:
    """Summarise the supplier's earlier reviews (the current one, already saved by Agent B, is excluded).
    Returns None when storage is off or the lookup fails: history is a nice-to-have."""
    if repository is None or not supplier:
        return None
    try:
        rows = [r for r in repository.supplier_reviews(supplier, HISTORY_SCAN)
                if r.get("a2a_task_id") != current_task_id]
    except Exception as exc:  # defensive: never let history break a review
        logger.warning("Supplier history lookup failed: %s", type(exc).__name__)
        return None
    key = lambda r: str(r.get("invoice_number") or "").casefold()
    current = str(invoice_number or "").casefold()
    # The same invoice number seen before may be a duplicate submission (a classic payables risk).
    # It is reported on its own and not counted in the track record, so re-submitting one invoice
    # does not make the supplier look worse than it is.
    same_invoice = sum(1 for r in rows if current and key(r) == current)
    distinct: dict[str, dict[str, Any]] = {}
    for row in rows:  # newest first: keep each other invoice's latest outcome
        if key(row) != current and key(row) not in distinct:
            distinct[key(row)] = row
    rows = list(distinct.values())[:HISTORY_LIMIT]
    counts = {"MATCH": 0, "DISCREPANCY_FOUND": 0, "NEEDS_REVIEW": 0, "INVALID_REQUEST": 0}
    for row in rows:
        counts[row.get("verification_status")] = counts.get(row.get("verification_status"), 0) + 1
    recent = [{"invoice_number": r.get("invoice_number"), "result": r.get("verification_status"),
               "date": (r.get("created_at") or "")[:10]} for r in rows[:5]]
    history = {"previous_reviews": len(rows), "matches": counts["MATCH"], "discrepancies": counts["DISCREPANCY_FOUND"],
               "needs_review": counts["NEEDS_REVIEW"], "recent": recent, "same_invoice_seen_before": same_invoice}
    if rows:
        history["discrepancy_rate_percent"] = round(counts["DISCREPANCY_FOUND"] / len(rows) * 100)
    return history

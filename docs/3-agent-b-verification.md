# 3 · Agent B: deterministic invoice verification

[← 2 · Agent A](2-agent-a-and-a2a.md) · [README](../README.md) · Next: [4 · Supabase](4-supabase.md)

**Agent B, the Invoice Verification Specialist** (`:10001`), compares an invoice with its
purchase order using fixed rules and exact decimal arithmetic. Same input, same answer, every
time. There is **no AI in this step**.

Code: [`backend/app/agents/invoice_verifier/`](../backend/app/agents/invoice_verifier/) and the
schema in [`backend/app/models/invoice.py`](../backend/app/models/invoice.py)

| File | Job |
|---|---|
| `verification.py` | Pure rules: request → findings → overall result (no network, no A2A) |
| `money.py` | Decimal helpers: currency minor units, half-up rounding |
| `agent_card.py` | Agent B's card: skill `verify_invoice_against_po`, input/output description |
| `executor.py` | A2A task lifecycle; saves each final review to Supabase |
| `server.py`, `__main__.py` | Build and run the A2A server |

## Request: one A2A data part (`application/json`)

```json
{
  "invoice": {
    "invoice_number": "INV-1001",
    "supplier_name": "Acme Office Supplies Ltd",
    "currency": "GBP",
    "po_reference": "PO-5001",
    "line_items": [
      {"sku": "PAPER-A4", "description": "A4 copier paper", "quantity": "10", "unit_price": "25.00", "line_total": "250.00"},
      {"sku": "PEN-BLK", "quantity": "5", "unit_price": "22.50", "line_total": "112.50"}
    ],
    "tax_amount": "72.50",
    "total_amount": "435.00"
  },
  "purchase_order": {
    "po_number": "PO-5001",
    "supplier_name": "ACME Office Supplies Ltd.",
    "currency": "GBP",
    "line_items": [
      {"sku": "PAPER-A4", "quantity": "10", "unit_price": "25.00"},
      {"sku": "PEN-BLK", "quantity": "5", "unit_price": "22.50"}
    ],
    "total_amount": "435.00"
  }
}
```

| Field | Rule if present | If absent |
|---|---|---|
| `invoice` (object) | required | `INVALID_REQUEST` |
| `invoice_number`, `po_reference`, `po_number` | 1–64 chars: letters, digits, space `. _ / # -` | `MISSING_INFORMATION` |
| `supplier_name` | text, max 200 chars | `MISSING_INFORMATION` |
| `currency` | ISO 4217 3-letter code | `MISSING_INFORMATION` |
| `line_items[].sku` | required in each line; unique per document | `INVALID_REQUEST` |
| `quantity` | decimal > 0 | `MISSING_INFORMATION` |
| `unit_price`, `line_total`, `tax_amount`, `total_amount` | decimal ≥ 0, max 6 decimal places | `MISSING_INFORMATION` (`tax_amount`: assumed 0, recorded in `assumptions`) |
| unknown fields | not allowed | — |

Send money and quantities as **decimal strings** (A2A data parts store numbers as floats).

## The checks

| `check` | Rule |
|---|---|
| `invoice_number` | present |
| `po_reference` | `invoice.po_reference` equals `purchase_order.po_number` (case-insensitive) |
| `supplier` | equal, ignoring case, `.`/`,` and repeated spaces |
| `currency` | equal codes |
| `line_items` / `line_item_presence` | both documents list lines; each SKU appears on both |
| `line_item_quantity` / `line_item_unit_price` | per-SKU equality; different currencies → `MISMATCH` |
| `line_total_arithmetic` | stated `line_total` = `round_half_up(quantity × unit_price)` |
| `invoice_total_arithmetic` | `total_amount` = sum of line amounts + `tax_amount` |
| `total_amount` | invoice total = PO total; different currencies → `MISMATCH` |

## Result: artifact `verification_result`

Always present: `result`, `invoice_number`, `po_number`, `summary`,
`findings[{check, outcome, explanation, evidence}]`, `validation_errors`, `assumptions`, `disclaimer`.

| `result` | Meaning |
|---|---|
| `MATCH` | Every check ran and passed |
| `DISCREPANCY_FOUND` | At least one `MISMATCH` (takes priority over missing information) |
| `NEEDS_REVIEW` | No mismatch, but at least one `MISSING_INFORMATION` |
| `INVALID_REQUEST` | Malformed payload; `validation_errors` lists the fields |

Each finding's `outcome` is `PASS`, `MISMATCH` or `MISSING_INFORMATION`. **Missing data is
never counted as a pass**, and no result ever says an invoice is approved for payment.

## Currency and rounding

- **Decimal only:** Python `Decimal`, never floats; comparisons are exact with no tolerance.
- **Rounding:** half-up to the currency's minor unit: 2 places by default, 0 for JPY and KRW,
  3 for BHD, KWD and OMR. An unknown currency assumes 2 places, and the result says so.
- **No conversion:** amounts in different currencies are never converted or treated as equal.

## The six scenarios (`backend/samples/json/`)

| Scenario | Agent B result | Agent A action |
|---|---|---|
| `matching` | `MATCH` | `ROUTE_TO_APPROVER` |
| `supplier_mismatch` | `DISCREPANCY_FOUND` | `HOLD_AND_QUERY_SUPPLIER` |
| `currency_mismatch` | `DISCREPANCY_FOUND` | `HOLD_AND_QUERY_SUPPLIER` |
| `quantity_total_mismatch` | `DISCREPANCY_FOUND` | `HOLD_AND_QUERY_SUPPLIER` |
| `missing_information` | `NEEDS_REVIEW` | `REQUEST_MISSING_INFORMATION` |
| `invalid_request` | `INVALID_REQUEST` (task `REJECTED`) | `CORRECT_AND_RESUBMIT` |

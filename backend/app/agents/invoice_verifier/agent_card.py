"""Agent Card for the Invoice Verification Specialist."""

import json

from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill

SKILL_ID = "verify_invoice_against_po"
JSON_MEDIA_TYPE = "application/json"

_EXAMPLE_REQUEST = {
    "invoice": {
        "invoice_number": "INV-1001",
        "supplier_name": "Acme Office Supplies Ltd",
        "currency": "GBP",
        "po_reference": "PO-5001",
        "line_items": [
            {"sku": "PAPER-A4", "quantity": "10", "unit_price": "25.00", "line_total": "250.00"}
        ],
        "tax_amount": "50.00",
        "total_amount": "300.00",
    },
    "purchase_order": {
        "po_number": "PO-5001",
        "supplier_name": "Acme Office Supplies Ltd",
        "currency": "GBP",
        "line_items": [{"sku": "PAPER-A4", "quantity": "10", "unit_price": "25.00"}],
        "total_amount": "300.00",
    },
}

_SKILL_DESCRIPTION = (
    "Checks an invoice against its purchase order using deterministic rules: invoice number, "
    "PO reference, supplier, currency, per-SKU quantity and unit price, line-total arithmetic, "
    "invoice-total arithmetic (sum of lines + tax) and invoice total vs PO total. "
    "INPUT: exactly one A2A data part (application/json): "
    "{invoice: {invoice_number, supplier_name, currency (ISO 4217), po_reference, "
    "line_items: [{sku (required), description, quantity, unit_price, line_total}], tax_amount, total_amount}, "
    "purchase_order: {po_number, supplier_name, currency, line_items, total_amount}}. "
    "Send amounts and quantities as decimal strings. Absent fields are reported as missing information, "
    "never as a match. "
    "OUTPUT: artifact 'verification_result' (application/json) with result "
    "(MATCH | DISCREPANCY_FOUND | NEEDS_REVIEW | INVALID_REQUEST), summary, "
    "findings[{check, outcome (PASS | MISMATCH | MISSING_INFORMATION), explanation, evidence}], "
    "validation_errors, assumptions and disclaimer. Malformed requests end in task state REJECTED. "
    "This agent never approves invoices for payment."
)


def build_agent_card(public_url: str) -> AgentCard:
    skill = AgentSkill(
        id=SKILL_ID,
        name="Verify invoice against purchase order",
        description=_SKILL_DESCRIPTION,
        tags=["invoice", "purchase-order", "procurement", "verification", "three-way-match"],
        examples=[json.dumps(_EXAMPLE_REQUEST)],
        input_modes=[JSON_MEDIA_TYPE],
        output_modes=[JSON_MEDIA_TYPE],
    )
    return AgentCard(
        name="NexusAI Invoice Verification Specialist",
        description=(
            "Independently checks invoice details against purchase-order details using "
            "deterministic rules and returns structured findings with evidence."
        ),
        version="0.2.0",
        supported_interfaces=[
            AgentInterface(url=public_url, protocol_binding="JSONRPC", protocol_version="1.0")
        ],
        capabilities=AgentCapabilities(streaming=False, push_notifications=False),
        default_input_modes=[JSON_MEDIA_TYPE],
        default_output_modes=[JSON_MEDIA_TYPE],
        skills=[skill],
    )

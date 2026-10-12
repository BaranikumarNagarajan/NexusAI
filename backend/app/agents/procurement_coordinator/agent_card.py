"""Agent Card for the Procurement Coordinator."""

from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill

SKILL_ID = "coordinate_invoice_review"
JSON_MEDIA_TYPE = "application/json"

_SKILL_DESCRIPTION = (
    "Receives an invoice-review request, delegates the invoice-vs-purchase-order verification to the "
    "Invoice Verification Specialist over A2A (discovered through its Agent Card), and turns the specialist's "
    "findings into a procurement next step. "
    "INPUT: exactly one A2A data part (application/json) with the same shape the specialist accepts: "
    "{invoice: {...}, purchase_order: {...}}. "
    "OUTPUT: artifact 'procurement_review' (application/json) with recommended_action "
    "(ROUTE_TO_APPROVER | HOLD_AND_QUERY_SUPPLIER | REQUEST_MISSING_INFORMATION | CORRECT_AND_RESUBMIT), "
    "verification_result, summary, action_items, delegation (specialist name, task ID, task state, "
    "persistence status), reasoning (plain-language explanation, risk level, risk points and a draft supplier "
    "email - written by a local Ollama LLM from the verified facts only, or by a built-in writer) "
    "and the specialist's full verification result. "
    "Malformed requests end in task state REJECTED; if the specialist is unavailable the task FAILS. "
    "This agent never approves invoices for payment."
)


def build_agent_card(public_url: str) -> AgentCard:
    skill = AgentSkill(
        id=SKILL_ID,
        name="Coordinate invoice review",
        description=_SKILL_DESCRIPTION,
        tags=["procurement", "invoice", "coordination", "delegation", "a2a"],
        examples=['{"invoice": {"invoice_number": "INV-1001", ...}, "purchase_order": {"po_number": "PO-5001", ...}}'],
        input_modes=[JSON_MEDIA_TYPE],
        output_modes=[JSON_MEDIA_TYPE],
    )
    return AgentCard(
        name="NexusAI Procurement Coordinator",
        description=(
            "Coordinates procurement invoice reviews by delegating verification to a specialist agent "
            "over A2A and recommending the next procurement action."
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

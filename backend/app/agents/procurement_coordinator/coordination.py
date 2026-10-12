"""Turns the specialist's verification result into a procurement next step.

Pure logic on plain dicts (the specialist's ``verification_result`` artifact),
so Agent A depends only on Agent B's published output, not on its code.
"""

from typing import Any

DISCLAIMER = (
    "Procurement guidance only. NexusAI does not approve invoices for payment; "
    "an authorised person must make the payment decision."
)

# verification result -> recommended procurement action
_ACTIONS = {
    "MATCH": "ROUTE_TO_APPROVER",
    "DISCREPANCY_FOUND": "HOLD_AND_QUERY_SUPPLIER",
    "NEEDS_REVIEW": "REQUEST_MISSING_INFORMATION",
    "INVALID_REQUEST": "CORRECT_AND_RESUBMIT",
}


class UnusableResultError(Exception):
    """The specialist's result is missing or not in the advertised format."""


def build_procurement_review(verification: dict[str, Any] | None, delegation: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(verification, dict) or verification.get("result") not in _ACTIONS:
        raise UnusableResultError("the verification agent returned no recognisable verification_result")

    status = verification["result"]
    findings = verification.get("findings") or []
    mismatches = [f for f in findings if f.get("outcome") == "MISMATCH"]
    missing = [f for f in findings if f.get("outcome") == "MISSING_INFORMATION"]
    invoice = verification.get("invoice_number") or "(no invoice number)"

    if status == "MATCH":
        summary = (
            f"Invoice {invoice} is consistent with purchase order {verification.get('po_number')}. "
            "Route it to an authorised approver for the payment decision."
        )
        action_items = [f"Send invoice {invoice} to an authorised approver with the verification evidence."]
    elif status == "DISCREPANCY_FOUND":
        summary = (
            f"Invoice {invoice} has {len(mismatches)} discrepancy finding(s). "
            "Place it on hold and query the supplier before any approval."
        )
        action_items = [f"Resolve {f['check']}: {f['explanation']}" for f in mismatches]
        action_items += [f"Also obtain: {', '.join(f['evidence'].get('missing_fields', []))}" for f in missing]
    elif status == "NEEDS_REVIEW":
        summary = (
            f"Invoice {invoice} could not be fully verified: {len(missing)} check(s) lack information. "
            "Request the missing details, then resubmit."
        )
        action_items = [
            f"Provide {', '.join(f['evidence'].get('missing_fields', [])) or f['check']} ({f['check']})"
            for f in missing
        ]
    else:  # INVALID_REQUEST
        errors = verification.get("validation_errors") or []
        summary = "The request was rejected as malformed by the verification agent. Correct it and resubmit."
        action_items = [f"Fix {e.get('field')}: {e.get('message')}" for e in errors]

    return {
        "recommended_action": _ACTIONS[status],
        "verification_result": status,
        "invoice_number": verification.get("invoice_number"),
        "po_number": verification.get("po_number"),
        "summary": summary,
        "action_items": action_items,
        "delegation": delegation,
        "verification": verification,
        "disclaimer": DISCLAIMER,
    }


def is_rejection(review: dict[str, Any]) -> bool:
    return review["recommended_action"] == "CORRECT_AND_RESUBMIT"

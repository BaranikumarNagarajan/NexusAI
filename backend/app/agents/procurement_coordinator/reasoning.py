"""Agent A's reasoning layer: explains Agent B's findings in plain language.

The deterministic verifier (Agent B) decides; this module only explains. It produces:
  * explanation      - what was found and why it matters
  * risk_level       - low | medium | high (derived from the verdict, never from the model)
  * risk_points      - short bullet points
  * likely_causes    - plausible, non-accusatory reasons for the problems (possibilities, not findings)
  * next_steps       - what the procurement officer should do, in order
  * supplier_email   - a draft query to the supplier, only for discrepancies / missing data
  * financial_impact - money at stake, computed in code from Agent B's evidence
  * supplier_history - how this supplier's earlier invoices turned out (from Supabase)
  * fact_check       - every figure in the AI text is checked against the verified facts
  * trace            - the steps Agent A took, shown in the UI as "How Agent A reasoned"

Source of the text:
  * "llm"      - a language model (Ollama Cloud / local Ollama / any OpenAI-compatible API), prompted
                 with ONLY the verified facts; output validated (JSON schema, guardrails, fact check,
                 one self-correction round) before use;
  * "built_in" - a deterministic writer used when no model is configured or reachable, or when the
                 model's output still breaks a guardrail after the correction round.
Either way the UI shows which source wrote the text, and why the fallback was used.
"""

import json
import logging
import re
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from app.agents.procurement_coordinator.context import financial_impact
from app.llm.client import ChatClient, LLMError

logger = logging.getLogger(__name__)

RISK_BY_RESULT = {"MATCH": "low", "NEEDS_REVIEW": "medium", "DISCREPANCY_FOUND": "high", "INVALID_REQUEST": "medium"}
ACTION_LABELS = {
    "ROUTE_TO_APPROVER": "Route to an authorised approver",
    "HOLD_AND_QUERY_SUPPLIER": "Hold the invoice and query the supplier",
    "REQUEST_MISSING_INFORMATION": "Request the missing information",
    "CORRECT_AND_RESUBMIT": "Correct the request and resubmit",
}
RESULT_LABELS = {"MATCH": "Match", "DISCREPANCY_FOUND": "Discrepancy found", "NEEDS_REVIEW": "Needs review",
                 "INVALID_REQUEST": "Invalid request"}
CHECK_NAMES = {
    "invoice_number": "invoice number", "po_reference": "purchase-order reference", "supplier": "supplier name",
    "currency": "currency", "line_items": "line items", "line_item_presence": "line items",
    "line_item_quantity": "quantity", "line_item_unit_price": "unit price",
    "line_total_arithmetic": "line total", "invoice_total_arithmetic": "invoice total arithmetic",
    "total_amount": "total amount",
}
# Typical, non-accusatory explanations per failed check (used by the built-in writer).
LIKELY_CAUSES = {
    "line_item_unit_price": "The supplier may have applied a newer price list than the one agreed on the purchase order.",
    "line_item_quantity": "The delivery may have been partial or larger than ordered, or a quantity was keyed incorrectly.",
    "currency": "The invoice may have been issued by a different billing entity or region than the purchase order.",
    "supplier": "The invoice may come from a related company or trading name, or have been sent to the wrong customer.",
    "po_reference": "The invoice may quote the wrong purchase order, or belong to a different order.",
    "line_total_arithmetic": "There may be a calculation error on one of the invoice lines.",
    "invoice_total_arithmetic": "Tax or a line amount may have been added up incorrectly on the invoice.",
    "total_amount": "Charges the purchase order did not include (delivery, fees or tax) may have been added.",
    "line_items": "Items may have been added, removed or substituted compared with the order.",
    "line_item_presence": "Items may have been added, removed or substituted compared with the order.",
}
CURRENCY_SYMBOLS = {"$": "USD", "£": "GBP", "€": "EUR", "₹": "INR", "¥": "JPY"}
# Never let generated text suggest a payment decision.
_FORBIDDEN = re.compile(
    r"\b(approved?|authori[sz]ed?|cleared|ok(ay)?)\s+(it\s+)?(for\s+)?payment\b|\b(pay|release payment for)\s+(the|this)\s+invoice\b",
    re.IGNORECASE,
)
_NUMBER = re.compile(r"(?<![\w.])\d[\d,]*(?:\.\d+)?")

SYSTEM_PROMPT = """You are the reasoning module of a procurement coordinator AI agent.
A deterministic verification engine has ALREADY checked an invoice against its purchase order.
Your job is only to explain its result for a procurement officer and suggest what to do next.

Rules:
- Use ONLY the facts in the JSON you are given. Do not invent numbers, names, dates or currencies.
- Never contradict or change the verification result.
- Never say or imply that the invoice is approved, authorised or should be paid.
- Do not do arithmetic; quote figures exactly as given (evidence, financial_impact, supplier_history).
- Write money as "<amount> <currency code>" (e.g. "15.00 GBP"), never with a currency symbol.
- The explanation must name the invoice number, the PO number and the supplier, say how many checks
  passed (passed_count of total_checks) and, for problems, quote the exact differing values.
- If financial_impact shows a difference, say how much money is at stake and in which direction
  (invoice above or below the purchase order).
- supplier_history.previous_reviews counts OTHER invoices from this supplier (repeats of this invoice
  are counted separately in same_invoice_seen_before). If it is > 0, mention a pattern of discrepancies,
  or a clean record. If same_invoice_seen_before > 0, warn that this may be a duplicate submission
  and that payment history should be checked.
- risk_points: 2-4 short plain-English sentences. For a MATCH, say what was confirmed (supplier,
  currency, quantities, prices, totals) and that an authorised person still makes the payment decision.
- likely_causes: for problems, 1-3 plausible, neutral reasons, written as possibilities ("may",
  "could"); never accuse the supplier of fraud. Use [] for a MATCH.
- next_steps: 2-4 concrete actions for the procurement officer, in order, starting with a verb.
- Write a supplier_email ONLY when the result is DISCREPANCY_FOUND or NEEDS_REVIEW; otherwise null.
  Greet the supplier's accounts team, quote the exact differing values, ask for confirmation or a
  corrected invoice, and sign off as "Procurement team".
- Be concise, neutral and professional: say "difference" or "above the purchase order", never
  "overcharge" or "over-billing" (the cause is not known yet). Write for
  a person, not a program: no field names like "unit_price".

Respond with a single JSON object and nothing else:
{"explanation": "<2-4 sentences>",
 "risk_points": ["..."],
 "likely_causes": ["..."],
 "next_steps": ["..."],
 "supplier_email": {"subject": "<subject>", "body": "<email body>"} or null}"""


class _LLMOutput(BaseModel):
    explanation: str = Field(min_length=20, max_length=1500)
    risk_points: list[str] = Field(default_factory=list, max_length=6)
    likely_causes: list[str] = Field(default_factory=list, max_length=4)
    next_steps: list[str] = Field(default_factory=list, max_length=6)
    supplier_email: dict[str, str] | None = None


def _facts(verification: dict[str, Any], review: dict[str, Any], payload: dict[str, Any],
           history: dict[str, Any] | None) -> dict[str, Any]:
    invoice = payload.get("invoice") if isinstance(payload.get("invoice"), dict) else {}
    findings = verification.get("findings", [])
    passed = sum(1 for f in findings if f.get("outcome") == "PASS")
    return {
        "verification_result": verification.get("result"),
        "recommended_action": review.get("recommended_action"),
        "invoice_number": verification.get("invoice_number"),
        "po_number": verification.get("po_number"),
        "supplier_name": invoice.get("supplier_name"),
        "currency": invoice.get("currency"),
        "invoice_total": invoice.get("total_amount"),
        "issues": [
            {"check": f.get("check"), "outcome": f.get("outcome"), "explanation": f.get("explanation"),
             "evidence": f.get("evidence")}
            for f in findings if f.get("outcome") != "PASS"
        ],
        "validation_errors": verification.get("validation_errors", []),
        "passed_checks": sorted({f.get("check") for f in findings if f.get("outcome") == "PASS"}),
        "passed_count": passed,
        "total_checks": len(findings),
        "financial_impact": financial_impact(verification, payload),
        "supplier_history": history,
    }


def _parse_llm_json(text: str) -> _LLMOutput:
    match = re.search(r"\{.*\}", text, re.DOTALL)  # tolerate ```json fences or leading prose
    if not match:
        raise ValueError("no JSON object in model output")
    return _LLMOutput.model_validate(json.loads(match.group(0)))


def _texts(output: _LLMOutput) -> list[str]:
    texts = [output.explanation, *output.risk_points, *output.likely_causes, *output.next_steps]
    if output.supplier_email:
        texts += list(output.supplier_email.values())
    return texts


def _violates_guardrails(output: _LLMOutput, result: str) -> str | None:
    if any(_FORBIDDEN.search(t) for t in _texts(output)):
        return "the model suggested a payment decision"
    if output.supplier_email and result not in {"DISCREPANCY_FOUND", "NEEDS_REVIEW"}:
        return "the model drafted a supplier email for a result that does not need one"
    if output.supplier_email and not {"subject", "body"} <= set(output.supplier_email):
        return "the supplier email was incomplete"
    return None


# --- fact check -------------------------------------------------------------------------------


def _norm(number: str) -> str | None:
    try:
        return format(Decimal(number.replace(",", "").rstrip(".")).normalize(), "f")
    except (InvalidOperation, ValueError):
        return None


def _known_numbers(facts: dict[str, Any]) -> set[str]:
    known = {_norm(n) for n in _NUMBER.findall(json.dumps(facts, ensure_ascii=False))}
    known |= {str(n) for n in range(13)}  # small counts ("two issues", "3 lines", "step 1")
    return {n for n in known if n}


def fact_check(output: _LLMOutput, facts: dict[str, Any]) -> tuple[int, list[str]]:
    """Return (figures checked, problems). A problem is a number or currency symbol that does not
    appear in the verified facts - i.e. something the model made up."""
    known = _known_numbers(facts)
    text = "\n".join(_texts(output))
    figures = [n for n in _NUMBER.findall(text) if _norm(n)]
    problems = sorted({n.rstrip(".,") for n in figures if _norm(n) not in known})
    currency = (facts.get("currency") or "").upper()
    problems += [f"currency symbol '{sym}'" for sym, code in CURRENCY_SYMBOLS.items() if sym in text and code != currency]
    return len(figures), problems


# --- shaping the model output -----------------------------------------------------------------


def _frame_email(output: _LLMOutput, supplier: str) -> None:
    """Models often skip the greeting / sign-off; add them so every draft reads the same."""
    if not output.supplier_email or "body" not in output.supplier_email:
        return
    body = output.supplier_email["body"].strip()
    if not re.match(r"(dear|hello|hi)\b", body, re.IGNORECASE):
        body = f"Dear {supplier} accounts team,\n\n{body}"
    if not re.search(r"(regards|sincerely|thank you),?\s*\n?.*$", body[-120:], re.IGNORECASE):
        body += "\n\nKind regards,\nProcurement team"
    output.supplier_email["body"] = body


def _ground(output: _LLMOutput, facts: dict[str, Any]) -> None:
    """Make model output consistently specific, using only verified facts: lead with who/what/how
    many checks, top up thin or machine-like lists, and never drop a duplicate-invoice warning."""
    invoice = facts.get("invoice_number")
    if invoice and str(invoice) not in output.explanation:
        supplier = f" from {facts['supplier_name']}" if facts.get("supplier_name") else ""
        po = facts.get("po_number") or "the purchase order"
        output.explanation = (f"Invoice {invoice}{supplier} was checked against {po}: "
                              f"{facts['passed_count']} of {facts['total_checks']} checks passed. {output.explanation}")
    built_in = built_in_reasoning(facts)
    words = lambda text: {w for w in re.findall(r"[a-z]+", text.lower()) if len(w) > 4}

    def top_up(items: list[str], extras: list[str], minimum: int) -> list[str]:
        items = [p for p in items if not re.fullmatch(r"[\w.\-]+", p.strip())]  # drop bare identifiers
        if len(items) < minimum:
            for extra in extras:
                if all(len(words(extra) & words(p)) < 2 for p in items):  # skip near-duplicates
                    items.append(extra)
        return items

    output.risk_points = top_up(output.risk_points, built_in["risk_points"], 2)
    output.next_steps = top_up(output.next_steps, built_in["next_steps"], 2)
    if facts["verification_result"] == "MATCH":
        output.likely_causes = []
    elif not output.likely_causes:
        output.likely_causes = built_in["likely_causes"]
    history = facts.get("supplier_history") or {}
    if history.get("same_invoice_seen_before") and "duplicate" not in " ".join(_texts(output)).lower():
        output.risk_points.insert(0, _duplicate_point(facts))
    output.risk_points = output.risk_points[:5]
    output.next_steps = output.next_steps[:5]


# --- built-in writer ---------------------------------------------------------------------


def _evidence_text(evidence: dict[str, Any]) -> str:
    keys = [k for k in evidence if k not in {"sku", "missing_fields", "calculation"}]
    parts = [f"{k.replace('purchase_order.', 'PO ').replace('invoice.', 'invoice ').replace('_', ' ')}: {evidence[k]}"
             for k in keys]
    return "; ".join(parts)


def _human_field(path: str) -> str:
    """'purchase_order.currency' -> 'the currency on the purchase order'."""
    side, _, name = path.partition(".")
    if not name:
        return path.replace("_", " ")
    name = re.sub(r"\[(.+?)\]", r" for ", name).replace(".", " ").replace("_", " ")
    name = name.replace("po reference", "PO reference").replace("po number", "PO number")
    return f"the {name} on the {side.replace('_', ' ')}"


def _duplicate_point(facts: dict[str, Any]) -> str:
    times = facts["supplier_history"]["same_invoice_seen_before"]
    return (f"Invoice {facts.get('invoice_number')} was already reviewed {times} time(s) before: check it is not a "
            "duplicate submission and has not been paid already.")


def _impact_sentence(facts: dict[str, Any]) -> str | None:
    impact = facts.get("financial_impact") or {}
    if impact.get("direction") == "different_currency":
        return (f"The totals cannot be compared: the invoice is in {impact.get('currency')} and the purchase order "
                f"in {impact.get('po_currency')}.")
    if impact.get("direction") in {"over", "under"}:
        where = "above" if impact["direction"] == "over" else "below"
        return (f"The invoice total ({impact['invoice_total']} {impact.get('currency') or ''}) is "
                f"{impact['difference']} {impact.get('currency') or ''} {where} the purchase order "
                f"({impact['po_total']}).").replace("  ", " ").replace(" )", ")")
    return None


def _history_sentence(facts: dict[str, Any]) -> str | None:
    history = facts.get("supplier_history") or {}
    n = history.get("previous_reviews") or 0
    if not n:
        return None
    if history.get("discrepancies"):
        return (f"{history['discrepancies']} of this supplier's last {n} reviewed invoices also had discrepancies, "
                "so the pattern is worth raising with the supplier.")
    return f"This supplier's last {n} reviewed invoices had no discrepancies."


def built_in_reasoning(facts: dict[str, Any]) -> dict[str, Any]:
    result = facts["verification_result"]
    invoice = facts.get("invoice_number") or "the invoice"
    po = facts.get("po_number") or "the purchase order"
    supplier = facts.get("supplier_name") or "the supplier"
    mismatches = [i for i in facts["issues"] if i["outcome"] == "MISMATCH"]
    missing = [i for i in facts["issues"] if i["outcome"] == "MISSING_INFORMATION"]
    email = None
    causes: list[str] = []

    if result == "MATCH":
        explanation = (f"Invoice {invoice} from {supplier} is consistent with {po}: all "
                       f"{facts['passed_count']} checks passed, including supplier, currency, quantities, "
                       "prices and totals. It can be routed to an authorised approver, who makes the payment decision.")
        points = ["No discrepancies or missing information were found.",
                  "Approval still requires an authorised person."]
        steps = [f"Send invoice {invoice} to an authorised approver together with this report.",
                 "Keep the verification record for the audit trail."]
    elif result == "DISCREPANCY_FOUND":
        names = sorted({CHECK_NAMES.get(i["check"], i["check"]) for i in mismatches})
        explanation = (f"Invoice {invoice} from {supplier} does not agree with {po} on: {', '.join(names)}. "
                       "These differences must be resolved with the supplier before the invoice can move to approval.")
        impact = _impact_sentence(facts)
        if impact:
            explanation += f" {impact}"
        points = [f"{CHECK_NAMES.get(i['check'], i['check']).capitalize()}: {i['explanation']}"
                  + (f" ({_evidence_text(i.get('evidence') or {})})" if _evidence_text(i.get('evidence') or {}) else "")
                  for i in mismatches][:5]
        if missing:
            points.append(f"{len(missing)} check(s) also lacked information.")
        causes = list(dict.fromkeys(LIKELY_CAUSES[i["check"]] for i in mismatches if i["check"] in LIKELY_CAUSES))[:3]
        steps = [f"Put invoice {invoice} on hold so it is not scheduled for payment.",
                 f"Send the drafted query to {supplier} and ask for confirmation or a corrected invoice.",
                 "Run the check again when the corrected invoice or confirmation arrives."]
        lines = "\n".join(f"- {p}" for p in points)
        email = {
            "subject": f"Query on invoice {invoice} ({po})",
            "body": (f"Dear {supplier} accounts team,\n\nWe are reviewing invoice {invoice} against purchase order {po} "
                     f"and found the following differences:\n\n{lines}\n\nCould you please confirm the correct details "
                     "or send a corrected invoice? We will hold the invoice until this is resolved.\n\nKind regards,\n"
                     "Procurement team"),
        }
    elif result == "NEEDS_REVIEW":
        fields = [", ".join(_human_field(f) for f in (i.get("evidence") or {}).get("missing_fields", [])) or i["check"]
                  for i in missing]
        explanation = (f"Invoice {invoice} could not be fully verified against {po} because some information is "
                       f"missing: {'; '.join(fields)}. No discrepancies were confirmed, but the checks that depend on "
                       "this information could not run.")
        points = [f"Missing: {f}" for f in fields][:5]
        causes = ["The document may be incomplete, or some values could not be read from the scan."]
        steps = [f"Ask {supplier} for the missing details (an email is drafted).",
                 "Fill in the missing values and run the check again."]
        email = {
            "subject": f"Missing information for invoice {invoice}",
            "body": (f"Dear {supplier} accounts team,\n\nTo complete our review of invoice {invoice} we need the "
                     "following information:\n\n" + "\n".join(f"- {f}" for f in fields)
                     + "\n\nCould you please provide it at your earliest convenience?\n\nKind regards,\nProcurement team"),
        }
    else:  # INVALID_REQUEST
        errors = [f"{e.get('field')}: {e.get('message')}" for e in facts.get("validation_errors", [])]
        explanation = ("The request was rejected before verification because some values are malformed. "
                       "Correct the fields listed below and resubmit.")
        points = errors[:5]
        steps = ["Correct the fields listed and submit the review again."]

    history_point = _history_sentence(facts)
    if history_point and result != "INVALID_REQUEST":
        points.append(history_point)
    if (facts.get("supplier_history") or {}).get("same_invoice_seen_before"):
        points.insert(0, _duplicate_point(facts))
        steps.insert(0, f"Check the payment history to confirm invoice {invoice} has not been paid already.")
    return {"explanation": explanation, "risk_points": points[:5], "likely_causes": causes, "next_steps": steps,
            "supplier_email": email}


# --- trace -----------------------------------------------------------------------------------------


def _history_detail(history: dict[str, Any] | None, note: str) -> str:
    if history is None:
        return note
    if not history["previous_reviews"]:
        detail = "No other invoices from this supplier reviewed yet."
    else:
        detail = (f"{history['previous_reviews']} other invoice(s) reviewed: {history['matches']} match, "
                  f"{history['discrepancies']} with discrepancies.")
    if history.get("same_invoice_seen_before"):
        detail += f" Same invoice number seen {history['same_invoice_seen_before']} time(s) before."
    return detail


def _trace(facts: dict[str, Any], history_note: str, writer: str, check: str | None) -> list[dict[str, str]]:
    impact = facts.get("financial_impact")
    result = facts["verification_result"]
    steps = [
        {"step": "Delegated the check to Agent B",
         "detail": f"{facts['total_checks']} rules ran over A2A: {facts['passed_count']} passed. "
                   f"Verdict: {RESULT_LABELS.get(result, result)}."},
        {"step": "Measured the money at stake",
         "detail": _impact_sentence(facts) or ("Invoice and purchase-order totals agree."
                                               if impact and impact.get("direction") == "equal"
                                               else "Totals were not available on both documents.")},
        {"step": "Looked up the supplier's history", "detail": _history_detail(facts.get("supplier_history"), history_note)},
        {"step": "Chose the next action",
         "detail": f"{ACTION_LABELS.get(facts['recommended_action'], facts['recommended_action'])} "
                   f"(risk: {RISK_BY_RESULT.get(result, 'medium')}). Fixed rules decide this, not the AI."},
        {"step": "Wrote the explanation", "detail": writer},
    ]
    if check:
        steps.append({"step": "Fact-checked the AI text", "detail": check})
    return steps


# --- public entry point ------------------------------------------------------------------------


async def _ask(llm: ChatClient, facts: dict[str, Any]) -> tuple[_LLMOutput | None, str, dict[str, Any]]:
    """Ask the model, validate, fact-check and give it one chance to correct itself.
    Returns (output or None, reason it was rejected, fact_check summary)."""
    result = facts["verification_result"]
    messages = [{"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(facts, ensure_ascii=False)}]
    problem = ""
    for attempt in (1, 2):
        text = await llm.complete(messages)
        output = _parse_llm_json(text)
        if result not in {"DISCREPANCY_FOUND", "NEEDS_REVIEW"}:
            output.supplier_email = None  # models sometimes draft one anyway; it is simply dropped
        problem = _violates_guardrails(output, result) or ""
        checked, unverified = (0, []) if problem else fact_check(output, facts)
        if not problem and not unverified:
            return output, "", {"status": "verified" if attempt == 1 else "corrected", "figures": checked,
                                "attempts": attempt}
        if not problem:
            problem = f"figures not found in the verified data: {', '.join(unverified[:6])}"
        logger.warning("LLM output failed check (attempt %s): %s", attempt, problem)
        messages += [{"role": "assistant", "content": text},
                     {"role": "user", "content": f"Your answer was rejected: {problem}. Rewrite the same JSON using "
                                                 "only figures and currency codes that appear in the facts, and follow "
                                                 "every rule."}]
    return None, problem, {"status": "failed", "attempts": 2}


async def reason(llm: ChatClient | None, verification: dict, review: dict, payload: dict,
                 disabled_reason: str = "AI reasoning is not configured.",
                 history: dict[str, Any] | None = None,
                 history_note: str = "Not available (storage is off).") -> dict[str, Any]:
    facts = _facts(verification, review, payload, history)
    result = facts["verification_result"]
    base = {"risk_level": RISK_BY_RESULT.get(result, "medium"),
            "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "financial_impact": facts["financial_impact"], "supplier_history": history}

    fallback_reason = disabled_reason
    if llm is not None:
        try:
            output, problem, check = await _ask(llm, facts)
            if output is not None:
                _frame_email(output, facts.get("supplier_name") or "the supplier")
                _ground(output, facts)
                model = llm.last_model
                logger.info("Reasoning written by %s (fact check: %s)", model, check["status"])
                detail = (f"All {check['figures']} figures match the verified data."
                          if check["status"] == "verified" else
                          f"The first draft quoted a figure not in the data; the model corrected it ({check['figures']} figures verified).")
                return {**base, "source": "llm", "model": model, "provider": llm.label, "fact_check": check,
                        "trace": _trace(facts, history_note, f"{model} on {llm.label}, using only the verified facts above.", detail),
                        **output.model_dump()}
            fallback_reason = f"AI output discarded: {problem}."
        except LLMError as exc:
            fallback_reason = str(exc)
        except (ValueError, ValidationError) as exc:
            fallback_reason = "The model's answer was not valid JSON in the expected format."
            logger.warning("LLM output unparseable: %s", type(exc).__name__)

    return {**base, "source": "built_in", "model": None, "fallback_reason": fallback_reason,
            "trace": _trace(facts, history_note, f"Built-in writer ({fallback_reason.rstrip('.')}).", None),
            **built_in_reasoning(facts)}

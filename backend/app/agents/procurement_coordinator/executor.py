"""A2A executor for Agent A: receive a review request, delegate to Agent B, respond."""

import asyncio
import logging
from typing import Any

from a2a.helpers import get_data_parts, new_data_part, new_task_from_user_message, new_text_part
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types import TaskState

from app.agents.procurement_coordinator.agent_card import JSON_MEDIA_TYPE
from app.agents.procurement_coordinator.context import supplier_history
from app.agents.procurement_coordinator.coordination import (
    UnusableResultError,
    build_procurement_review,
    is_rejection,
)
from app.agents.procurement_coordinator.delegate import DelegationError, VerificationDelegate
from app.agents.procurement_coordinator.reasoning import reason
from app.llm.client import ChatClient

logger = logging.getLogger(__name__)

REVIEW_ARTIFACT_NAME = "procurement_review"
HISTORY_TIMEOUT_SECONDS = 5.0
_FINAL_SPECIALIST_STATES = {"TASK_STATE_COMPLETED", "TASK_STATE_REJECTED"}


class ProcurementCoordinatorExecutor(AgentExecutor):
    def __init__(
        self, delegate: VerificationDelegate, llm: ChatClient | None = None, llm_disabled_reason: str = "",
        repository: Any = None,
    ) -> None:
        self._delegate = delegate
        self._llm = llm
        self._llm_disabled_reason = llm_disabled_reason or "AI reasoning is not configured."
        self._repository = repository  # read-only use: the supplier's earlier reviews

    async def _history(self, payload: dict, verification: dict, task_id: str) -> tuple[dict | None, str]:
        if self._repository is None:
            return None, "Not available (storage is off)."
        invoice = payload.get("invoice") if isinstance(payload.get("invoice"), dict) else {}
        try:
            history = await asyncio.wait_for(asyncio.to_thread(
                supplier_history, self._repository, invoice.get("supplier_name"), task_id,
                verification.get("invoice_number")), HISTORY_TIMEOUT_SECONDS)
        except TimeoutError:
            return None, "Skipped: the database did not answer in time."
        if history is None:
            return None, ("Not available (no supplier name on the invoice)." if not invoice.get("supplier_name")
                          else "Not available (the database lookup failed).")
        return history, ""

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        message = context.message
        if message is None:
            raise ValueError("SendMessage request has no message")

        task = context.current_task
        if task is None:
            task = new_task_from_user_message(message)
            await event_queue.enqueue_event(task)
        updater = TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id)

        payloads = get_data_parts(message.parts)
        if len(payloads) != 1 or not isinstance(payloads[0], dict):
            await updater.reject(
                updater.new_agent_message(
                    [new_text_part("Expected exactly one JSON data part with 'invoice' and 'purchase_order'.")]
                )
            )
            return

        await updater.update_status(
            TaskState.TASK_STATE_WORKING,
            updater.new_agent_message([new_text_part("Delegating invoice verification to the specialist agent.")]),
        )
        try:
            delegation = await self._delegate.delegate(payloads[0])
            if delegation.task_state not in _FINAL_SPECIALIST_STATES:
                raise UnusableResultError(
                    f"specialist task {delegation.task_id} ended in {delegation.task_state}"
                    + (f": {delegation.status_message}" if delegation.status_message else "")
                )
            review = build_procurement_review(
                delegation.result,
                {
                    "agent": delegation.agent_name,
                    "agent_url": delegation.agent_url,
                    "skill": delegation.skill_id,
                    "task_id": delegation.task_id,
                    "task_state": delegation.task_state,
                    "persistence": delegation.persistence,
                },
            )
        except (DelegationError, UnusableResultError) as exc:
            logger.error("Task %s: delegation failed: %s", task.id, exc)
            await updater.failed(
                updater.new_agent_message([new_text_part(f"Invoice verification could not be completed: {exc}")])
            )
            return

        # Explain the verified result (LLM if available, built-in writer otherwise).
        # Reasoning is optional: a failure here must never fail the review.
        try:
            history, history_note = await self._history(payloads[0], delegation.result or {}, delegation.task_id)
            review["reasoning"] = await reason(
                self._llm, delegation.result or {}, review, payloads[0], self._llm_disabled_reason,
                history=history, history_note=history_note,
            )
        except Exception as exc:  # defensive
            logger.error("Task %s: reasoning failed (%s); returning review without it", task.id, type(exc).__name__)
            review["reasoning"] = None

        logger.info(
            "Task %s: specialist task %s -> %s -> %s",
            task.id, delegation.task_id, review["verification_result"], review["recommended_action"],
        )
        await updater.add_artifact(
            parts=[new_data_part(review, media_type=JSON_MEDIA_TYPE)],
            name=REVIEW_ARTIFACT_NAME,
        )
        status_message = updater.new_agent_message([new_text_part(review["summary"])])
        if is_rejection(review):
            await updater.reject(status_message)
        else:
            await updater.complete(status_message)

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        if task is None:
            return
        await TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id).cancel()

"""A2A transport adapter: turns A2A messages into verification calls."""

import asyncio
import logging
from typing import Any

from a2a.helpers import get_data_parts, new_data_part, new_task_from_user_message, new_text_part
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater

from app.agents.invoice_verifier.agent_card import JSON_MEDIA_TYPE
from app.agents.invoice_verifier.verification import verify_payload
from app.models.invoice import OverallResult, ValidationIssue, VerificationResult
from app.persistence.reviews import PersistenceError, ReviewRepository, build_review_row

logger = logging.getLogger(__name__)

RESULT_ARTIFACT_NAME = "verification_result"


class InvoiceVerificationExecutor(AgentExecutor):
    """Runs the deterministic verifier for each incoming A2A task.

    When a ``ReviewRepository`` is supplied, each final result (COMPLETED or
    REJECTED) is saved to Supabase before the task's final status is published.
    A persistence failure does not discard the verification: the result is still
    returned, and the artifact's ``metadata.persistence`` reports the failure.
    """

    def __init__(self, review_repository: ReviewRepository | None = None) -> None:
        self._reviews = review_repository

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        message = context.message
        if message is None:
            raise ValueError("SendMessage request has no message")

        task = context.current_task
        if task is None:
            task = new_task_from_user_message(message)
            await event_queue.enqueue_event(task)

        updater = TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id)
        await updater.start_work()

        payloads = get_data_parts(message.parts)
        try:
            result = _verify_message_parts(payloads)
        except Exception as exc:  # defensive: never leak internals or payloads to the caller
            logger.error("Verification failed for task %s: %s", task.id, type(exc).__name__)
            await updater.failed(
                updater.new_agent_message([new_text_part("Internal error while verifying invoice.")])
            )
            return

        logger.info("Task %s finished with result %s", task.id, result.result.value)
        payload = payloads[0] if len(payloads) == 1 else None
        persistence = await self._persist(task.id, task.context_id, result, payload)
        await updater.add_artifact(
            parts=[new_data_part(result.model_dump(mode="json"), media_type=JSON_MEDIA_TYPE)],
            name=RESULT_ARTIFACT_NAME,
            metadata={"persistence": persistence},
        )
        status_message = updater.new_agent_message([new_text_part(result.summary)])
        if result.result is OverallResult.INVALID_REQUEST:
            await updater.reject(status_message)
        else:
            await updater.complete(status_message)

    async def _persist(
        self, task_id: str, context_id: str, result: VerificationResult, payload: Any
    ) -> dict[str, Any]:
        """Save the final review; return a status dict for the artifact metadata."""
        if self._reviews is None:
            return {"status": "disabled", "detail": "Supabase persistence is not configured on this agent."}
        try:
            row = build_review_row(task_id=task_id, context_id=context_id, result=result, payload=payload)
            # The Supabase client is synchronous; keep the event loop free.
            saved = await asyncio.to_thread(self._reviews.save, row)
        except PersistenceError as exc:
            logger.error("Review for task %s was NOT saved: %s", task_id, exc)
            return {"status": "failed", "detail": str(exc)}
        except Exception as exc:  # defensive: unexpected client/mapping errors
            logger.error("Review for task %s was NOT saved: unexpected %s", task_id, type(exc).__name__)
            return {"status": "failed", "detail": f"Unexpected persistence error ({type(exc).__name__})."}
        logger.info("Review for task %s saved (review id %s)", task_id, saved.get("id"))
        return {"status": "saved", "review_id": str(saved.get("id")), "table": "verification_reviews"}

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        # Verification is synchronous and short-lived, but honour the contract.
        task = context.current_task
        if task is None:
            return
        updater = TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id)
        await updater.cancel()


def _verify_message_parts(payloads: list) -> VerificationResult:
    if len(payloads) != 1:
        return VerificationResult(
            result=OverallResult.INVALID_REQUEST,
            summary="Request is malformed; no verification checks were run.",
            validation_errors=[
                ValidationIssue(
                    field="message.parts",
                    message=f"expected exactly one JSON data part, received {len(payloads)}",
                )
            ],
        )
    return verify_payload(payloads[0])

"""Delegates invoice verification to the specialist agent over A2A.

Agent A treats Agent B as an independent A2A participant: it discovers it
through its Agent Card, checks the advertised skill, sends a task over
JSON-RPC and reads the result artifact. It never imports Agent B's code.
"""

import logging
from dataclasses import dataclass, field

import httpx
from a2a.client import AgentCardResolutionError
from a2a.types import TaskState
from a2a.utils.errors import A2AError

from app.protocol.client import (
    artifact_data,
    artifact_metadata,
    discover,
    find_skill,
    open_client,
    status_text,
    submit_and_retrieve,
)

logger = logging.getLogger(__name__)

# The contract Agent A expects the specialist to advertise and return.
VERIFICATION_SKILL_ID = "verify_invoice_against_po"
VERIFICATION_ARTIFACT = "verification_result"


class DelegationError(Exception):
    """The specialist could not be reached or did not honour its advertised contract."""


@dataclass
class Delegation:
    agent_name: str
    agent_url: str
    skill_id: str
    task_id: str
    task_state: str  # e.g. "TASK_STATE_COMPLETED"
    result: dict | None
    status_message: str = ""
    persistence: dict = field(default_factory=dict)


class VerificationDelegate:
    def __init__(self, agent_b_url: str, timeout_seconds: float = 30.0) -> None:
        self.agent_b_url = agent_b_url
        self.timeout_seconds = timeout_seconds

    async def delegate(self, payload: dict) -> Delegation:
        timeout = httpx.Timeout(self.timeout_seconds, connect=5.0)
        try:
            async with httpx.AsyncClient(timeout=timeout) as http:
                card = await discover(http, self.agent_b_url)
                skill = find_skill(card, VERIFICATION_SKILL_ID)
                if skill is None:
                    raise DelegationError(
                        f"agent '{card.name}' at {self.agent_b_url} does not advertise skill '{VERIFICATION_SKILL_ID}'"
                    )
                logger.info("Discovered '%s' v%s; delegating skill '%s'", card.name, card.version, skill.id)
                client = await open_client(card, http)
                try:
                    task = await submit_and_retrieve(client, payload, self.timeout_seconds)
                finally:
                    await client.close()
        except AgentCardResolutionError as exc:
            raise DelegationError(f"could not discover the verification agent at {self.agent_b_url}") from exc
        except httpx.HTTPError as exc:
            raise DelegationError(f"network error talking to the verification agent ({type(exc).__name__})") from exc
        except (A2AError, TimeoutError, RuntimeError) as exc:
            raise DelegationError(f"verification task did not complete ({type(exc).__name__}: {exc})") from exc

        logger.info("Specialist task %s finished: %s", task.id, TaskState.Name(task.status.state))
        return Delegation(
            agent_name=card.name,
            agent_url=self.agent_b_url,
            skill_id=skill.id,
            task_id=task.id,
            task_state=TaskState.Name(task.status.state),
            result=artifact_data(task, VERIFICATION_ARTIFACT),
            status_message=status_text(task),
            persistence=artifact_metadata(task, VERIFICATION_ARTIFACT).get("persistence", {}),
        )

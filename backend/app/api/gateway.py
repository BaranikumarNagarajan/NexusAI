"""The API's connection to the agents: A2A only, through the shared client helpers."""

import asyncio
import logging
from dataclasses import dataclass

import httpx
from a2a.client import AgentCardResolutionError
from a2a.types import Task, TaskState
from a2a.utils.errors import A2AError

from app.protocol.client import artifact_data, discover, open_client, status_text, submit_and_retrieve

logger = logging.getLogger(__name__)

REVIEW_ARTIFACT = "procurement_review"


class AgentUnavailableError(Exception):
    """Agent A could not be reached or did not answer in time."""


@dataclass
class CoordinatorOutcome:
    task: Task
    review: dict | None  # Agent A's procurement_review artifact, if any

    @property
    def state(self) -> str:
        return TaskState.Name(self.task.status.state)

    @property
    def message(self) -> str:
        return status_text(self.task)


class AgentGateway:
    def __init__(self, http: httpx.AsyncClient, coordinator_url: str, verifier_url: str, timeout_seconds: float):
        self._http = http
        self.coordinator_url = coordinator_url
        self.verifier_url = verifier_url
        self._timeout = timeout_seconds

    async def submit_review(self, payload: dict) -> CoordinatorOutcome:
        """Send one review request to Agent A over A2A and wait for its final task."""
        try:
            card = await discover(self._http, self.coordinator_url)
            # Not closed here: closing an SDK client closes its httpx client, and self._http is
            # shared across requests and owned by the app lifespan (closed on shutdown).
            client = await open_client(card, self._http)
            task = await submit_and_retrieve(client, payload, self._timeout)
        except (AgentCardResolutionError, httpx.ConnectError) as exc:
            raise AgentUnavailableError("The Procurement Coordinator (Agent A) is not reachable.") from exc
        except (httpx.TimeoutException, TimeoutError) as exc:
            raise AgentUnavailableError("The Procurement Coordinator (Agent A) did not respond in time.") from exc
        except (httpx.HTTPError, A2AError) as exc:
            raise AgentUnavailableError(f"A2A error talking to the Procurement Coordinator ({type(exc).__name__}).") from exc
        logger.info("Agent A task %s finished: %s", task.id, TaskState.Name(task.status.state))
        return CoordinatorOutcome(task=task, review=artifact_data(task, REVIEW_ARTIFACT))

    async def coordinator_info(self) -> dict:
        """Agent A's live status from GET / (reasoning mode); never raises."""
        try:
            response = await asyncio.wait_for(self._http.get(f"{self.coordinator_url}/"), timeout=5)
            return response.json() if response.status_code == 200 else {}
        except Exception:
            return {}

    async def agent_cards(self) -> list[dict]:
        """Discover both agents concurrently (for status pages); never raises."""

        async def fetch(role: str, url: str) -> dict:
            try:
                card = await asyncio.wait_for(discover(self._http, url), timeout=5)
            except Exception as exc:  # offline agents are reported, not raised
                return {"role": role, "url": url, "online": False, "error": type(exc).__name__}
            return {
                "role": role,
                "url": url,
                "online": True,
                "name": card.name,
                "version": card.version,
                "skills": [{"id": s.id, "name": s.name, "description": s.description} for s in card.skills],
            }

        return list(
            await asyncio.gather(
                fetch("coordinator", self.coordinator_url),
                fetch("verifier", self.verifier_url),
            )
        )

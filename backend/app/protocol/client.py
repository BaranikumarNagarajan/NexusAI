"""Shared A2A client helpers (official a2a-sdk).

Used by Agent A to delegate work to Agent B, and by the command-line scripts.
Everything here is generic A2A: discovery, sending one JSON task, waiting for a
final state with GetTask, and reading artifacts. It knows nothing about invoices.
"""

import asyncio
import time

import httpx
from a2a.client import A2ACardResolver, Client, ClientConfig, create_client
from a2a.helpers import get_data_parts, new_data_message
from a2a.types import AgentCard, AgentSkill, GetTaskRequest, Role, SendMessageRequest, Task, TaskState
from google.protobuf.json_format import MessageToDict

TERMINAL_STATES = {
    TaskState.TASK_STATE_COMPLETED,
    TaskState.TASK_STATE_FAILED,
    TaskState.TASK_STATE_CANCELED,
    TaskState.TASK_STATE_REJECTED,
}
JSON_MEDIA_TYPE = "application/json"


async def discover(http: httpx.AsyncClient, base_url: str) -> AgentCard:
    """Fetch an agent's card from its well-known discovery endpoint."""
    return await A2ACardResolver(httpx_client=http, base_url=base_url).get_agent_card()


def find_skill(card: AgentCard, skill_id: str) -> AgentSkill | None:
    return next((skill for skill in card.skills if skill.id == skill_id), None)


async def open_client(card: AgentCard, http: httpx.AsyncClient) -> Client:
    """Create an SDK client for the transport the card advertises (non-streaming).

    Note: the SDK's ``client.close()`` also closes ``http``. Only call it when ``http``
    is not shared with other work.
    """
    return await create_client(card, client_config=ClientConfig(streaming=False, httpx_client=http))


async def submit_and_retrieve(client: Client, payload: dict, timeout_seconds: float = 30.0) -> Task:
    """Send one JSON data-part task and return the final task fetched via GetTask."""
    message = new_data_message(payload, media_type=JSON_MEDIA_TYPE, role=Role.ROLE_USER)
    task: Task | None = None
    async for response in client.send_message(SendMessageRequest(message=message)):
        if response.HasField("task"):
            task = response.task
    if task is None:
        raise RuntimeError("agent replied without a task")

    # Non-streaming SendMessage normally returns once the task is terminal; poll
    # with GetTask anyway so callers also work if an agent answers early.
    deadline = time.monotonic() + timeout_seconds
    task = await client.get_task(GetTaskRequest(id=task.id))
    while task.status.state not in TERMINAL_STATES:
        if time.monotonic() > deadline:
            raise TimeoutError(f"task {task.id} did not finish within {timeout_seconds}s")
        await asyncio.sleep(0.5)
        task = await client.get_task(GetTaskRequest(id=task.id))
    return task


def artifact_data(task: Task, name: str) -> dict | None:
    """First JSON data part of the named artifact, or None."""
    for artifact in task.artifacts:
        if artifact.name == name:
            data = get_data_parts(artifact.parts)
            if data and isinstance(data[0], dict):
                return data[0]
    return None


def artifact_metadata(task: Task, name: str) -> dict:
    for artifact in task.artifacts:
        if artifact.name == name and artifact.HasField("metadata"):
            return MessageToDict(artifact.metadata)
    return {}


def status_text(task: Task) -> str:
    """Text of the task's final status message, if any."""
    if task.status.HasField("message"):
        return " ".join(p.text for p in task.status.message.parts if p.HasField("text"))
    return ""

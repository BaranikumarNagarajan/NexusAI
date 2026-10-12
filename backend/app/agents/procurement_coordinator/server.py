"""Builds the A2A Starlette application for the Procurement Coordinator."""

from typing import Any

from a2a.types import AgentCard
from starlette.applications import Starlette

from app.agents.procurement_coordinator.agent_card import build_agent_card
from app.agents.procurement_coordinator.delegate import VerificationDelegate
from app.agents.procurement_coordinator.executor import ProcurementCoordinatorExecutor
from app.llm.client import ChatClient
from app.protocol.server import build_a2a_app


def build_app(
    public_url: str,
    agent_b_url: str,
    delegation_timeout_seconds: float = 30.0,
    max_request_bytes: int = 256 * 1024,
    llm: ChatClient | None = None,
    llm_disabled_reason: str = "",
    repository: Any = None,
) -> tuple[Starlette, AgentCard]:
    agent_card = build_agent_card(public_url)
    delegate = VerificationDelegate(agent_b_url, delegation_timeout_seconds)
    executor = ProcurementCoordinatorExecutor(delegate, llm, llm_disabled_reason, repository)

    def info() -> dict:
        if llm is None:
            return {"reasoning": {"mode": "built_in", "model": None, "provider": None,
                                  "detail": llm_disabled_reason or "AI reasoning is not configured."}}
        ready = llm.last_status == "ready"
        return {"reasoning": {"mode": "llm" if ready else "built_in", "model": llm.model, "provider": llm.label,
                              "detail": f"{llm.label} model ready." if ready else llm.friendly(llm.last_status)}}

    app = build_a2a_app(agent_card, executor, max_request_bytes, info=info)
    return app, agent_card

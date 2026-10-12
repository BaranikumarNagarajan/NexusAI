"""Builds the A2A Starlette application for the Invoice Verification Specialist."""

from a2a.types import AgentCard
from starlette.applications import Starlette

from app.agents.invoice_verifier.agent_card import build_agent_card
from app.agents.invoice_verifier.executor import InvoiceVerificationExecutor
from app.persistence.reviews import ReviewRepository
from app.protocol.server import build_a2a_app


def build_app(
    public_url: str,
    max_request_bytes: int = 256 * 1024,
    review_repository: ReviewRepository | None = None,
) -> tuple[Starlette, AgentCard]:
    agent_card = build_agent_card(public_url)
    app = build_a2a_app(agent_card, InvoiceVerificationExecutor(review_repository), max_request_bytes)
    return app, agent_card

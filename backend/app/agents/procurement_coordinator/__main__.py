"""Run Agent A locally: ``python -m app.agents.procurement_coordinator``."""

import asyncio
import logging

import uvicorn

from app.agents.procurement_coordinator.server import build_app
from app.config import ConfigurationError, load_agent_a_settings, load_llm_settings, load_supabase_settings
from app.llm.client import ChatClient

logger = logging.getLogger(__name__)


def _llm_client() -> tuple[ChatClient | None, str]:
    """Create the reasoning-model client and report its readiness once at startup."""
    settings = load_llm_settings()
    if not settings.enabled:
        missing_key = {"huggingface": "No Hugging Face token configured (HF_TOKEN).",
                       "openai": "No LLM_API_KEY configured.",
                       "ollama": "No OLLAMA_API_KEY configured for Ollama Cloud."}.get(settings.provider)
        reason = missing_key if missing_key and not settings.token else "AI reasoning disabled in configuration."
        logger.info("AI reasoning: %s Using the built-in explanation writer.", reason)
        return None, reason
    client = ChatClient(settings.provider, settings.model, base_url=settings.base_url, token=settings.token,
                        timeout_seconds=settings.timeout_seconds, fallback_model=settings.fallback_model)
    status = asyncio.run(client.probe())
    if status == "ready":
        backup = f" (fallback: {settings.fallback_model})" if settings.fallback_model else ""
        logger.info("AI reasoning: %s model %s is ready%s.", client.label, settings.model, backup)
        return client, ""
    logger.warning("AI reasoning: %s Falling back to the built-in writer until this is resolved.", client.friendly(status))
    return client, client.friendly(status)


def _repository():
    """Read access to saved reviews, for the supplier-history context. Optional."""
    try:
        settings = load_supabase_settings()
    except ConfigurationError as exc:
        logger.warning("Supplier history disabled: %s", exc)
        return None
    if settings is None:
        logger.info("Supplier history disabled: Supabase is not configured.")
        return None
    from app.persistence.reviews import create_review_repository
    return create_review_repository(settings)


def main() -> None:
    settings = load_agent_a_settings()
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    # The SDK logs the full Agent Card on every discovery; our delegate logs a one-line summary instead.
    logging.getLogger("a2a.client.card_resolver").setLevel(logging.WARNING)

    llm, llm_reason = _llm_client()
    app, card = build_app(
        settings.public_url,
        settings.agent_b_url,
        settings.delegation_timeout_seconds,
        settings.max_request_bytes,
        llm=llm,
        llm_disabled_reason=llm_reason,
        repository=_repository(),
    )
    logger.info("Starting '%s' - Agent Card at %s/.well-known/agent-card.json", card.name, settings.public_url)
    logger.info("Delegates invoice verification to the agent at %s", settings.agent_b_url)
    uvicorn.run(app, host=settings.host, port=settings.port, timeout_keep_alive=5, log_level="info")


if __name__ == "__main__":
    main()

"""Run Agent B locally: ``python -m app.agents.invoice_verifier``."""

import logging
import sys
from urllib.parse import urlparse

import uvicorn

from app.agents.invoice_verifier.server import build_app
from app.config import ConfigurationError, load_agent_b_settings, load_supabase_settings
from app.persistence.reviews import PersistenceError, ReviewRepository, create_review_repository

logger = logging.getLogger(__name__)


def _review_repository() -> ReviewRepository | None:
    """Create the Supabase repository, or None (loudly) when it is not configured."""
    try:
        supabase = load_supabase_settings()
    except ConfigurationError as exc:
        logger.error("Invalid Supabase configuration: %s", exc)
        sys.exit(1)
    if supabase is None:
        logger.warning(
            "Supabase persistence DISABLED: SUPABASE_URL and SUPABASE_SECRET_KEY are not set. "
            "Verification works, but reviews are not saved."
        )
        return None

    repository = create_review_repository(supabase)
    host = urlparse(supabase.url).hostname
    try:
        repository.check_ready()
    except PersistenceError as exc:
        # Keep serving verifications; each task will report persistence status "failed".
        logger.error("Supabase persistence enabled for %s but NOT READY: %s", host, exc)
    else:
        logger.info("Supabase persistence enabled for %s (key type: %s)", host, supabase.key_type)
    return repository


def main() -> None:
    settings = load_agent_b_settings()
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # httpx logs every request URL at INFO; keep Supabase traffic out of the default log.
    logging.getLogger("httpx").setLevel(logging.WARNING)

    app, card = build_app(settings.public_url, settings.max_request_bytes, _review_repository())
    logger.info(
        "Starting '%s' - Agent Card at %s/.well-known/agent-card.json", card.name, settings.public_url
    )
    uvicorn.run(app, host=settings.host, port=settings.port, timeout_keep_alive=5, log_level="info")


if __name__ == "__main__":
    main()

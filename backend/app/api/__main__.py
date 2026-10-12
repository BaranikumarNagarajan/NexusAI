"""Run the NexusAI API locally: ``python -m app.api``."""

import logging
import sys
from urllib.parse import urlparse

import uvicorn

from app.api.main import create_app
from app.config import ConfigurationError, load_api_settings, load_supabase_settings
from app.ingestion.ocr import find_tesseract
from app.persistence.reviews import PersistenceError, ReviewRepository, create_review_repository

logger = logging.getLogger(__name__)


def _review_repository() -> ReviewRepository | None:
    try:
        supabase = load_supabase_settings()
    except ConfigurationError as exc:
        logger.error("Invalid Supabase configuration: %s", exc)
        sys.exit(1)
    if supabase is None:
        logger.warning("Supabase not configured: review history endpoints will return 503.")
        return None
    repository = create_review_repository(supabase)
    host = urlparse(supabase.url).hostname
    try:
        repository.check_ready()
    except PersistenceError as exc:
        logger.error("Supabase configured for %s but NOT READY: %s", host, exc)
    else:
        logger.info("Review storage connected: %s", host)
    return repository


def main() -> None:
    settings = load_api_settings()
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("a2a.client.card_resolver").setLevel(logging.WARNING)

    app = create_app(settings, _review_repository())
    logger.info("NexusAI API on http://%s:%s (docs: /docs) -> Agent A at %s", settings.host, settings.port, settings.agent_a_url)
    logger.info("CORS allowed origins: %s", ", ".join(settings.cors_origins) or "(none)")
    tesseract = find_tesseract()
    if tesseract:
        logger.info("OCR available: %s", tesseract)
    else:
        logger.warning("OCR not available (Tesseract not found): scanned PDFs and images will be rejected. Set TESSERACT_CMD.")
    uvicorn.run(app, host=settings.host, port=settings.port, timeout_keep_alive=5, log_level="info")


if __name__ == "__main__":
    main()

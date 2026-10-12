"""FastAPI application factory for the NexusAI API."""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.gateway import AgentGateway
from app.api.routes import MAX_UPLOAD_BYTES, router
from app.api.security import AccessGuard, Limit, RateLimiter, security_middleware
from app.config import ApiSettings
from app.persistence.reviews import ReviewRepository
from app.protocol.server import MaxBodySizeMiddleware

logger = logging.getLogger(__name__)

# NexusAI/frontend (this file is NexusAI/backend/app/api/main.py)
FRONTEND_DIR = Path(__file__).resolve().parents[3] / "frontend"

DESCRIPTION = """
Gateway between the NexusAI frontend and the agents.

* **POST /api/reviews** sends an invoice + purchase order to **Agent A (Procurement Coordinator)**
  over **A2A**; Agent A delegates verification to **Agent B (Invoice Verification Specialist)** over A2A.
* **POST /api/documents/extract** reads an invoice or purchase-order **PDF or image** (OCR) and returns the extracted
  fields with confidence levels, for a person to review before submitting.
* **GET /api/reviews** and **GET /api/reviews/{task_id}** read saved reviews from Supabase.

The browser never talks to the agents or to Supabase directly. NexusAI never approves invoices for payment.
"""


def create_app(settings: ApiSettings, reviews: ReviewRepository | None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # One pooled HTTP client for all A2A calls; closed on shutdown.
        timeout = httpx.Timeout(settings.request_timeout_seconds, connect=5.0)
        async with httpx.AsyncClient(timeout=timeout) as http:
            app.state.gateway = AgentGateway(
                http, settings.agent_a_url, settings.agent_b_url, settings.request_timeout_seconds
            )
            app.state.reviews = reviews
            yield

    app = FastAPI(
        title="NexusAI API",
        version="0.1.0",
        description=DESCRIPTION,
        lifespan=lifespan,
    )
    app.state.guard = AccessGuard(settings.access_password, settings.access_token_hours)
    app.state.limiter = RateLimiter({rule: [(Limit.parse(text), is_global) for text, is_global in limits]
                                     for rule, limits in settings.rate_limits.items()})
    app.state.trust_proxy = settings.trust_proxy_headers
    if app.state.guard.enabled:
        logger.info("Access password enabled: the web app asks for it before using the API.")
    else:
        logger.warning("ACCESS_PASSWORD is not set: the API is open (fine locally, not for a public deployment).")

    # Middleware added last runs first: CORS outermost (so 401/413/429 answers still carry CORS
    # headers), then the body-size limit, then the access check and rate limits.
    app.middleware("http")(security_middleware)
    app.add_middleware(
        MaxBodySizeMiddleware,
        max_bytes=settings.max_request_bytes,
        path_limits={"/api/documents": MAX_UPLOAD_BYTES + 64 * 1024},  # file + multipart overhead
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "Authorization"],
    )
    app.include_router(router)

    if FRONTEND_DIR.is_dir():
        # Same origin for the UI and the API: http://<host>:<port>/ is the app, /api/* the API.
        # Mounted last so /api, /docs and /openapi.json keep priority.
        app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
    else:
        logger.warning("Frontend folder not found at %s; serving the API only.", FRONTEND_DIR)

        @app.get("/", include_in_schema=False)
        async def root() -> dict:
            return {"name": "NexusAI API", "docs": "/docs", "health": "/api/health"}

    return app

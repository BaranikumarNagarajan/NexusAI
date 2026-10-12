"""Environment-driven configuration.

Secrets (the Supabase secret key) are read from the environment or a local,
git-ignored ``.env`` file. They are never logged, printed or included in
exception messages or ``repr`` output.
"""

import base64
import binascii
import json
import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

# Values already present in the process environment take precedence over .env.
load_dotenv(override=False)


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"Environment variable {name} must be an integer") from exc


@dataclass(frozen=True)
class AgentBSettings:
    host: str
    port: int
    public_url: str
    max_request_bytes: int
    log_level: str


def load_agent_b_settings() -> AgentBSettings:
    host = os.getenv("AGENT_B_HOST", "127.0.0.1")
    port = _int_env("AGENT_B_PORT", 10001)
    public_url = os.getenv("AGENT_B_PUBLIC_URL") or f"http://{host}:{port}"
    return AgentBSettings(
        host=host,
        port=port,
        public_url=public_url.rstrip("/"),
        max_request_bytes=_int_env("MAX_REQUEST_BYTES", 256 * 1024),
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
    )


@dataclass(frozen=True)
class AgentASettings:
    host: str
    port: int
    public_url: str
    agent_b_url: str  # where Agent A discovers Agent B (its Agent Card base URL)
    delegation_timeout_seconds: float
    max_request_bytes: int
    log_level: str


def load_agent_a_settings() -> AgentASettings:
    host = os.getenv("AGENT_A_HOST", "127.0.0.1")
    port = _int_env("AGENT_A_PORT", 10002)
    public_url = os.getenv("AGENT_A_PUBLIC_URL") or f"http://{host}:{port}"
    agent_b_url = os.getenv("AGENT_B_URL") or "http://127.0.0.1:10001"
    return AgentASettings(
        host=host,
        port=port,
        public_url=public_url.rstrip("/"),
        agent_b_url=agent_b_url.rstrip("/"),
        delegation_timeout_seconds=float(_int_env("DELEGATION_TIMEOUT_SECONDS", 30)),
        max_request_bytes=_int_env("MAX_REQUEST_BYTES", 256 * 1024),
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
    )


@dataclass(frozen=True)
class LLMSettings:
    provider: str  # "ollama" | "huggingface" | "openai" (any OpenAI-compatible API)
    token: str = field(repr=False)
    model: str
    base_url: str
    timeout_seconds: float
    enabled: bool
    fallback_model: str = ""  # tried when the main model is unavailable, rate-limited or out of credits


_DEFAULT_MODELS = {"ollama": "qwen2.5:3b", "huggingface": "meta-llama/Llama-3.1-8B-Instruct:cheapest",
                   "openai": "llama-3.1-8b-instant"}


def load_llm_settings() -> LLMSettings:
    """Settings for Agent A's reasoning model.

    LLM_PROVIDER:
      * ollama      - local Ollama (default), or Ollama Cloud with OLLAMA_URL=https://ollama.com
                      and OLLAMA_API_KEY (works after deployment, no local machine needed);
      * huggingface - token from HF_TOKEN / HF_API_KEY;
      * openai      - any OpenAI-compatible API (Groq, OpenRouter, ...): LLM_BASE_URL + LLM_API_KEY.
    LLM_FALLBACK_MODEL (optional): a second model of the same provider, tried when the main one fails.
    With LLM_ENABLED=false, or a hosted provider without a key, Agent A uses its built-in writer.
    """
    provider = os.getenv("LLM_PROVIDER", "ollama").strip().lower()
    if provider in {"hf", "hugging_face"}:
        provider = "huggingface"
    if provider not in _DEFAULT_MODELS:
        raise ConfigurationError("LLM_PROVIDER must be 'ollama', 'huggingface' or 'openai'")
    token = {
        "ollama": os.getenv("OLLAMA_API_KEY"),
        "huggingface": os.getenv("HF_TOKEN") or os.getenv("HF_API_KEY"),
        "openai": os.getenv("LLM_API_KEY"),
    }[provider] or ""
    token = token.strip()
    enabled = os.getenv("LLM_ENABLED", "true").strip().lower() not in {"0", "false", "no", "off"}
    base_url = (os.getenv("LLM_BASE_URL", "") if provider == "openai"
                else os.getenv("OLLAMA_URL") or "http://localhost:11434").strip().rstrip("/")
    local = provider == "ollama" and any(h in base_url for h in ("localhost", "127.0.0.1"))
    try:
        # Local models on a CPU are slower than a hosted API.
        timeout = float(os.getenv("LLM_TIMEOUT_SECONDS", "60" if local else "30"))
    except ValueError as exc:
        raise ConfigurationError("LLM_TIMEOUT_SECONDS must be a number") from exc
    return LLMSettings(
        provider=provider,
        token=token,
        model=os.getenv("LLM_MODEL", "").strip() or _DEFAULT_MODELS[provider],
        base_url=base_url,
        timeout_seconds=timeout,
        # Local Ollama needs no key; Ollama Cloud and the hosted providers do.
        enabled=enabled and (local or bool(token)),
        fallback_model=os.getenv("LLM_FALLBACK_MODEL", "").strip(),
    )


@dataclass(frozen=True)
class ApiSettings:
    host: str
    port: int
    cors_origins: list[str]
    agent_a_url: str  # the API submits reviews to Agent A
    agent_b_url: str  # only used for health/agent listing
    request_timeout_seconds: float
    max_request_bytes: int
    log_level: str
    access_password: str = field(default="", repr=False)  # blank = open API (local development)
    access_token_hours: float = 12.0
    trust_proxy_headers: bool = False  # true behind a hosting proxy (Render, Railway, Fly...)
    rate_limits: dict = field(default_factory=dict)  # rule -> [(limit text, is_global)]


# Per-client limits on the paid / heavy endpoints, plus daily totals across all visitors.
_RATE_LIMIT_DEFAULTS = {
    "reviews": [("RATE_LIMIT_REVIEWS", "6/minute", False), ("RATE_LIMIT_REVIEWS_TOTAL", "300/day", True)],
    "ocr": [("RATE_LIMIT_OCR", "20/minute", False)],
    "kaggle": [("RATE_LIMIT_KAGGLE", "4/minute", False), ("RATE_LIMIT_KAGGLE_TOTAL", "100/day", True)],
    "login": [("RATE_LIMIT_LOGIN", "5/minute", False)],
}


def _rate_limits() -> dict:
    return {rule: [(os.getenv(name, default).strip() or default, is_global) for name, default, is_global in entries]
            for rule, entries in _RATE_LIMIT_DEFAULTS.items()}


def load_api_settings() -> ApiSettings:
    origins = os.getenv(
        "API_CORS_ORIGINS",
        "http://127.0.0.1:5500,http://localhost:5500,http://127.0.0.1:5173,http://localhost:5173",
    )
    return ApiSettings(
        host=os.getenv("API_HOST", "127.0.0.1"),
        # Hosting platforms (Render, Hugging Face Spaces, Railway...) tell the app its port in PORT.
        port=_int_env("API_PORT", _int_env("PORT", 8080)),
        cors_origins=[o.strip().rstrip("/") for o in origins.split(",") if o.strip()],
        agent_a_url=(os.getenv("AGENT_A_URL") or "http://127.0.0.1:10002").rstrip("/"),
        agent_b_url=(os.getenv("AGENT_B_URL") or "http://127.0.0.1:10001").rstrip("/"),
        # Covers delegation to Agent B plus the reasoning model (a local model can take ~20-60 s).
        request_timeout_seconds=float(_int_env("API_REQUEST_TIMEOUT_SECONDS", 120)),
        max_request_bytes=_int_env("MAX_REQUEST_BYTES", 256 * 1024),
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        access_password=os.getenv("ACCESS_PASSWORD", "").strip(),
        access_token_hours=float(os.getenv("ACCESS_TOKEN_HOURS", "12") or 12),
        trust_proxy_headers=os.getenv("TRUST_PROXY_HEADERS", "false").strip().lower() in {"1", "true", "yes", "on"},
        rate_limits=_rate_limits(),
    )


# --- Supabase -------------------------------------------------------------------


class ConfigurationError(Exception):
    """Invalid or incomplete configuration. Messages never contain secret values."""


@dataclass(frozen=True)
class SupabaseSettings:
    url: str
    secret_key: str = field(repr=False)
    key_type: str  # "secret" (sb_secret_...) or "legacy_service_role" (JWT)
    timeout_seconds: float


def load_supabase_settings() -> SupabaseSettings | None:
    """Return Supabase settings, or None when persistence is not configured.

    Raises ConfigurationError for partial or unsafe configuration (for example a
    publishable/anon key, which cannot write to the RLS-protected table).
    """
    url = os.getenv("SUPABASE_URL", "").strip()
    key = os.getenv("SUPABASE_SECRET_KEY", "").strip()
    if not url and not key:
        return None
    if not url or not key:
        missing = "SUPABASE_URL" if not url else "SUPABASE_SECRET_KEY"
        raise ConfigurationError(f"{missing} is not set; set both SUPABASE_URL and SUPABASE_SECRET_KEY or neither.")
    if not url.startswith("https://") and not url.startswith("http://127.0.0.1") and not url.startswith("http://localhost"):
        raise ConfigurationError("SUPABASE_URL must be an https:// URL (http is allowed only for a local Supabase stack).")
    timeout = os.getenv("SUPABASE_TIMEOUT_SECONDS", "").strip()
    try:
        timeout_seconds = float(timeout) if timeout else 10.0
    except ValueError as exc:
        raise ConfigurationError("SUPABASE_TIMEOUT_SECONDS must be a number") from exc
    return SupabaseSettings(
        url=url.rstrip("/"),
        secret_key=key,
        key_type=classify_supabase_key(key),
        timeout_seconds=timeout_seconds,
    )


def classify_supabase_key(key: str) -> str:
    """Accept only backend-privileged keys; never echo the key in errors."""
    if key.startswith("sb_secret_"):
        return "secret"
    if key.startswith("sb_publishable_"):
        raise ConfigurationError(
            "SUPABASE_SECRET_KEY holds a publishable key. Use a secret key (sb_secret_...) from "
            "Project Settings > API Keys; publishable keys cannot write to the review table."
        )
    role = _legacy_jwt_role(key)
    if role == "service_role":
        return "legacy_service_role"
    if role is not None:
        raise ConfigurationError(
            f"SUPABASE_SECRET_KEY is a legacy JWT with role '{role}'. Use a secret key (sb_secret_...) "
            "or the legacy service_role key."
        )
    raise ConfigurationError("SUPABASE_SECRET_KEY is not a recognised Supabase secret or service_role key.")


def _legacy_jwt_role(key: str) -> str | None:
    """Read the unverified 'role' claim of a legacy Supabase JWT key (no signature check needed:
    this only guards against configuring the wrong key; Supabase enforces the real permissions)."""
    parts = key.split(".")
    if len(parts) != 3:
        return None
    try:
        padded = parts[1] + "=" * (-len(parts[1]) % 4)
        claims = json.loads(base64.urlsafe_b64decode(padded))
    except (binascii.Error, ValueError):
        return None
    role = claims.get("role") if isinstance(claims, dict) else None
    return role if isinstance(role, str) else None

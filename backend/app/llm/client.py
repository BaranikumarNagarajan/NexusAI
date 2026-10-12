"""Minimal async chat client for Agent A's reasoning, with two interchangeable providers.

  * ollama      - a model running locally in Ollama (free, offline, invoice data never leaves
                  the machine).  POST {OLLAMA_URL}/api/chat
                  Also Ollama Cloud: base URL https://ollama.com + API key (same API).
  * huggingface - Hugging Face Inference Providers (OpenAI-compatible, needs credits).
                  POST https://router.huggingface.co/v1/chat/completions
  * openai      - any OpenAI-compatible API (Groq, OpenRouter, ...): POST {base_url}/chat/completions

Errors are classified so callers can fall back gracefully and tell the user why
(not running, model missing, no credits, bad token, rate limit, timeout). Tokens are never logged.
An optional fallback model (same provider) is tried when the main model is unavailable,
rate-limited or out of credits - useful on Ollama Cloud's free tier.
"""

import logging
import threading
import time

import httpx

logger = logging.getLogger(__name__)

HF_ROUTER_URL = "https://router.huggingface.co/v1/chat/completions"
# After a non-transient failure (no credits / bad token) skip that model for a while
# instead of paying the round-trip on every request.
COOLDOWN_SECONDS = 300
# How long Ollama keeps the model in memory after a request (a cold load takes ~30 s on CPU).
OLLAMA_KEEP_ALIVE = "30m"
# Hosted models such as gpt-oss "think" before answering, and the thinking counts against the token
# limit; give them room so the JSON answer is not cut off.
HOSTED_MIN_TOKENS = 3000


class LLMError(Exception):
    def __init__(self, kind: str, message: str):
        super().__init__(message)
        # no_credits | auth | rate_limited | unavailable | bad_response | not_running | model_missing
        self.kind = kind


_FRIENDLY = {
    "huggingface": {
        "no_credits": "Hugging Face credits exhausted (HTTP 402). Add pre-paid credits or PRO to enable AI reasoning.",
        "auth": "The Hugging Face token was rejected or lacks the 'Make calls to Inference Providers' permission.",
        "rate_limited": "Hugging Face rate limit reached; try again shortly.",
        "unavailable": "The Hugging Face inference provider is unavailable or timed out.",
    },
    "openai": {
        "no_credits": "The LLM provider reports no credits / quota left (HTTP 402).",
        "auth": "The LLM API key (LLM_API_KEY) was rejected.",
        "rate_limited": "The LLM provider's rate limit was reached; try again shortly.",
        "unavailable": "The LLM provider is unavailable or timed out.",
    },
    "ollama": {
        "auth": "Ollama Cloud rejected the API key (OLLAMA_API_KEY).",
        "rate_limited": "Ollama Cloud usage limit reached; it resets after a few hours.",
        "no_credits": "This model is not included in the Ollama Cloud free tier. Pick another LLM_MODEL.",
        "not_running": "Ollama is not running. Start the Ollama app (it serves on port 11434).",
        "model_missing": "The model is not downloaded in Ollama. Run: ollama pull <model>",
        "unavailable": "The local Ollama model did not answer in time.",
    },
}


def friendly(kind: str, provider: str = "huggingface") -> str:
    if kind == "bad_response":
        return "The model returned an unexpected response."
    return _FRIENDLY.get(provider, {}).get(kind, "AI reasoning is unavailable.")


class ChatClient:
    def __init__(self, provider: str, model: str, *, base_url: str = "", token: str = "",
                 timeout_seconds: float = 60.0, fallback_model: str = ""):
        self.provider = provider  # "ollama" | "huggingface" | "openai"
        self.model = model
        self.fallback_model = fallback_model if fallback_model != model else ""
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._timeout = timeout_seconds
        self._blocked: dict[str, tuple[float, str]] = {}  # model -> (until, kind)
        self.last_status = "unknown"
        self.last_model = model  # the model that wrote the most recent answer

    @property
    def label(self) -> str:
        if self.provider == "ollama":
            return "Ollama (local)" if self.local else "Ollama Cloud"
        return {"huggingface": "Hugging Face", "openai": "LLM API"}.get(self.provider, self.provider)

    @property
    def local(self) -> bool:
        return self.provider == "ollama" and any(h in self._base_url for h in ("localhost", "127.0.0.1"))

    def friendly(self, kind: str) -> str:
        return friendly(kind, self.provider).replace("<model>", self.model)

    def _fail(self, kind: str, model: str, cause: Exception | None = None) -> LLMError:
        self.last_status = kind
        if kind in {"no_credits", "auth"}:
            self._blocked[model] = (time.monotonic() + COOLDOWN_SECONDS, kind)
        error = LLMError(kind, self.friendly(kind))
        error.__cause__ = cause
        return error

    def _request(self, model: str, messages: list[dict], max_tokens: int, temperature: float) -> tuple[str, dict, dict]:
        if not self.local:
            max_tokens = max(max_tokens, HOSTED_MIN_TOKENS)
        if self.provider == "ollama":
            body = {"model": model, "messages": messages, "stream": False, "format": "json",
                    "keep_alive": OLLAMA_KEEP_ALIVE,
                    "options": {"temperature": temperature, "num_predict": max_tokens}}
            if not self.local:
                body.pop("keep_alive")
            return f"{self._base_url}/api/chat", self._auth(), body
        body = {"model": model, "messages": messages, "max_tokens": max_tokens,
                "temperature": temperature, "stream": False}
        url = HF_ROUTER_URL if self.provider == "huggingface" else f"{self._base_url}/chat/completions"
        return url, self._auth(), body

    def _auth(self) -> dict:
        return {"Authorization": f"Bearer {self._token}"} if self._token else {}

    async def complete(self, messages: list[dict], *, max_tokens: int = 900, temperature: float = 0.2) -> str:
        """Ask the main model; on a model-specific failure try the fallback model once."""
        models = [self.model] + ([self.fallback_model] if self.fallback_model else [])
        error: LLMError | None = None
        for model in models:
            try:
                content = await self._complete_with(model, messages, max_tokens, temperature)
                self.last_model = model
                return content
            except LLMError as exc:
                error = exc
                if exc.kind in {"auth", "not_running"}:  # the same for every model: do not retry
                    break
                if model != models[-1]:
                    logger.warning("%s model %s failed (%s); trying fallback %s", self.label, model, exc.kind, models[-1])
        assert error is not None
        raise error

    async def _complete_with(self, model: str, messages: list[dict], max_tokens: int, temperature: float) -> str:
        until, kind = self._blocked.get(model, (0.0, ""))
        if time.monotonic() < until:
            raise LLMError(kind, self.friendly(kind))
        url, headers, body = self._request(model, messages, max_tokens, temperature)
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(self._timeout, connect=5.0)) as http:
                response = await http.post(url, headers=headers, json=body)
        except httpx.ConnectError as exc:
            raise self._fail("not_running" if self.local else "unavailable", model, exc) from exc
        except httpx.HTTPError as exc:
            raise self._fail("unavailable", model, exc) from exc

        if response.status_code == 200:
            try:
                data = response.json()
                content = (data["message"]["content"] if self.provider == "ollama"
                           else data["choices"][0]["message"]["content"]) or ""
            except (KeyError, IndexError, TypeError, ValueError) as exc:
                raise self._fail("bad_response", model, exc) from exc
            self.last_status = "ready"
            return content

        if self.provider == "ollama" and response.status_code == 404:
            kind = "model_missing"
        else:
            kind = {402: "no_credits", 401: "auth", 403: "auth", 429: "rate_limited"}.get(response.status_code, "unavailable")
        logger.warning("%s request to %s failed: HTTP %s (%s)", self.label, model, response.status_code, kind)
        raise self._fail(kind, model)

    async def probe(self) -> str:
        """Report readiness at startup: 'ready' or an error kind."""
        if self.local:
            return self._probe_ollama()
        try:
            await self.complete([{"role": "user", "content": "Reply with OK."}], max_tokens=2, temperature=0)
            return "ready"
        except LLMError as exc:
            return exc.kind

    def _probe_ollama(self) -> str:
        """Check the server and the model without generating, then load the model in the background
        so the first review does not pay the cold-start cost."""
        try:
            tags = httpx.get(f"{self._base_url}/api/tags", timeout=3).json().get("models", [])
        except (httpx.HTTPError, ValueError):
            self.last_status = "not_running"
            return "not_running"
        names = {m.get("name") for m in tags} | {m.get("model") for m in tags}
        if self.model not in names and f"{self.model}:latest" not in names:
            self.last_status = "model_missing"
            return "model_missing"
        self.last_status = "ready"
        threading.Thread(target=self._warm_up, daemon=True).start()
        return "ready"

    def _warm_up(self) -> None:
        try:
            httpx.post(f"{self._base_url}/api/generate",
                       json={"model": self.model, "prompt": "", "keep_alive": OLLAMA_KEEP_ALIVE}, timeout=180)
            logger.info("Ollama model %s loaded and kept warm for %s.", self.model, OLLAMA_KEEP_ALIVE)
        except httpx.HTTPError:
            pass

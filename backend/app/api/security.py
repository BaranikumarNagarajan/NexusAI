"""Access password and rate limits for a public deployment.

* **Access password** (``ACCESS_PASSWORD``): when set, every ``/api/*`` call except the health
  check and the login needs a token. ``POST /api/auth/login`` exchanges the password for a
  signed, short-lived token (HMAC-SHA256, no database). The web app sends it as
  ``Authorization: Bearer <token>``; plain ``<img>`` links may pass ``?access_token=``.
  Without ``ACCESS_PASSWORD`` (local development) the API stays open.
* **Rate limits** protect the paid / heavy work - the AI model (reviews), Kaggle downloads,
  OCR - and slow down password guessing. Limits are per client IP, plus a daily total for
  reviews so many visitors together cannot use up the AI quota.

Everything is in memory: limits reset when the API restarts, which is fine for one instance.
"""

import base64
import hashlib
import hmac
import time
from collections import defaultdict, deque
from dataclasses import dataclass

from fastapi import Request
from fastapi.responses import JSONResponse

# Paths that never need a token.
PUBLIC_PATHS = {"/api/health", "/api/auth/login", "/api/auth/status"}


@dataclass(frozen=True)
class Limit:
    count: int
    seconds: int

    @classmethod
    def parse(cls, text: str) -> "Limit":
        """'6/minute', '300/day', '20/hour' or '10/30' (count per seconds)."""
        count, _, per = text.strip().partition("/")
        seconds = {"second": 1, "minute": 60, "hour": 3600, "day": 86400}.get(per.strip().rstrip("s"))
        return cls(int(count), seconds if seconds else int(per))


class AccessGuard:
    def __init__(self, password: str, token_hours: float = 12.0) -> None:
        self.enabled = bool(password)
        self._password = password
        self._ttl = int(token_hours * 3600)
        # Changing the password invalidates every token issued with the old one.
        self._key = hashlib.sha256(f"nexusai-access:{password}".encode()).digest()

    def check_password(self, attempt: str) -> bool:
        return self.enabled and hmac.compare_digest(attempt.encode(), self._password.encode())

    def issue(self) -> tuple[str, int]:
        expires = int(time.time()) + self._ttl
        return f"{expires}.{self._sign(str(expires))}", self._ttl

    def verify(self, token: str | None) -> bool:
        if not self.enabled:
            return True
        if not token or "." not in token:
            return False
        expires, _, signature = token.partition(".")
        return (expires.isdigit() and int(expires) > time.time()
                and hmac.compare_digest(signature, self._sign(expires)))

    def _sign(self, value: str) -> str:
        digest = hmac.new(self._key, value.encode(), hashlib.sha256).digest()
        return base64.urlsafe_b64encode(digest).decode().rstrip("=")


class RateLimiter:
    """Sliding-window counters: rule name -> limits, checked per client key."""

    def __init__(self, rules: dict[str, list[tuple[Limit, bool]]]) -> None:
        self._rules = rules  # (limit, is_global) pairs
        self._hits: dict[tuple[str, str, int], deque[float]] = defaultdict(deque)

    def hit(self, rule: str, client: str) -> int | None:
        """Record one request; return seconds to wait if a limit is exceeded (nothing recorded then)."""
        now = time.monotonic()
        windows = []
        for index, (limit, is_global) in enumerate(self._rules.get(rule, [])):
            hits = self._hits[(rule, "*" if is_global else client, index)]
            while hits and hits[0] <= now - limit.seconds:
                hits.popleft()
            if len(hits) >= limit.count:
                return max(1, int(hits[0] + limit.seconds - now) + 1)
            windows.append(hits)
        for hits in windows:
            hits.append(now)
        return None


# (method, path prefix) -> rule name
_LIMITED = [
    ("POST", "/api/reviews", "reviews"),
    ("POST", "/api/documents/extract", "ocr"),
    ("POST", "/api/samples/kaggle/refresh", "kaggle"),
    ("POST", "/api/auth/login", "login"),
]


def client_ip(request: Request, trust_proxy: bool) -> str:
    """Behind a hosting proxy (Render, Railway, Fly...) the real IP is the first X-Forwarded-For entry."""
    if trust_proxy:
        forwarded = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
        if forwarded:
            return forwarded
    return request.client.host if request.client else "unknown"


def request_token(request: Request) -> str | None:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return request.query_params.get("access_token")


async def security_middleware(request: Request, call_next):
    """Token check, then rate limits, for /api/* (the web app's static files stay public)."""
    path = request.url.path
    if not path.startswith("/api/") or request.method == "OPTIONS":
        return await call_next(request)
    guard: AccessGuard = request.app.state.guard
    if guard.enabled and path not in PUBLIC_PATHS and not guard.verify(request_token(request)):
        return JSONResponse({"detail": "Sign in with the access password to use NexusAI.", "auth_required": True},
                            status_code=401, headers={"WWW-Authenticate": "Bearer"})
    limiter: RateLimiter = request.app.state.limiter
    for method, prefix, rule in _LIMITED:
        if request.method == method and path.startswith(prefix):
            wait = limiter.hit(rule, client_ip(request, request.app.state.trust_proxy))
            if wait is not None:
                return JSONResponse({"detail": f"Too many requests. Please wait {wait} s and try again."},
                                    status_code=429, headers={"Retry-After": str(wait)})
            break
    return await call_next(request)

"""Server plumbing shared by every NexusAI A2A agent."""

from a2a.server.agent_execution import AgentExecutor
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import AgentCard
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class _BodyTooLarge(Exception):
    pass


class MaxBodySizeMiddleware:
    """Rejects HTTP requests whose body exceeds the limit with 413.

    ``path_limits`` maps path prefixes to larger limits (e.g. file uploads).
    """

    def __init__(self, app: ASGIApp, max_bytes: int, path_limits: dict[str, int] | None = None) -> None:
        self.app = app
        self.max_bytes = max_bytes
        self.path_limits = path_limits or {}

    def _limit_for(self, path: str) -> int:
        return next((limit for prefix, limit in self.path_limits.items() if path.startswith(prefix)), self.max_bytes)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        max_bytes = self._limit_for(scope.get("path", ""))
        declared = dict(scope.get("headers", [])).get(b"content-length")
        if declared is not None and declared.isdigit() and int(declared) > max_bytes:
            await self._reject(scope, receive, send)
            return

        received = 0
        response_started = False

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > max_bytes:
                    raise _BodyTooLarge
            return message

        async def tracking_send(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracking_send)
        except _BodyTooLarge:
            if not response_started:
                await self._reject(scope, receive, send)

    @staticmethod
    async def _reject(scope: Scope, receive: Receive, send: Send) -> None:
        response = JSONResponse({"error": "request body too large"}, status_code=413)
        await response(scope, receive, send)


def build_a2a_app(
    agent_card: AgentCard, executor: AgentExecutor, max_request_bytes: int, info=None
) -> Starlette:
    """Agent Card discovery route + JSON-RPC endpoint at '/', via the official SDK."""
    request_handler = DefaultRequestHandler(
        agent_executor=executor,
        task_store=InMemoryTaskStore(),
        agent_card=agent_card,
    )
    async def about(_request) -> JSONResponse:
        # Browsers open the base URL with GET; A2A JSON-RPC at "/" is POST-only.
        return JSONResponse({
            "agent": agent_card.name,
            "version": agent_card.version,
            "protocol": "A2A JSON-RPC: POST /",
            "agent_card": "/.well-known/agent-card.json",
            **(info() if info else {}),
        })

    routes = [
        *create_agent_card_routes(agent_card),
        *create_jsonrpc_routes(request_handler, "/"),
        Route("/", about, methods=["GET"]),
    ]
    return Starlette(
        routes=routes,
        middleware=[Middleware(MaxBodySizeMiddleware, max_bytes=max_request_bytes)],
    )

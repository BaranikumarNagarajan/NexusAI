"""A2A test client for the NexusAI agents.

Discovers an agent through its Agent Card, submits invoice-review tasks over A2A
(JSON-RPC), retrieves each task with GetTask and prints the result.

    --agent coordinator   client -> Agent A (Procurement Coordinator) -> Agent B   [full two-agent flow]
    --agent verifier      client -> Agent B (Invoice Verification Specialist) directly   [default]

Usage (from backend/, virtual environment activated):
    python scripts/4_send_verification_task.py --agent coordinator --brief   # all scenarios via Agent A
    python scripts/4_send_verification_task.py --brief                       # all scenarios, Agent B directly
    python scripts/4_send_verification_task.py supplier_mismatch             # one scenario, full JSON
    python scripts/4_send_verification_task.py path/to/request.json
    python scripts/4_send_verification_task.py --list
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

# Allow running as a plain script from backend/.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402
from a2a.client import AgentCardResolutionError  # noqa: E402
from a2a.types import Task, TaskState  # noqa: E402
from a2a.utils.errors import A2AError  # noqa: E402
from app.protocol.client import (  # noqa: E402
    artifact_data,
    artifact_metadata,
    discover,
    open_client,
    status_text,
    submit_and_retrieve,
)

AGENTS = {
    "verifier": {
        "url": "http://127.0.0.1:10001",
        "artifact": "verification_result",
        "start": "python -m app.agents.invoice_verifier",
    },
    "coordinator": {
        "url": "http://127.0.0.1:10002",
        "artifact": "procurement_review",
        "start": "python -m app.agents.procurement_coordinator",
    },
}
SAMPLES_DIR = Path(__file__).resolve().parent.parent / "samples"
SCENARIOS = [
    "matching",
    "supplier_mismatch",
    "currency_mismatch",
    "quantity_total_mismatch",
    "missing_information",
    "invalid_request",
]
HTTP_TIMEOUT = httpx.Timeout(40.0, connect=5.0)
RESULT_TIMEOUT_SECONDS = 45.0


def extract_result(task: Task) -> dict | None:
    """Agent B's verification_result artifact."""
    return artifact_data(task, "verification_result")


def _print_findings(verification: dict) -> None:
    for finding in verification.get("findings", []):
        print(f"  {finding['outcome']:<20} {finding['check']:<26} {finding['explanation']}")
    for issue in verification.get("validation_errors", []):
        print(f"  INVALID              {issue['field']:<26} {issue['message']}")


def _saved_line(persistence: dict | None) -> str:
    if not persistence:
        return "unknown"
    line = persistence.get("status", "unknown")
    if persistence.get("review_id"):
        line += f" (review id {persistence['review_id']})"
    elif persistence.get("detail"):
        line += f" - {persistence['detail']}"
    return line


def print_verifier_task(task: Task, brief: bool) -> None:
    result = extract_result(task)
    if result is None:
        print(f"No verification_result artifact returned. {status_text(task)}")
        return
    print(f"Result     : {result['result']}")
    print(f"Summary    : {result['summary']}")
    print(f"Saved      : {_saved_line(artifact_metadata(task, 'verification_result').get('persistence'))}")
    if brief:
        _print_findings(result)
    else:
        print(json.dumps(result, indent=2))


def print_coordinator_task(task: Task, brief: bool) -> None:
    review = artifact_data(task, "procurement_review")
    if review is None:
        print(f"No procurement_review artifact returned. {status_text(task)}")
        return
    delegation = review["delegation"]
    print(f"Action     : {review['recommended_action']}")
    print(f"Result     : {review['verification_result']}")
    print(f"Summary    : {review['summary']}")
    print(f"Delegated  : {delegation['agent']} via A2A, skill {delegation['skill']}")
    print(f"Agent B    : task {delegation['task_id']} ({delegation['task_state']})")
    print(f"Saved      : {_saved_line(delegation.get('persistence'))}")
    if not brief:
        print(json.dumps(review, indent=2))
        return
    for item in review["action_items"]:
        print(f"  - {item}")


def print_task(label: str, task: Task, agent: str, brief: bool) -> None:
    print(f"\n=== {label} ===")
    print(f"Task ID    : {task.id}")
    print(f"Task state : {TaskState.Name(task.status.state)}")
    if agent == "coordinator":
        print_coordinator_task(task, brief)
    else:
        print_verifier_task(task, brief)


def resolve_requests(names: list[str]) -> list[tuple[str, Path]]:
    if not names:
        names = SCENARIOS
    resolved = []
    for name in names:
        path = Path(name)
        if not path.suffix:
            path = SAMPLES_DIR / "json" / f"{name}.json"
        if not path.is_file():
            raise SystemExit(f"Unknown scenario or file: {name} (use --list)")
        resolved.append((path.stem, path))
    return resolved


async def run(base_url: str, requests: list[tuple[str, Path]], brief: bool, agent: str = "verifier") -> int:
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as http:
        card = await discover(http, base_url)
        print(f"Discovered : {card.name} v{card.version}")
        print(f"Endpoint   : {card.supported_interfaces[0].url} ({card.supported_interfaces[0].protocol_binding})")
        print(f"Skills     : {', '.join(s.id for s in card.skills)}")

        client = await open_client(card, http)
        try:
            for label, path in requests:
                payload = json.loads(path.read_text(encoding="utf-8"))
                task = await submit_and_retrieve(client, payload, RESULT_TIMEOUT_SECONDS)
                print_task(label, task, agent, brief)
        finally:
            await client.close()
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Send invoice-review tasks to a NexusAI agent over A2A.",
        epilog=f"Scenarios: {', '.join(SCENARIOS)}",
    )
    parser.add_argument("requests", nargs="*", help="scenario names or JSON request files (default: all scenarios)")
    parser.add_argument("--agent", choices=sorted(AGENTS), default="verifier", help="which agent to call")
    parser.add_argument("--url", help="agent base URL (default depends on --agent)")
    parser.add_argument("--brief", action="store_true", help="print a summary table instead of full JSON")
    parser.add_argument("--list", action="store_true", help="list built-in scenarios and exit")
    args = parser.parse_args()

    if args.list:
        print("\n".join(SCENARIOS))
        return
    agent = AGENTS[args.agent]
    url = (args.url or agent["url"]).rstrip("/")
    requests = resolve_requests(args.requests)
    try:
        sys.exit(asyncio.run(run(url, requests, args.brief, args.agent)))
    except (AgentCardResolutionError, httpx.ConnectError):
        sys.exit(f"Could not discover the {args.agent} agent at {url}. Is it running? Start it with: {agent['start']}")
    except (httpx.HTTPError, A2AError, TimeoutError) as exc:
        sys.exit(f"A2A request failed: {type(exc).__name__}: {exc}")


if __name__ == "__main__":
    main()

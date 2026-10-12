"""Start the whole NexusAI system with one command.

    python backend\\run.py            (from D:\\NexusAI)
    python run.py                    (from D:\\NexusAI\\backend)

Starts three independent processes, in order, waiting until each is ready:
  1. Agent B - Invoice Verification Specialist (A2A server)
  2. Agent A - Procurement Coordinator         (A2A server, delegates to Agent B over A2A)
  3. NexusAI API + web app                    (FastAPI; the one URL you open)
Logs from all three appear here with a prefix. Press Ctrl+C once to stop everything.
"""

import argparse
import os
import signal
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path
from urllib.parse import urlparse

BACKEND = Path(__file__).resolve().parent
sys.path.insert(0, str(BACKEND))

import httpx  # noqa: E402
from app.config import load_agent_a_settings, load_agent_b_settings, load_api_settings  # noqa: E402

COLORS = {"agent-b": "\033[36m", "agent-a": "\033[35m", "api": "\033[32m"}
RESET = "\033[0m"
READY_TIMEOUT_SECONDS = 30


class Service:
    def __init__(self, name: str, module: str, ready_url: str, bind_url: str):
        self.name, self.module, self.ready_url, self.bind_url = name, module, ready_url, bind_url
        self.process: subprocess.Popen | None = None

    def start(self, use_color: bool) -> None:
        env = {**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"}
        self.process = subprocess.Popen(
            [sys.executable, "-m", self.module],
            cwd=BACKEND,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        prefix = f"{COLORS[self.name]}[{self.name:^7}]{RESET} " if use_color else f"[{self.name:^7}] "
        threading.Thread(target=self._pump, args=(prefix,), daemon=True).start()

    def _pump(self, prefix: str) -> None:
        assert self.process and self.process.stdout
        for line in self.process.stdout:
            print(prefix + line.rstrip(), flush=True)

    def running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def wait_ready(self) -> bool:
        deadline = time.monotonic() + READY_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            if not self.running():
                return False
            try:
                if httpx.get(self.ready_url, timeout=2).status_code == 200:
                    return True
            except httpx.HTTPError:
                pass
            time.sleep(0.4)
        return False

    def stop(self) -> None:
        if self.running():
            self.process.terminate()
            try:
                self.process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                self.process.kill()


def _port_in_use(url: str) -> bool:
    parsed = urlparse(url)
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex((parsed.hostname or "127.0.0.1", parsed.port or 80)) == 0


def _stop_on_sigterm(signum, frame) -> None:
    raise KeyboardInterrupt


def main() -> None:
    parser = argparse.ArgumentParser(description="Start Agent B, Agent A and the NexusAI web app.")
    parser.add_argument("--no-browser", action="store_true", help="don't open the web app in a browser")
    args = parser.parse_args()
    # In a container `docker stop` sends SIGTERM: stop the three services as cleanly as Ctrl+C does.
    signal.signal(signal.SIGTERM, _stop_on_sigterm)

    use_color = sys.stdout.isatty()
    if use_color and os.name == "nt":
        os.system("")  # enable ANSI colours in the Windows console

    b, a, api = load_agent_b_settings(), load_agent_a_settings(), load_api_settings()
    api_url = f"http://{api.host}:{api.port}"
    services = [
        Service("agent-b", "app.agents.invoice_verifier", f"{b.public_url}/.well-known/agent-card.json", b.public_url),
        Service("agent-a", "app.agents.procurement_coordinator", f"{a.public_url}/.well-known/agent-card.json", a.public_url),
        Service("api", "app.api", f"{api_url}/api/health", api_url),
    ]

    busy = [s for s in services if _port_in_use(s.bind_url)]
    if busy:
        for s in busy:
            print(f"Port for {s.name} ({s.bind_url}) is already in use. Is NexusAI already running in another terminal?")
        sys.exit(1)

    started: list[Service] = []
    try:
        for service in services:
            print(f">> starting {service.name} ...", flush=True)
            service.start(use_color)
            started.append(service)
            if not service.wait_ready():
                print(f"!! {service.name} did not become ready; see its log above. Stopping.", flush=True)
                raise SystemExit(1)

        bar = "=" * 64
        print(f"\n{bar}\n  NexusAI is running\n"
              f"  Web app      : {api_url}\n"
              f"  API docs     : {api_url}/docs\n"
              f"  Agent A card : {a.public_url}/.well-known/agent-card.json\n"
              f"  Agent B card : {b.public_url}/.well-known/agent-card.json\n"
              f"  Press Ctrl+C to stop everything.\n{bar}\n", flush=True)
        if not args.no_browser:
            webbrowser.open(api_url)

        while all(s.running() for s in started):
            time.sleep(0.5)
        dead = next(s for s in started if not s.running())
        print(f"!! {dead.name} stopped unexpectedly (exit code {dead.process.returncode}). Stopping the rest.", flush=True)
        raise SystemExit(1)
    except KeyboardInterrupt:
        print("\n>> stopping NexusAI ...", flush=True)
    finally:
        for service in reversed(started):
            service.stop()
        print(">> all services stopped.", flush=True)


if __name__ == "__main__":
    main()

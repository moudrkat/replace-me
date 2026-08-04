"""Shared plumbing for the end-to-end suites.

Standalone scripts, not pytest: `python3 tests/run_all.py` runs
everything. Each suite drives the REAL brain/daemon/MCP code against a
stub OpenAI endpoint and a sandbox transcript dir — no mic, no TTS, no
model, no network beyond 127.0.0.1. Call `sandbox_env(...)` BEFORE
importing anything from `replace_me`: the modules read their env at
import/first-use and the character cache is per-process.
"""

import asyncio
import os
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
FAILURES: list[str] = []


def sandbox_env(name: str, port: int, llm_port: int) -> Path:
    """Fresh transcript dir + daemon/LLM ports, wired up via env."""
    sandbox = Path(tempfile.mkdtemp(prefix=f"replace-me-{name}-"))
    os.environ["REPLACEME_DIR"] = str(sandbox)
    os.environ["REPLACEME_PORT"] = str(port)
    os.environ["REPLACEME_LLM_URL"] = f"http://127.0.0.1:{llm_port}/v1"
    os.environ.pop("REPLACEME_PRIVATE", None)
    os.environ.pop("REPLACEME_CHARACTER", None)
    sys.path.insert(0, str(REPO / "src"))
    return sandbox


def check(label: str, ok: bool, detail: str = "") -> None:
    print(("PASS  " if ok else "FAIL  ") + label + (f"  [{detail}]" if detail and not ok else ""))
    if not ok:
        FAILURES.append(label)


async def wait_for(predicate, seconds: float = 8.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.3)
    return False


async def serve_stub(handler, port: int, route: str):
    """One-route aiohttp server; returns the runner for cleanup()."""
    from aiohttp import web

    app = web.Application()
    app.router.add_post(route, handler)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", port).start()
    return runner


def finish() -> None:
    print("---")
    print("ALL PASS" if not FAILURES else f"FAILED: {FAILURES}")
    sys.exit(1 if FAILURES else 0)

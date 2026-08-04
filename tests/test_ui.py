"""Face buttons end to end: REAL daemon (ears idled, TTS stubbed) + real
brain + stub LLM + websocket clients standing in for the face page."""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _harness import check, finish, sandbox_env, serve_stub, wait_for  # noqa: E402

SANDBOX = sandbox_env("ui", port=9878, llm_port=9335)
BASE = "http://127.0.0.1:9878"

import aiohttp  # noqa: E402
from aiohttp import web  # noqa: E402
from replace_me import __main__ as daemon_main  # noqa: E402
from replace_me import brain, transcript  # noqa: E402

CANNED = "## Topics\n- buttons\n## Decisions\n- none\n## Tasks\n- nothing"
llm_calls: list[dict] = []


async def idle_ears(sink, muted=None):  # replaces the mic entirely
    while True:
        await asyncio.sleep(3600)


async def silent_speak(text, muted):  # no edge-tts network calls
    return None


async def stub_llm(request: web.Request) -> web.Response:
    llm_calls.append(await request.json())
    return web.json_response({"choices": [{"message": {"content": CANNED}}]})


class FakeFace:
    """A websocket client that records state messages like face.html would."""

    def __init__(self, session: aiohttp.ClientSession) -> None:
        self.session = session
        self.states: list[dict] = []
        self.task: asyncio.Task | None = None

    async def connect(self) -> None:
        ws = await self.session.ws_connect(f"{BASE}/ws")

        async def pump() -> None:
            async for message in ws:
                data = json.loads(message.data)
                if data.get("kind") == "state":
                    self.states.append(data)

        self.task = asyncio.create_task(pump())


async def main() -> None:
    daemon_main.ears.run = idle_ears
    daemon_main._speak = silent_speak
    llm_runner = await serve_stub(stub_llm, 9335, "/v1/chat/completions")
    daemon_task = asyncio.create_task(daemon_main._serve())
    await asyncio.sleep(1.0)

    async with aiohttp.ClientSession() as session:
        face = FakeFace(session)
        await face.connect()
        await asyncio.sleep(0.5)
        check("no state before brain lives", not face.states)

        brain_task = asyncio.create_task(brain.run(chatty=False, observed=False))
        ok = await wait_for(
            lambda: face.states
            and face.states[-1] == {"kind": "state", "meeting": False, "pending_handoff": False}
        )
        check("startup state broadcast", ok, str(face.states))

        resp = await session.post(f"{BASE}/ui", json={"action": "rm -rf"})
        check("garbage action rejected", resp.status == 400 and not transcript.read_last(5))

        await session.post(f"{BASE}/ui", json={"action": "meeting_start"})
        ok = await wait_for(lambda: face.states and face.states[-1]["meeting"])
        marks = [l.text for l in transcript.read_last(50) if l.who == transcript.WHO_MEETING]
        check("button opened meeting + state", ok and marks == ["start"], str(marks))

        late = FakeFace(session)
        await late.connect()
        ok = await wait_for(lambda: late.states and late.states[-1]["meeting"], 4)
        check("late face gets state replay", ok, str(late.states))

        transcript.append("Avatar, fix the bug in tasks.py")
        ok = await wait_for(lambda: face.states and face.states[-1]["pending_handoff"])
        check("consent panel state on ask", ok)
        await session.post(f"{BASE}/ui", json={"action": "handoff_yes"})
        ok = await wait_for(
            lambda: any(l.who == transcript.WHO_HANDOFF for l in transcript.read_last(80)), 10
        )
        check("click-consent produced brief", ok)
        check(
            "state cleared after consent",
            await wait_for(lambda: not face.states[-1]["pending_handoff"]),
        )

        transcript.append("Avatar, write that endpoint for me")
        ok = await wait_for(lambda: face.states[-1]["pending_handoff"])
        check("second ask pending", ok)
        await session.post(f"{BASE}/ui", json={"action": "handoff_no"})
        ok = await wait_for(lambda: not face.states[-1]["pending_handoff"])
        briefs = sum(1 for l in transcript.read_last(100) if l.who == transcript.WHO_HANDOFF)
        check("button decline -> no brief", ok and briefs == 1, f"briefs={briefs}")

        transcript.append("Avatar, fix the other bug too")
        await wait_for(lambda: face.states[-1]["pending_handoff"])
        transcript.append("yes, hand it over")
        ok = await wait_for(
            lambda: sum(1 for l in transcript.read_last(120) if l.who == transcript.WHO_HANDOFF)
            == 2,
            10,
        )
        check("spoken consent still works", ok)

        await session.post(f"{BASE}/ui", json={"action": "meeting_end"})
        ok = await wait_for(lambda: not face.states[-1]["meeting"], 15)
        files = sorted((SANDBOX / "minutes").glob("*.md")) if (SANDBOX / "minutes").exists() else []
        marks = [l.text for l in transcript.read_last(200) if l.who == transcript.WHO_MEETING]
        check(
            "button closed meeting with minutes",
            ok and len(files) == 1 and marks == ["start", "end"],
            f"files={files} markers={marks}",
        )

        html = await (await session.get(f"{BASE}/")).text()
        check(
            "ui labels substituted",
            "▶ Meeting" in html and "Hand over" in html and "{{ui_" not in html,
        )

        brain_task.cancel()
        daemon_task.cancel()
        for fake in (face, late):
            if fake.task:
                fake.task.cancel()
    await llm_runner.cleanup()
    finish()


asyncio.run(main())

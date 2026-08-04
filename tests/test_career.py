"""Career + memory end to end: the replacement learns the job, remembers
meetings, announces milestones, and room_report closes the loop."""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _harness import check, finish, sandbox_env, serve_stub, wait_for  # noqa: E402

SANDBOX = sandbox_env("career", port=9880, llm_port=9337)

from aiohttp import web  # noqa: E402
from replace_me import brain, career, transcript  # noqa: E402

CANNED_MINUTES = "## Topics\n- deploy\n## Decisions\n- none\n## Tasks\n- fix tasks.py"
CANNED_MEMORY = "- the deploy breaks every night\n- nobody ever looks at the whiteboard"
CANNED_BANTER = "I heard that."
CANNED_BRIEF = "Fix the login bug. Done = tests pass."
said: list[str] = []
banter_systems: list[str] = []


async def stub_llm(request: web.Request) -> web.Response:
    payload = await request.json()
    system = payload["messages"][0].get("content", "")
    if isinstance(system, str) and "meeting minutes" in system:
        reply = CANNED_MINUTES
    elif isinstance(system, str) and "notes-to-self" in system:
        reply = CANNED_MEMORY
    elif isinstance(system, str) and "task brief" in system:
        reply = CANNED_BRIEF
    else:
        banter_systems.append(system if isinstance(system, str) else "")
        reply = CANNED_BANTER
    return web.json_response({"choices": [{"message": {"content": reply}}]})


async def stub_say(request: web.Request) -> web.Response:
    said.append((await request.json())["text"])
    return web.json_response({"ok": True})


async def main() -> None:
    # milestone at 5 % needs few points: shrink the scale for the test
    career._SCALE = 40.0
    runners = [
        await serve_stub(stub_llm, 9337, "/v1/chat/completions"),
        await serve_stub(stub_say, 9880, "/say"),
    ]
    check("day zero: progress 0", career.progress() == 0.0)
    check("day zero: no memory", career.memory_text() == "")

    brain_task = asyncio.create_task(brain.run(chatty=False, observed=False))
    await asyncio.sleep(0.5)

    # one full meeting -> minutes + meeting logged, memory written
    transcript.append("Avatar, start the meeting")
    await wait_for(lambda: len(said) >= 1)
    transcript.append("the deploy failed again tonight")
    await asyncio.sleep(3)
    transcript.append("Avatar, end the meeting")
    ok = await wait_for(lambda: career.memory_text() == CANNED_MEMORY, 12)
    check("memory distilled after meeting", ok, career.memory_text())
    stats = career.stats()
    check("career logged minutes+meeting", stats["minutes"] == 1 and stats["meeting"] == 1, str(stats))
    check("progress ticked up", career.progress() > 0)

    # she now REMEMBERS: next banter call carries the notes in the system prompt
    banter_systems.clear()
    transcript.append("Avatar, can you hear us?")
    ok = await wait_for(lambda: len(banter_systems) >= 1, 10)
    check("memory injected into banter prompt", ok and CANNED_MEMORY in banter_systems[-1])

    # milestone bubble fired at some threshold (scale shrunk above)
    ok = await wait_for(lambda: any("%" in s for s in said), 6)
    check("milestone announced", ok and career.milestones_announced(), str(said))

    # handoff logs career points too
    transcript.append("Avatar, fix the login bug")
    await wait_for(lambda: any("?" in s and "hand" in s.lower() for s in said), 8)
    transcript.append("yes, hand it over")
    ok = await wait_for(lambda: career.stats()["handoff"] == 1, 10)
    check("handoff logged in career", ok, str(career.stats()))

    brain_task.cancel()

    # room_report closes the loop: bubble + handoff_done + progress jump
    from replace_me import mcp as mcp_server

    report = getattr(mcp_server.room_report, "fn", mcp_server.room_report)
    before = career.progress()
    out = await report("Login bug fixed, tests green.")
    check("room_report accepted", out.startswith("Reported"), out)
    check("handoff_done logged", career.stats()["handoff_done"] == 1)
    check("progress jumped", career.progress() > before)
    check("report bubble shown", said and said[-1] == "Login bug fixed, tests green.")

    # the example character template still covers every knob
    import os

    os.environ["REPLACEME_CHARACTER"] = str(
        Path(__file__).resolve().parents[1] / "characters" / "example.md"
    )
    from replace_me import character as character_module

    character_module.get.cache_clear()
    ch = character_module.get()
    defaults = character_module.Character()
    check(
        "example.md covers memory prompt + milestones",
        ch.memory_prompt == defaults.memory_prompt
        and ch.milestone_bubble == defaults.milestone_bubble
        and ch.theme.get("ui_progress") == "replacement progress",
    )

    for runner in runners:
        await runner.cleanup()
    finish()


asyncio.run(main())

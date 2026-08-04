"""Handoff with spoken consent, end to end: real brain loop, stub LLM,
stub daemon /say — plus the private-mode filter through the real MCP
tools."""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _harness import check, finish, sandbox_env, serve_stub, wait_for  # noqa: E402

SANDBOX = sandbox_env("handoff", port=9876, llm_port=9333)

from aiohttp import web  # noqa: E402
from replace_me import brain, transcript  # noqa: E402

CANNED_BRIEF = "Fix the bug in tasks.py: it crashes on empty dates. Done = tests pass."
said: list[str] = []
llm_calls: list[dict] = []


async def stub_llm(request: web.Request) -> web.Response:
    llm_calls.append(await request.json())
    return web.json_response({"choices": [{"message": {"content": CANNED_BRIEF}}]})


async def stub_say(request: web.Request) -> web.Response:
    said.append((await request.json())["text"])
    return web.json_response({"ok": True})


async def main() -> None:
    runners = [
        await serve_stub(stub_llm, 9333, "/v1/chat/completions"),
        await serve_stub(stub_say, 9876, "/say"),
    ]
    brain_task = asyncio.create_task(brain.run(chatty=False, observed=False))
    await asyncio.sleep(0.5)

    # 1) unaddressed work verb -> nothing at all
    transcript.append("we need to fix that deploy")
    await asyncio.sleep(4)
    check("unaddressed work verb ignored", not said and not llm_calls)

    # 2) addressed work request -> consent question (no LLM call yet)
    transcript.append("Avatar, fix the bug in tasks.py")
    ok = await wait_for(lambda: len(said) == 1)
    check("consent question asked", ok, f"said={said}")
    check("no LLM call before consent", not llm_calls)

    # 3) room says yes -> brief composed, [HANDOFF] line, confirm bubble
    transcript.append("yes, hand it over")
    ok = await wait_for(
        lambda: any(l.who == transcript.WHO_HANDOFF for l in transcript.read_last(50))
    )
    check("handoff line written", ok)
    briefs = [l for l in transcript.read_last(50) if l.who == transcript.WHO_HANDOFF]
    check("brief content is the model output", bool(briefs) and briefs[-1].text == CANNED_BRIEF)
    check(
        "brief LLM call used brief prompt not persona",
        bool(llm_calls)
        and "task brief" in llm_calls[-1]["messages"][0]["content"]
        and "deadpan" not in llm_calls[-1]["messages"][0]["content"],
    )
    ok = await wait_for(lambda: len(said) >= 2)
    check("confirm bubble said", ok)
    check("render tag is [HANDOFF]", bool(briefs) and briefs[-1].render().startswith("[HANDOFF]"))

    # 4) second request, room declines -> drop bubble, no new handoff
    said.clear()
    llm_calls.clear()
    transcript.append("Avatar, write that endpoint for me")
    ok = await wait_for(lambda: len(said) == 1)
    check("second consent question", ok)
    transcript.append("no, leave it")
    ok = await wait_for(lambda: len(said) >= 2)
    handoffs = sum(1 for l in transcript.read_last(100) if l.who == transcript.WHO_HANDOFF)
    check(
        "decline -> drop bubble, no LLM, still 1 handoff",
        ok and not llm_calls and handoffs == 1,
        f"said={said} llm={len(llm_calls)} handoffs={handoffs}",
    )

    brain_task.cancel()
    for runner in runners:
        await runner.cleanup()

    # 5) private mode through the REAL MCP tools
    import os

    from replace_me import mcp as mcp_server

    fn = getattr(mcp_server.room_recent, "fn", mcp_server.room_recent)
    os.environ["REPLACEME_PRIVATE"] = "1"
    private_out = await fn(50)
    os.environ.pop("REPLACEME_PRIVATE")
    full_out = await fn(50)
    check(
        "private mode returns only briefs",
        bool(private_out.strip())
        and all(l.startswith("[HANDOFF]") for l in private_out.splitlines()),
        private_out,
    )
    check("full mode returns raw speech", any(l.startswith("[ROOM]") for l in full_out.splitlines()))
    finish()


asyncio.run(main())

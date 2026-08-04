"""Meeting minutes end to end: spoken boundaries, local-model minutes,
chunked rolling summarization. Real brain loop, stub LLM, stub daemon."""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _harness import check, finish, sandbox_env, serve_stub, wait_for  # noqa: E402

SANDBOX = sandbox_env("minutes", port=9877, llm_port=9334)

from aiohttp import web  # noqa: E402
from replace_me import brain, transcript  # noqa: E402

CANNED = "## Topics\n- deploy\n## Decisions\n- none\n## Tasks\n- fix tasks.py"
said: list[str] = []
llm_calls: list[dict] = []


def minutes_files() -> list[Path]:
    d = SANDBOX / "minutes"
    return sorted(d.glob("*.md")) if d.exists() else []


def markers() -> list[str]:
    return [l.text for l in transcript.read_last(200) if l.who == transcript.WHO_MEETING]


async def stub_llm(request: web.Request) -> web.Response:
    llm_calls.append(await request.json())
    return web.json_response({"choices": [{"message": {"content": CANNED}}]})


async def stub_say(request: web.Request) -> web.Response:
    said.append((await request.json())["text"])
    return web.json_response({"ok": True})


async def main() -> None:
    runners = [
        await serve_stub(stub_llm, 9334, "/v1/chat/completions"),
        await serve_stub(stub_say, 9877, "/say"),
    ]
    brain_task = asyncio.create_task(brain.run(chatty=False, observed=False))
    await asyncio.sleep(0.5)

    transcript.append("okay everyone, let's start the meeting")
    await asyncio.sleep(4)
    check("unaddressed start ignored", not said and not markers())

    transcript.append("Avatar, start the meeting")
    ok = await wait_for(lambda: markers() == ["start"])
    check("meeting opened", ok, str(markers()))
    ok = await wait_for(lambda: len(said) >= 1)
    check("start bubble", ok, str(said))

    transcript.append("Avatar, start the meeting")
    ok = await wait_for(lambda: len(said) >= 2)
    check("double start refused", ok and markers() == ["start"], str(said))

    transcript.append("the deploy failed again, we need to handle it")
    transcript.append("we'll do it tomorrow morning")
    await asyncio.sleep(3)

    said.clear()
    llm_calls.clear()
    transcript.append("Avatar, take the minutes")
    ok = await wait_for(lambda: len(minutes_files()) == 1, 10)
    check("mid-meeting minutes file", ok, str(minutes_files()))
    check(
        "minutes content",
        bool(minutes_files()) and CANNED in minutes_files()[0].read_text(encoding="utf-8"),
    )
    check(
        "minutes prompt used",
        bool(llm_calls) and "meeting minutes" in llm_calls[0]["messages"][0]["content"],
    )
    check("no handoff consent question", all("hand" not in s.lower() or ".md" in s for s in said), str(said))
    check("meeting still open", markers() == ["start"])

    said.clear()
    llm_calls.clear()
    transcript.append("Avatar, end the meeting")
    ok = await wait_for(lambda: len(minutes_files()) == 2 and markers() == ["start", "end"], 10)
    check("meeting closed with minutes", ok, f"files={len(minutes_files())} markers={markers()}")

    said.clear()
    transcript.append("Avatar, end the meeting")
    ok = await wait_for(lambda: len(said) >= 1)
    check("end without meeting refused", ok and len(minutes_files()) == 2, str(said))

    # chunking: small chunks -> multiple LLM calls, update prompt on later ones
    brain.MINUTES_CHUNK_LINES = 4
    said.clear()
    llm_calls.clear()
    transcript.append("Avatar, start the meeting")
    await wait_for(lambda: markers() == ["start", "end", "start"])
    for i in range(9):
        transcript.append(f"meeting item number {i}")
    await asyncio.sleep(3)
    transcript.append("Avatar, end the meeting")
    ok = await wait_for(lambda: len(minutes_files()) == 3, 15)
    check("chunked minutes written", ok)
    check("chunking made multiple LLM calls", len(llm_calls) >= 2, f"calls={len(llm_calls)}")
    check(
        "update prompt used on later chunks",
        len(llm_calls) >= 2
        and any(
            "Update the minutes" in call["messages"][0]["content"] for call in llm_calls
        ),  # the very last call may be the memory distillation, not a chunk
    )

    brain_task.cancel()
    for runner in runners:
        await runner.cleanup()
    finish()


asyncio.run(main())

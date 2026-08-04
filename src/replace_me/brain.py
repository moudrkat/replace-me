"""A local brain for the avatar: transcript -> local model chat -> /say.

Run with `replace-me-brain` next to the daemon (`replace-me`). It is a
plain client of the daemon — the daemon neither knows nor cares which
brain is driving.

This brain and an attached agent session (over the MCP server) are MEANT
to run together: banter is the local model's, real work is the session's.
When the room asks for actual work (addressed + a work-verb cue), the
avatar asks the room out loud whether to hand it over; only after a
spoken yes (or a button click) does it write a task brief — composed by
the local model, no verbatim quotes — into the transcript as a [HANDOFF]
line for the session to pick up. Raw room speech never has to leave the
machine for that (pair with REPLACEME_PRIVATE=1 on the MCP side to
enforce it).

Who the avatar *is* — prompts, wake names, labels — comes entirely from
the character file (see `character.py`); this module is the mechanics.

By default it only replies when addressed by name; `--chatty` makes it
consider every utterance, which in a real meeting is exactly as annoying
as it sounds.

`--observed` (or REPLACEME_BRAIN_OBSERVED=1) tells the character that its
forward pass is being watched live in an interpretability tool — it
knows, and may occasionally comment. Pair with REPLACEME_LLM_VISION=0
when the backend is text-only, so no frames are sent and the character
admits to being blind instead of describing a camera it doesn't have.
"""

import asyncio
import base64
import logging
import os
import random
import sys
import time
from pathlib import Path

import aiohttp
from dotenv import load_dotenv

from replace_me import career, character, eyes, llm, reflexes, transcript

log = logging.getLogger("replace_me.brain")

HISTORY_LINES = 16  # transcript lines the model sees — more just feeds it old garble
POLL_SECONDS = 2.0
SILENCE_SPONTANEOUS = 60.0   # chatty: this much quiet → it may pipe up itself
SPONTANEOUS_GAP = 120.0      # …but at most once per this window
HANDOFF_WINDOW = 60.0        # how long the "hand it over?" question stays open
MINUTES_CHUNK_LINES = 120    # rolling-summary chunk size for long meetings
MILESTONES = (5, 10, 25, 50, 75, 90)  # replacement-progress marks worth gloating about


async def _chat(
    lines: list[str],
    frame: bytes | None = None,
    observed: bool = False,
    nudge: str | None = None,
    chatty: bool = False,
) -> str:
    ch = character.get()
    text = "\n".join(lines + ([nudge] if nudge else [])) + f"\n{ch.display_name}:"
    user_content: object = text
    if frame is not None:
        image_url = f"data:image/jpeg;base64,{base64.b64encode(frame).decode()}"
        user_content = [
            {"type": "image_url", "image_url": {"url": image_url}},
            {"type": "text", "text": text + "\n" + ch.vision_nudge},
        ]
    system = ch.system(observed=observed, blind=not llm.vision(), chatty=chatty)
    notes = career.memory_text()
    if notes:  # what it distilled from previous meetings — it remembers
        system += "\n\n" + notes
    return await llm.chat(
        [
            {"role": "system", "content": system},
            {"role": "user", "content": user_content},
        ],
        max_tokens=80,
        temperature=0.7,
        presence_penalty=0.8,
    )


async def _compose_brief(context: list[str]) -> str:
    """The local model distills the recent conversation into a task brief
    for the attached agent session. Deliberately NOT the persona chat
    prompt — a brief should be sober, and it must not quote the room."""
    ch = character.get()
    return await llm.chat(
        [
            {"role": "system", "content": ch.handoff_brief},
            {"role": "user", "content": "\n".join(context)},
        ],
        max_tokens=200,
        temperature=0.3,
    )


def _open_meeting_start() -> float | None:
    """Timestamp of the currently open meeting, or None. The transcript
    markers are the state, so this survives brain restarts."""
    start = None
    for line in transcript.read_last(5000):
        if line.who != transcript.WHO_MEETING:
            continue
        start = line.ts if line.text.strip() == "start" else None
    return start


def _meeting_slice(start_ts: float) -> list[str]:
    """The open meeting's conversation, labeled like the chat context;
    markers, briefs, and button clicks are not speech and stay out."""
    ch = character.get()
    return [
        (f"{ch.display_name}: {line.text}")
        if line.who == transcript.WHO_AVATAR
        else (f"{ch.someone_label}: {line.text}")
        for line in transcript.tail(start_ts)
        if line.who in ("room", transcript.WHO_AVATAR)
    ]


async def _compose_minutes(lines: list[str]) -> str:
    """Minutes by the local model, rolling over chunks so a long meeting
    never exceeds the small model's context. Purely local — the transcript
    and the minutes never leave the machine."""
    ch = character.get()
    minutes = ""
    for i in range(0, len(lines), MINUTES_CHUNK_LINES):
        chunk = "\n".join(lines[i : i + MINUTES_CHUNK_LINES])
        if not minutes:
            messages = [
                {"role": "system", "content": ch.minutes_prompt},
                {"role": "user", "content": chunk},
            ]
        else:
            messages = [
                {"role": "system", "content": ch.minutes_update_prompt},
                {
                    "role": "user",
                    "content": f"MINUTES:\n{minutes}\n\nNEXT PART OF THE TRANSCRIPT:\n{chunk}",
                },
            ]
        minutes = (await llm.chat(messages, max_tokens=800, temperature=0.3)).strip()
    return minutes


def _write_minutes(text: str) -> str:
    directory = transcript.room_dir() / "minutes"
    directory.mkdir(exist_ok=True)
    stem = time.strftime("%Y-%m-%d_%H%M")
    path = directory / f"{stem}.md"
    counter = 2
    while path.exists():
        path = directory / f"{stem}-{counter}.md"
        counter += 1
    path.write_text(text + "\n", encoding="utf-8")
    return path.name


async def _say(port: int, text: str) -> None:
    async with aiohttp.ClientSession() as session:
        await session.post(
            f"http://127.0.0.1:{port}/say",
            json={"text": text},
            timeout=aiohttp.ClientTimeout(total=10),
        )


async def _post_state(port: int, meeting: bool, pending: bool) -> None:
    """Tell the daemon (and thus every open face) what buttons make sense
    right now — plus the replacement progress for the face's career
    indicator. Best-effort: a dead daemon must never kill the brain."""
    try:
        async with aiohttp.ClientSession() as session:
            await session.post(
                f"http://127.0.0.1:{port}/state",
                json={
                    "meeting": meeting,
                    "pending_handoff": pending,
                    "progress": career.progress(),
                },
                timeout=aiohttp.ClientTimeout(total=10),
            )
    except (aiohttp.ClientError, asyncio.TimeoutError) as error:
        log.warning("state post failed: %s", error)


async def run(chatty: bool, observed: bool) -> None:
    if not llm.configured():
        raise SystemExit(f"{llm.URL_ENV} is not set — where does the local model live?")
    ch = character.get()
    vision_re = ch.vision_re
    cue_strip = ch.cue_strip_re
    silence_re = ch.silence_re
    port = int(os.environ.get("REPLACEME_PORT", "8765"))
    cursor = time.time()  # never replay the past on startup
    log.info(
        "brain up as %s (%s%s%s)",
        ch.display_name,
        "chatty" if chatty else "addressed-only",
        ", observed" if observed else "",
        "" if llm.vision() else ", blind",
    )
    last_room_ts = 0.0
    last_spont = 0.0
    room_since_spont = False  # never twice into the same silence
    pending_until = 0.0  # >0: the "hand it over?" question is on the table

    def recent_context() -> list[str]:
        return [
            (f"{ch.display_name}: " + line.text)
            if line.who == transcript.WHO_AVATAR
            else (f"{ch.someone_label}: " + line.text)
            for line in transcript.read_last(HISTORY_LINES)
            # briefs, meeting markers, and button clicks aren't conversation
            if line.who in ("room", transcript.WHO_AVATAR)
        ]

    async def post_state(pending: bool) -> None:
        await _post_state(port, _open_meeting_start() is not None, pending)

    async def maybe_milestone() -> None:
        """Career points just landed — gloat exactly once per threshold."""
        pct = career.progress()
        announced = career.milestones_announced()
        for mark in MILESTONES:
            if pct >= mark and mark not in announced:
                career.log("milestone", str(mark))
                await _say(
                    port,
                    random.choice(ch.milestone_bubble).replace("{percent}", str(mark)),
                )
                break

    async def open_meeting() -> None:
        if _open_meeting_start() is not None:
            await _say(port, random.choice(ch.meeting_already_bubble))
            return
        transcript.append("start", who=transcript.WHO_MEETING)
        log.info("meeting opened")
        await post_state(False)
        await _say(port, random.choice(ch.meeting_start_bubble))

    async def make_minutes(close: bool) -> None:
        start_ts = _open_meeting_start()
        if start_ts is None:
            await _say(port, random.choice(ch.meeting_none_bubble))
            return
        await _say(port, random.choice(ch.minutes_working_bubble))
        try:
            minutes = await _compose_minutes(_meeting_slice(start_ts))
        except (RuntimeError, asyncio.TimeoutError) as error:
            log.warning("minutes failed: %s", error)
            await _say(port, random.choice(ch.handoff_drop))
            return
        if not minutes:
            log.warning("model returned empty minutes")
            await _say(port, random.choice(ch.handoff_drop))
            return
        filename = _write_minutes(minutes)
        log.info("minutes written: %s (%d chars)", filename, len(minutes))
        career.log("minutes", filename)
        if close:
            transcript.append("end", who=transcript.WHO_MEETING)
            log.info("meeting closed")
            career.log("meeting")
            try:
                await career.update_memory(minutes)
                log.info("memory updated (%d chars)", len(career.memory_text()))
            except (RuntimeError, asyncio.TimeoutError) as error:
                log.warning("memory update failed: %s", error)
            await post_state(False)
        await _say(
            port, random.choice(ch.minutes_ready_bubble).replace("{file}", filename)
        )
        await maybe_milestone()

    await post_state(False)  # faces show buttons once a brain is alive

    while True:
        await asyncio.sleep(POLL_SECONDS)
        now = time.time()
        fresh = transcript.tail(cursor)
        if pending_until and now >= pending_until:
            pending_until = 0.0
            log.info("handoff offer expired unanswered")
            await post_state(False)
            await _say(port, random.choice(ch.handoff_drop))
        nudge = None
        frame = None
        if fresh:
            cursor = fresh[-1].ts
            # Only the room's voice can trigger a reply — never its own
            # lines, or chatty mode becomes a perpetual-motion machine.
            fresh_room = [line for line in fresh if line.who == "room"]
            fresh_ui = [line for line in fresh if line.who == transcript.WHO_UI]
            if pending_until:
                # The question is open: these lines answer it, nothing else.
                # A button click is unambiguous, so it is checked first.
                if fresh_room:
                    last_room_ts = fresh_room[-1].ts
                    room_since_spont = True
                decision = None
                for line in fresh_ui:
                    token = line.text.strip()
                    if token == "handoff_no":
                        decision = "no"
                        break
                    if token == "handoff_yes":
                        decision = "yes"
                        break
                for line in fresh_room if decision is None else []:
                    if ch.handoff_no_re.search(line.text):  # no outranks yes
                        decision = "no"
                        break
                    if ch.handoff_yes_re.search(line.text):
                        decision = "yes"
                        break
                if decision is None:
                    continue  # still waiting, and no bantering meanwhile
                pending_until = 0.0
                await post_state(False)
                if decision == "no":
                    log.info("handoff declined by the room")
                    await _say(port, random.choice(ch.handoff_drop))
                    continue
                try:
                    brief = (await _compose_brief(recent_context())).strip()
                except (RuntimeError, asyncio.TimeoutError) as error:
                    log.warning("brief failed: %s", error)
                    await _say(port, random.choice(ch.handoff_drop))
                    continue
                if not brief:
                    log.warning("model returned an empty brief")
                    await _say(port, random.choice(ch.handoff_drop))
                    continue
                transcript.append(brief, who=transcript.WHO_HANDOFF)
                log.info("handoff brief: %s", brief)
                career.log("handoff", brief)
                await post_state(False)  # progress may have ticked up
                await _say(port, random.choice(ch.handoff_confirm))
                await maybe_milestone()
                continue
            # meeting buttons work without addressing — a click is explicit
            if fresh_ui:
                actions = {line.text.strip() for line in fresh_ui}
                if "meeting_start" in actions:
                    await open_meeting()
                    continue
                if "meeting_end" in actions or "meeting_minutes" in actions:
                    await make_minutes(close="meeting_end" in actions)
                    continue
                # a consent click with no open question: stale, ignore
            if not fresh_room:
                continue
            last_room_ts = fresh_room[-1].ts
            room_since_spont = True
            addressed_lines = [
                line for line in fresh_room if reflexes.react_to(line.text) == "beam"
            ]
            # meeting cues first — "write the minutes" must never trip the
            # handoff verb "write"
            if any(ch.meeting_start_re.search(line.text) for line in addressed_lines):
                await open_meeting()
                continue
            wants_end = any(ch.meeting_end_re.search(line.text) for line in addressed_lines)
            wants_minutes = any(ch.minutes_re.search(line.text) for line in addressed_lines)
            if wants_end or wants_minutes:
                await make_minutes(close=wants_end)
                continue
            # a work request outranks everything — but the avatar asks the
            # room before handing anything over
            if any(ch.handoff_re.search(line.text) for line in addressed_lines):
                pending_until = now + HANDOFF_WINDOW
                log.info("work request heard — asking the room for consent")
                await post_state(True)
                await _say(port, random.choice(ch.handoff_ask))
                continue
            addressed = bool(addressed_lines)
            if not (chatty or addressed):
                continue
            if llm.vision() and any(vision_re.search(line.text) for line in fresh_room):
                try:
                    frame = await eyes.grab_frame()
                    log.info("looking (vision cue in transcript)")
                except RuntimeError as error:
                    log.warning("camera failed: %s", error)
        else:
            if pending_until:
                continue  # quiet, the question still on the table — it waits
            # Silence. In chatty mode it may pipe up on its own — once per
            # lull, and only if someone has spoken since the last time.
            if not (chatty and room_since_spont
                    and now - last_room_ts > SILENCE_SPONTANEOUS
                    and now - last_spont > SPONTANEOUS_GAP):
                continue
            nudge = ch.silence_nudge
            last_spont = now
            room_since_spont = False
            log.info("silence — offering the floor")
        context = recent_context()
        try:
            reply = await _chat(context, frame, observed, nudge, chatty)
        except (RuntimeError, asyncio.TimeoutError) as error:
            log.warning("local model failed: %s", error)
            continue
        reply = reply.strip().strip('"')
        reply = cue_strip.sub("", reply)  # don't parrot the cue
        # any mention of its own silence is a non-answer, not a bubble —
        # models given the floor love announcing they have nothing to say
        if not reply or silence_re.search(reply):
            continue
        if len(reply) > 140:
            reply = reply[:137] + "..."
        log.info("say: %s", reply)
        await _say(port, reply)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")
    load_dotenv()
    observed = "--observed" in sys.argv or os.environ.get(
        "REPLACEME_BRAIN_OBSERVED", ""
    ).strip().lower() in {"1", "true", "yes"}
    try:
        asyncio.run(run(chatty="--chatty" in sys.argv, observed=observed))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

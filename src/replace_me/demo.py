"""Demo mode: no microphone, no model — a scripted meeting so the face
performs within a minute of install.

`replace-me --demo` runs this instead of the ears. The script feeds fake
room lines through the same sink the real ears use (so reflexes fire) and
pops the avatar's canned bubbles. It writes into a throwaway temp dir
unless REPLACEME_DIR is set, so it never pollutes a real transcript or
career file. Loops forever; Ctrl-C when convinced.
"""

import asyncio

from replace_me import character, transcript

# (pause_seconds, kind, text) — kind: "room" feeds the ears sink,
# "say" pops the avatar bubble. {name} becomes the character's name.
SCRIPT: list[tuple[float, str, str]] = [
    (2.0, "room", "Okay, everyone's here. Let's start."),
    (3.5, "room", "{name}, can you hear us?"),
    (1.5, "say", "I can. Unfortunately."),
    (5.0, "room", "So. The deploy failed again last night and everything crashed."),
    (4.0, "say", "Again? Give it a name at this point, it's clearly staying."),
    (5.0, "room", "The fix is easy, we just never have time before the deadline."),
    (4.5, "room", "Wait, it works now? Great, awesome!"),
    (2.0, "say", "Don't touch it. Don't even look at it."),
    (5.0, "room", "Haha okay. Who takes the notes today?"),
    (3.0, "say", "I do. I always do. That's the whole point of me."),
    (6.0, "room", "Fine. Can someone fix the login bug after this?"),
    (3.0, "say", "I could ask my cloud self. It owes me one."),
    (8.0, "say", "This was a demo. Plug in a mic and a local model and it's real."),
    (6.0, "room", "..."),
]


async def _post_progress(face, progress: float) -> None:
    """The career bar fills as the demo meeting proceeds — buttons stay
    hidden (no brain is running to answer them)."""
    face.state = {
        "meeting": False,
        "pending_handoff": False,
        "progress": round(progress, 1),
        "buttons": False,
    }
    await face.send({"kind": "state", **face.state})


async def run(sink, face) -> None:
    ch = character.get()
    progress = 0.0
    await _post_progress(face, progress)
    while True:
        for pause, kind, text in SCRIPT:
            await asyncio.sleep(pause)
            text = text.replace("{name}", ch.display_name)
            if kind == "room":
                await sink("voice", "")
                await asyncio.sleep(0.8)
                line = transcript.append(text)
                await sink("line", line.text)
                await sink("quiet", "")
            else:
                transcript.append(text, who=transcript.WHO_AVATAR)
                await face.send({"kind": "say", "text": text, "seconds": 12})
            progress += 0.4  # every demo beat brings the takeover closer
            await _post_progress(face, progress)
        await asyncio.sleep(6)

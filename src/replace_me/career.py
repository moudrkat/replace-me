"""The employment file: what the replacement remembers and how far along it is.

Two artifacts, both local, both plain files:

- ``memory.md`` — notes-to-self the LOCAL model distills after every
  meeting (projects, recurring topics, running jokes, how things usually
  go). Injected into the system prompt, so the character actually
  remembers last week. Delete the file and it starts fresh.
- ``career.jsonl`` — an append-only log of work events: meetings
  attended, minutes written, tasks handed off, tasks completed. From it
  comes the **replacement progress** — a deadpan percentage of "how far
  along it is to your job". Asymptotic by design: it never quite gets
  there. That's the joke, and also the promise.
"""

import json
import time

from replace_me import character, llm, transcript

# progress points per event kind — tuned for comedy, not HR accuracy
_POINTS = {
    "meeting": 2.0,       # attended a whole meeting
    "minutes": 3.0,       # wrote the minutes
    "handoff": 1.0,       # got work approved for handoff
    "handoff_done": 5.0,  # the work actually came back done
}
_SCALE = 400.0  # points at which progress reaches 50 %


def career_file():
    return transcript.room_dir() / "career.jsonl"


def memory_file():
    return transcript.room_dir() / "memory.md"


def log(kind: str, note: str = "") -> None:
    with career_file().open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps({"ts": time.time(), "kind": kind, "note": note}, ensure_ascii=False)
            + "\n"
        )


def stats() -> dict[str, int]:
    counts: dict[str, int] = {kind: 0 for kind in _POINTS}
    path = career_file()
    if not path.exists():
        return counts
    for raw in path.read_text(encoding="utf-8").splitlines():
        try:
            kind = json.loads(raw).get("kind", "")
        except ValueError:
            continue
        if kind in counts:
            counts[kind] += 1
    return counts


def progress() -> float:
    """Replacement progress in percent. Monotonic, asymptotic, never 100."""
    points = sum(_POINTS[kind] * count for kind, count in stats().items())
    return round(100.0 * points / (points + _SCALE), 1)


def milestones_announced() -> set[int]:
    path = career_file()
    if not path.exists():
        return set()
    done: set[int] = set()
    for raw in path.read_text(encoding="utf-8").splitlines():
        try:
            data = json.loads(raw)
        except ValueError:
            continue
        if data.get("kind") == "milestone":
            try:
                done.add(int(data.get("note", "")))
            except ValueError:
                continue
    return done


def memory_text() -> str:
    path = memory_file()
    return path.read_text(encoding="utf-8").strip() if path.exists() else ""


async def update_memory(minutes: str) -> None:
    """After a meeting: old notes + fresh minutes -> updated notes.

    Purely local (the character's own model); failures are logged by the
    caller and never lose the old notes.
    """
    ch = character.get()
    old = memory_text()
    reply = await llm.chat(
        [
            {"role": "system", "content": ch.memory_prompt},
            {
                "role": "user",
                "content": f"NOTES SO FAR:\n{old or '(none yet)'}\n\nMINUTES OF THE MEETING THAT JUST ENDED:\n{minutes}",
            },
        ],
        max_tokens=500,
        temperature=0.3,
    )
    reply = reply.strip()
    if reply:
        memory_file().write_text(reply + "\n", encoding="utf-8")

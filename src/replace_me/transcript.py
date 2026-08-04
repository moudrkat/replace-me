"""The transcript file — the only thing the daemon, the brain, and the MCP
server share.

JSONL, one utterance per line, append-only. A file (not a socket) on
purpose: any process can restart without the others noticing, and the
whole meeting stays greppable afterwards. Meeting-scale is a few hundred
lines, so readers just re-read the file instead of keeping offsets.
"""

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

# Wire values of the `who` field.
WHO_AVATAR = "avatar"  # the avatar's own bubbles

# A task brief written by the local brain for the operator's agent session,
# after the room consented out loud. In private mode these lines are the
# ONLY thing the MCP tools may return.
WHO_HANDOFF = "handoff"

# Meeting boundary markers (text "start"/"end"). The markers ARE the
# meeting state: an open meeting is a start with no later end, and that
# survives brain restarts because the transcript does.
WHO_MEETING = "meeting"

# A button click on the face page (text = action token). Same trust level
# as a voice in the room — it can consent to a handoff, never approve
# anything bigger. Commands, not speech: excluded from banter and minutes.
WHO_UI = "ui"


def room_dir() -> Path:
    custom = os.environ.get("REPLACEME_DIR")
    directory = Path(custom) if custom else Path.home() / ".replace-me"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def transcript_file() -> Path:
    return room_dir() / "transcript.jsonl"


@dataclass(frozen=True)
class Line:
    ts: float
    text: str
    who: str = "room"  # "room" = heard from the mic

    def render(self) -> str:
        from replace_me import character  # lazy: keep transcript import-light

        stamp = time.strftime("%H:%M:%S", time.localtime(self.ts))
        if self.who == WHO_AVATAR:
            tag = character.get().transcript_tag
        elif self.who == WHO_HANDOFF:
            tag = "HANDOFF"
        elif self.who == WHO_MEETING:
            tag = "MEETING"
        elif self.who == WHO_UI:
            tag = "UI"
        else:
            tag = "ROOM"
        return f"[{tag}] [{stamp}] {self.text}"


def append(text: str, who: str = "room") -> Line:
    line = Line(ts=time.time(), text=text, who=who)
    with transcript_file().open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps({"ts": line.ts, "text": line.text, "who": line.who}, ensure_ascii=False)
            + "\n"
        )
    return line


def _read_all() -> list[Line]:
    path = transcript_file()
    if not path.exists():
        return []
    lines: list[Line] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            data = json.loads(raw)
            lines.append(
                Line(ts=float(data["ts"]), text=str(data["text"]), who=str(data.get("who", "room")))
            )
        except (ValueError, KeyError):
            continue  # a torn write at the tail is not worth crashing over
    return lines


def read_last(n: int = 20) -> list[Line]:
    return _read_all()[-n:]


def tail(after_ts: float) -> list[Line]:
    """Every line strictly newer than `after_ts` (oldest first)."""
    return [line for line in _read_all() if line.ts > after_ts]

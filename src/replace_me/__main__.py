"""The daemon: ears + face server in one process.

Run with `replace-me` (or `python -m replace_me`), then open
http://127.0.0.1:8765/ fullscreen on the laptop facing the table. The
brain (`replace-me-brain`) and the MCP server (`replace-me-mcp`) never
import this module; they read the transcript file and POST to /say,
/react, /state.

Recording consent is the operator's job to arrange in person; the face
keeps a pulsing "recording" indicator the whole time the microphone is
open, so the room can always see it is live.
"""

import asyncio
import json
import logging
import os
from pathlib import Path
from importlib.resources import files

from aiohttp import WSMsgType, web
from dotenv import load_dotenv

from replace_me import character, ears, reflexes, transcript

log = logging.getLogger("replace_me")

PORT_ENV = "REPLACEME_PORT"
DEFAULT_PORT = 8765

EXPRESSIONS = frozenset(
    {
        "neutral", "smile", "beam", "laugh", "shocked", "surprise", "worried",
        "skeptical", "curious", "eyeroll", "wink", "frown", "sleepy",
    }
)

# Face-button actions. A click is appended to the transcript as who="ui"
# and the brain treats it exactly like the spoken equivalent — same trust
# as a voice in the room.
UI_ACTIONS = frozenset(
    {"meeting_start", "meeting_minutes", "meeting_end", "handoff_yes",
     "handoff_no", "plan_approve", "plan_reject"}
)


async def _speak(text: str, muted: asyncio.Event) -> None:
    """Synthesize with edge-tts and play; the ears are deaf meanwhile.

    Only the avatar's own lines ever reach the TTS service — never anything
    the room said. An empty voice (character `voice:` field or
    REPLACEME_VOICE set to empty) disables speech entirely: bubble only.
    If synthesis fails (offline, service down) the bubble has already been
    shown, so we just log and stay silent.
    """
    ch = character.get()
    if not ch.voice:
        return
    import edge_tts

    path = transcript.room_dir() / "say.mp3"
    try:
        await edge_tts.Communicate(text, ch.voice, rate=ch.voice_rate).save(str(path))
    except Exception as error:  # noqa: BLE001 — voice is best-effort
        log.warning("tts failed (%s); bubble only", error)
        return
    muted.set()
    try:
        player = await asyncio.create_subprocess_exec(
            "ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", str(path)
        )
        await player.wait()
        await asyncio.sleep(1.5)  # sink latency + reverb tail — don't hear our own echo
    finally:
        muted.clear()


class _Face:
    """Fan-out of daemon events to every open face page."""

    def __init__(self) -> None:
        self.sockets: set[web.WebSocketResponse] = set()
        self.muted = asyncio.Event()  # set while the avatar is speaking
        # last brain-reported state; None until a brain posts one, and the
        # face hides its buttons until then (no brain -> no buttons)
        self.state: dict | None = None
        # last plan card posted by the agent session (display only)
        self.plan: dict | None = None

    async def send(self, payload: dict) -> None:
        dead = []
        for socket in self.sockets:
            try:
                await socket.send_str(json.dumps(payload, ensure_ascii=False))
            except ConnectionError:
                dead.append(socket)
        for socket in dead:
            self.sockets.discard(socket)


def _routes(face: _Face) -> list[web.RouteDef]:
    html = (files("replace_me") / "face.html").read_text(encoding="utf-8")
    for key, value in character.get().face_tokens().items():
        html = html.replace("{{" + key + "}}", value)

    async def index(_request: web.Request) -> web.Response:
        return web.Response(text=html, content_type="text/html")

    async def websocket(request: web.Request) -> web.WebSocketResponse:
        socket = web.WebSocketResponse(heartbeat=30)
        await socket.prepare(request)
        face.sockets.add(socket)
        log.info("face connected (%d open)", len(face.sockets))
        if face.state is not None:  # late joiners get the current buttons
            await socket.send_str(
                json.dumps({"kind": "state", **face.state}, ensure_ascii=False)
            )
        if face.plan is not None:  # ...and the plan card in progress
            await socket.send_str(
                json.dumps({"kind": "plan", **face.plan}, ensure_ascii=False)
            )
        try:
            async for message in socket:
                if message.type == WSMsgType.ERROR:
                    break
        finally:
            face.sockets.discard(socket)
        return socket

    async def react(request: web.Request) -> web.Response:
        try:
            data = await request.json()
        except json.JSONDecodeError:
            return web.json_response({"error": "bad json"}, status=400)
        expression = str(data.get("expression", ""))
        if expression not in EXPRESSIONS:
            return web.json_response(
                {"error": f"unknown expression, pick from: {sorted(EXPRESSIONS)}"},
                status=400,
            )
        seconds = min(max(float(data.get("seconds", 5)), 1.0), 30.0)
        await face.send({"kind": "react", "expression": expression, "seconds": seconds})
        return web.json_response({"ok": True})

    async def say(request: web.Request) -> web.Response:
        """Bubble always; voice too unless speak=false or the character is
        voiceless. Mic mutes while the avatar talks."""
        try:
            data = await request.json()
        except json.JSONDecodeError:
            return web.json_response({"error": "bad json"}, status=400)
        text = str(data.get("text", "")).strip()
        if not text:
            return web.json_response({"error": "empty text"}, status=400)
        if len(text) > 140:
            return web.json_response({"error": "max 140 chars — it is a quip, not a blog"}, status=400)
        seconds = min(max(float(data.get("seconds", 25)), 3.0), 120.0)
        await face.send({"kind": "say", "text": text, "seconds": seconds,
                         "instant": bool(data.get("instant"))})
        transcript.append(text, who=transcript.WHO_AVATAR)  # its side persists too
        if data.get("speak", True):
            asyncio.get_running_loop().create_task(_speak(text, face.muted))
        return web.json_response({"ok": True})

    async def look(_request: web.Request) -> web.Response:
        """Webcam frame -> local VLM caption. The image stays on the LAN;
        only text leaves this handler (see eyes.py for the reasoning)."""
        from replace_me import eyes

        try:
            described = await eyes.look()
        except RuntimeError as error:
            return web.json_response({"error": str(error)}, status=503)
        await face.send({"kind": "react", "expression": "curious", "seconds": 3})
        return web.json_response({"caption": described})

    async def ui(request: web.Request) -> web.Response:
        """A face-button click -> transcript command line for the brain."""
        try:
            data = await request.json()
        except json.JSONDecodeError:
            return web.json_response({"error": "bad json"}, status=400)
        action = str(data.get("action", ""))
        if action not in UI_ACTIONS:
            return web.json_response(
                {"error": f"unknown action, pick from: {sorted(UI_ACTIONS)}"},
                status=400,
            )
        transcript.append(action, who=transcript.WHO_UI)
        return web.json_response({"ok": True})

    async def state(request: web.Request) -> web.Response:
        """The brain reports meeting/consent state; every face mirrors it.
        Partial updates merge with the cached state, so a progress-only
        post (e.g. from room_report) never clobbers an open consent
        panel."""
        try:
            data = await request.json()
        except json.JSONDecodeError:
            return web.json_response({"error": "bad json"}, status=400)
        current = face.state or {
            "meeting": False, "pending_handoff": False,
            "progress": 0.0, "buttons": True, "model": "",
        }
        face.state = {
            "meeting": bool(data.get("meeting", current["meeting"])),
            "pending_handoff": bool(
                data.get("pending_handoff", current["pending_handoff"])
            ),
            "progress": float(data.get("progress", current["progress"]) or 0.0),
            "buttons": bool(data.get("buttons", current["buttons"])),
            "model": str(data.get("model", current.get("model", ""))),
        }
        await face.send({"kind": "state", **face.state})
        return web.json_response({"ok": True})

    async def plan(request: web.Request) -> web.Response:
        """A plan card from the agent session: what the big brain is doing
        with the handed-off work. Display only — the room watches
        progress, it approves nothing here."""
        try:
            data = await request.json()
        except json.JSONDecodeError:
            return web.json_response({"error": "bad json"}, status=400)
        if data.get("clear"):
            face.plan = None
            await face.send({"kind": "plan", "clear": True})
            return web.json_response({"ok": True})
        title = str(data.get("title", "")).strip()
        steps = data.get("steps") or []
        if not title or len(title) > 80:
            return web.json_response({"error": "title required, max 80 chars"}, status=400)
        if not isinstance(steps, list) or len(steps) > 10 or any(
            not isinstance(s, str) or not s.strip() or len(s) > 120 for s in steps
        ):
            return web.json_response(
                {"error": "steps: list of up to 10 non-empty strings, max 120 chars each"},
                status=400,
            )
        status = str(data.get("status", "working"))
        if status not in {"proposed", "working", "done"}:
            return web.json_response({"error": "status: proposed|working|done"}, status=400)
        face.plan = {
            "title": title,
            "steps": [s.strip() for s in steps],
            "current": int(data.get("current", -1)),
            "status": status,
        }
        await face.send({"kind": "plan", **face.plan})
        return web.json_response({"ok": True})

    return [
        web.get("/", index),
        web.get("/ws", websocket),
        web.post("/react", react),
        web.post("/say", say),
        web.post("/look", look),
        web.post("/ui", ui),
        web.post("/state", state),
        web.post("/plan", plan),
    ]


async def _serve(demo: bool = False) -> None:
    face = _Face()

    async def sink(kind: str, payload: str) -> None:
        message: dict = {"kind": kind}
        if kind == "line":
            message["text"] = payload
            message["react"] = reflexes.react_to(payload)
        await face.send(message)

    app = web.Application()
    app.add_routes(_routes(face))
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get(PORT_ENV, DEFAULT_PORT))
    site = web.TCPSite(runner, "127.0.0.1", port)
    await site.start()
    log.info("face at http://127.0.0.1:%d/ — open it fullscreen", port)
    try:
        if demo:
            from replace_me import demo as demo_module

            log.info("DEMO MODE: scripted meeting, no mic, no model")
            await demo_module.run(sink, face)
        else:
            await ears.run(sink, muted=face.muted)
    finally:
        await runner.cleanup()


def run() -> None:
    import sys
    import tempfile

    logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")
    load_dotenv(Path.cwd() / ".env")
    load_dotenv()
    if "doctor" in sys.argv[1:]:
        from replace_me import doctor

        doctor.main()
        return
    demo = "--demo" in sys.argv[1:]
    if demo and not os.environ.get("REPLACEME_DIR"):
        # demo never pollutes a real transcript or career file
        os.environ["REPLACEME_DIR"] = tempfile.mkdtemp(prefix="replace-me-demo-")
    try:
        asyncio.run(_serve(demo=demo))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    run()

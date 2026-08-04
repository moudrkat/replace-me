"""`replace-me doctor`: says exactly what is missing before you find out
the hard way. Checks tools, mic, model endpoint, character — touches
nothing, sends nothing anywhere except one ping to your own model URL.
"""

import asyncio
import os
import shutil
import socket
import subprocess
import sys

from replace_me import character, llm

OK, BAD, WARN = "  ok   ", "  FAIL ", "  warn "


def _check(label: str, ok: bool, hint: str = "", warn: bool = False) -> bool:
    print((OK if ok else WARN if warn else BAD) + label + ("" if ok or not hint else f" — {hint}"))
    return ok or warn


async def _ping_llm() -> str | None:
    """One tiny completion against the configured endpoint; None = fine."""
    try:
        await llm.chat([{"role": "user", "content": "ping"}], max_tokens=1)
        return None
    except RuntimeError as error:
        return str(error)


def main() -> None:
    from dotenv import load_dotenv

    load_dotenv()
    healthy = True

    for tool, why in (("ffmpeg", "captures the mic and decodes images"),
                      ("ffplay", "plays the voice out loud")):
        healthy &= _check(f"{tool} on PATH", shutil.which(tool) is not None,
                          f"install ffmpeg ({why})")

    mic = os.environ.get("REPLACEME_MIC", "default")
    probe = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "pulse",
         "-i", mic, "-t", "0.3", "-f", "null", "-"],
        capture_output=True,
    ) if shutil.which("ffmpeg") else None
    healthy &= _check(
        f"microphone (source={mic})",
        probe is not None and probe.returncode == 0,
        "list sources with `pactl list short sources`, set REPLACEME_MIC",
    )

    try:
        import faster_whisper  # noqa: F401
        healthy &= _check("faster-whisper importable", True)
    except ImportError as error:
        healthy &= _check("faster-whisper importable", False, str(error))

    ch = character.get()
    found = character._find_file()
    _check(
        f"character: {ch.display_name} ({'default built-in' if found is None else found})",
        True,
    )
    _check(
        f"voice: {ch.voice or '(none — bubble only)'}",
        True,
    )

    port = int(os.environ.get("REPLACEME_PORT", "8765"))
    with socket.socket() as sock:
        in_use = sock.connect_ex(("127.0.0.1", port)) == 0
    _check(
        f"port {port} " + ("already serving (daemon running?)" if in_use else "free"),
        True,
    )

    if not llm.configured():
        healthy &= _check(
            "local model URL", False,
            f"set {llm.URL_ENV} (e.g. http://127.0.0.1:11434/v1 for ollama); "
            "without it: face + transcript only, no banter/minutes/handoff",
            warn=True,
        )
    else:
        error = asyncio.run(_ping_llm())
        healthy &= _check(
            f"model answers at {os.environ.get(llm.URL_ENV, '')}",
            error is None, error or "",
        )
        _check(
            "vision " + ("on (frames go to the model)" if llm.vision()
                         else "off (text-only backend, character admits blindness)"),
            True,
        )

    print("\n" + ("Looks employable. Run `replace-me`." if healthy
                  else "Fix the FAILs above, then try again. Or run `replace-me --demo` meanwhile."))
    sys.exit(0 if healthy else 1)


if __name__ == "__main__":
    main()

"""replace-me-style: derive the face palette from a photo, fully locally.

The photo is read from disk and sent to YOUR local model
(REPLACEME_LLM_URL, the same one the eyes use) — it is never stored,
never uploaded to any cloud API, and only five hex colors come back.
Prints a ready-to-paste `theme_*` frontmatter block for CHARACTER.md.

Honest limitation: this styles the palette. The SVG geometry (hair shape,
the earring) is still one drawing.
"""

import argparse
import asyncio
import base64
import re
import sys
from pathlib import Path

from replace_me import llm
from replace_me.character import _DEFAULT_THEME

KEYS = ("theme_hair", "theme_hair_light", "theme_fringe", "theme_eyes", "theme_skin")

PROMPT = (
    "This is a portrait photo. Reply with EXACTLY five lines, nothing "
    "else, each `key: #rrggbb`, picking colors from the photo:\n"
    "theme_hair: the dominant hair color\n"
    "theme_hair_light: a lighter shade of the hair\n"
    "theme_fringe: the lightest hair highlight\n"
    "theme_eyes: the iris color\n"
    "theme_skin: the skin tone"
)

_HEX_RE = re.compile(r"(theme_[a-z_]+)\s*:\s*[\"']?(#[0-9a-fA-F]{6})")


async def _decode_photo(path: Path) -> bytes:
    """Any image ffmpeg can read -> one reasonably sized JPEG."""
    process = await asyncio.create_subprocess_exec(
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-i", str(path), "-frames:v", "1", "-vf", "scale='min(1024,iw)':-2",
        "-f", "image2", "-c:v", "mjpeg", "-",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    frame, err = await process.communicate()
    if process.returncode != 0 or not frame:
        raise RuntimeError(f"could not decode photo: {err.decode(errors='replace')[-200:]}")
    return frame


def _parse(reply: str) -> dict[str, str]:
    return {key: color for key, color in _HEX_RE.findall(reply) if key in KEYS}


async def extract(path: Path) -> dict[str, str]:
    """Photo -> {theme key: hex}, retrying once, defaults filling gaps."""
    if not llm.vision():
        raise RuntimeError(
            "the configured model is text-only (REPLACEME_LLM_VISION=0) — "
            "a photo needs a multimodal local model"
        )
    frame = await _decode_photo(path)
    image_url = f"data:image/jpeg;base64,{base64.b64encode(frame).decode()}"
    content = [
        {"type": "text", "text": PROMPT},
        {"type": "image_url", "image_url": {"url": image_url}},
    ]
    colors: dict[str, str] = {}
    for _attempt in range(2):
        reply = await llm.chat(
            [{"role": "user", "content": content}], max_tokens=120, temperature=0.2
        )
        colors = _parse(reply)
        if len(colors) == len(KEYS):
            break
    for key in KEYS:
        if key not in colors:
            print(f"# {key}: model gave nothing usable, keeping the default", file=sys.stderr)
            colors[key] = _DEFAULT_THEME[key]
    return colors


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="replace-me-style",
        description=(
            "Derive the avatar's face colors from a photo using your LOCAL "
            "model (REPLACEME_LLM_URL). The photo never leaves your machine/"
            "LAN. Prints a theme_* block to paste into CHARACTER.md."
        ),
    )
    parser.add_argument("photo", type=Path, help="portrait image (anything ffmpeg reads)")
    args = parser.parse_args()
    if not args.photo.exists():
        parser.error(f"{args.photo} does not exist")
    from dotenv import load_dotenv

    load_dotenv()
    try:
        colors = asyncio.run(extract(args.photo))
    except RuntimeError as error:
        raise SystemExit(f"replace-me-style: {error}")
    for key in KEYS:
        print(f'{key}: "{colors[key]}"')


if __name__ == "__main__":
    main()

"""replace-me-style end to end: ffmpeg-synthesized test image, stub VLM."""

import asyncio
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _harness import check, finish, sandbox_env, serve_stub  # noqa: E402

SANDBOX = sandbox_env("style", port=9879, llm_port=9336)

import os  # noqa: E402

from aiohttp import web  # noqa: E402
from replace_me import style  # noqa: E402
from replace_me.character import _DEFAULT_THEME  # noqa: E402

GOOD_REPLY = (
    "theme_hair: #112233\ntheme_hair_light: #223344\ntheme_fringe: #334455\n"
    "theme_eyes: #445566\ntheme_skin: #556677"
)
replies: list[str] = []
requests: list[dict] = []


async def stub_llm(request: web.Request) -> web.Response:
    requests.append(await request.json())
    return web.json_response({"choices": [{"message": {"content": replies.pop(0)}}]})


def make_photo() -> Path:
    photo = SANDBOX / "portrait.png"
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
         "-i", "color=c=pink:s=64x64:d=1", "-frames:v", "1", str(photo)],
        check=True,
    )
    return photo


async def main() -> None:
    runner = await serve_stub(stub_llm, 9336, "/v1/chat/completions")
    photo = make_photo()

    # 1) clean five-color reply -> parsed as-is, image part actually sent
    replies[:] = [GOOD_REPLY]
    colors = await style.extract(photo)
    check("five colors parsed", colors["theme_hair"] == "#112233"
          and colors["theme_skin"] == "#556677", str(colors))
    content = requests[0]["messages"][0]["content"]
    check("request contained the image", isinstance(content, list)
          and any(part.get("type") == "image_url" for part in content))

    # 2) garbage reply twice -> defaults fill in, no crash
    replies[:] = ["i am a poem about hair", "still a poem"]
    requests.clear()
    colors = await style.extract(photo)
    check("garbage -> defaults", colors == {k: _DEFAULT_THEME[k] for k in style.KEYS},
          str(colors))
    check("garbage was retried once", len(requests) == 2)

    # 3) partial reply then good -> retry fixes it
    replies[:] = ["theme_hair: #aabbcc", GOOD_REPLY]
    colors = await style.extract(photo)
    check("retry recovers full set", colors["theme_eyes"] == "#445566")

    # 4) text-only backend -> clean refusal
    os.environ["REPLACEME_LLM_VISION"] = "0"
    try:
        await style.extract(photo)
        check("vision=0 refused", False)
    except RuntimeError as error:
        check("vision=0 refused", "text-only" in str(error))
    os.environ.pop("REPLACEME_LLM_VISION")

    await runner.cleanup()
    finish()


asyncio.run(main())

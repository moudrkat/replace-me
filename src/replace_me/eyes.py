"""Eyes: webcam frame -> local multimodal model -> caption. Text out, only.

The privacy rule: camera frames go to the local model on your LAN, and
only the resulting *text* ever leaves this module. There is deliberately
no code path that returns the image itself to the caller.
"""

import asyncio
import base64
import os

from replace_me import character, llm

CAMERA_ENV = "REPLACEME_CAMERA"
DEFAULT_CAMERA = "/dev/video0"


async def grab_frame(device: str | None = None) -> bytes:
    """One JPEG from the webcam via ffmpeg; raises RuntimeError on failure."""
    device = device or os.environ.get(CAMERA_ENV, DEFAULT_CAMERA)
    process = await asyncio.create_subprocess_exec(
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-f", "v4l2", "-i", device, "-frames:v", "1", "-f", "image2", "-",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    frame, err = await process.communicate()
    if process.returncode != 0 or not frame:
        raise RuntimeError(f"camera grab failed: {err.decode(errors='replace')[-200:]}")
    return frame


async def look() -> str:
    frame = await grab_frame()
    image_url = f"data:image/jpeg;base64,{base64.b64encode(frame).decode()}"
    text = await llm.chat(
        [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": character.get().caption_prompt},
                    {"type": "image_url", "image_url": {"url": image_url}},
                ],
            }
        ],
        temperature=0.3,
    )
    if not text:
        raise RuntimeError("model returned an empty caption")
    return text

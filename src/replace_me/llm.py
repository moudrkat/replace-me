"""The local model, spoken to over the OpenAI-compatible API.

One multimodal model can serve both the eyes and the brain. The OpenAI
dialect is deliberate — vLLM, ollama, llama.cpp, and brainscope all speak
it, so the backend is a URL swap, not a code change.
"""

import json
import os

import aiohttp

URL_ENV = "REPLACEME_LLM_URL"  # e.g. http://127.0.0.1:11434/v1 (ollama)
KEY_ENV = "REPLACEME_LLM_KEY"
MODEL_ENV = "REPLACEME_MODEL"
VISION_ENV = "REPLACEME_LLM_VISION"
DEFAULT_MODEL = "gemma-4-e4b"


def configured() -> bool:
    return bool(os.environ.get(URL_ENV, "").strip())


def vision() -> bool:
    """Whether the backend accepts images. Set REPLACEME_LLM_VISION=0 for
    text-only servers (e.g. brainscope) so callers never send an image_url
    part."""
    return os.environ.get(VISION_ENV, "1").strip().lower() not in {"0", "false", "no"}


async def chat(
    messages: list[dict],
    max_tokens: int = 120,
    temperature: float = 0.9,
    presence_penalty: float = 0.0,
) -> str:
    """One chat completion; raises RuntimeError with a short reason on failure."""
    base = os.environ.get(URL_ENV, "").rstrip("/")
    if not base:
        raise RuntimeError(f"{URL_ENV} is not set — where does the local model live?")
    headers = {"Content-Type": "application/json"}
    key = os.environ.get(KEY_ENV, "").strip()
    if key:
        headers["Authorization"] = f"Bearer {key}"
    payload = {
        "model": os.environ.get(MODEL_ENV, DEFAULT_MODEL),
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "presence_penalty": presence_penalty,
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{base}/chat/completions",
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=120),
            ) as response:
                if response.status != 200:
                    raise RuntimeError(f"LLM returned {response.status}: {(await response.text())[:200]}")
                data = json.loads(await response.text())
    except aiohttp.ClientError as error:
        raise RuntimeError(f"LLM unreachable: {error}") from error
    try:
        return data["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError) as error:
        raise RuntimeError(f"unexpected LLM response shape: {str(data)[:200]}") from error

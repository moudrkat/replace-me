"""Ears: microphone -> energy VAD -> faster-whisper -> transcript file.

Audio never leaves the machine; only the transcribed text does (via the
transcript file). The capture chain is plain ffmpeg over
PulseAudio/PipeWire — no PortAudio dependency.

The VAD is deliberately dumb (RMS against an adaptive noise floor with a
hangover). A meeting room with four people does not reward cleverness
here; it rewards a decent tabletop microphone.
"""

import asyncio
import contextlib
import logging
import math
import os
import re
import sys
import time
from collections.abc import Awaitable, Callable

import numpy as np  # ships with ctranslate2 (faster-whisper)

from replace_me import character, transcript

log = logging.getLogger(__name__)

RATE = 16_000
FRAME_SAMPLES = 480  # 30 ms
FRAME_BYTES = FRAME_SAMPLES * 2  # s16le mono

SPEECH_FLOOR_RATIO = 3.5  # voiced when rms exceeds noise floor by this factor
SPEECH_MIN_RMS = 250  # ...but never below this absolute level
NOISE_ADAPT = 0.05  # EMA weight for the noise floor (silence frames only)
START_FRAMES = 6  # 180 ms of voice before an utterance opens
HANG_FRAMES = 27  # ~800 ms of silence before it closes
MAX_UTTERANCE_S = 30.0  # force a cut so one monologue can't starve the queue
LOUD_RATIO = 14.0  # a bang/laugh well above floor -> "loud" event for the face

# Whisper hallucinates these on silence/music. Drop, never transcribe.
_HALLUCINATIONS = re.compile(
    r"^(thanks? (you )?for watching|subtitles by|subscribe|www\.|\.{3})",
    re.IGNORECASE,
)

# Event = ("voice"|"quiet"|"thinking"|"line"|"loud", payload)
EventSink = Callable[[str, str], Awaitable[None]]


def _mic_source() -> str:
    default = ":0" if sys.platform == "darwin" else "default"
    return os.environ.get("REPLACEME_MIC", default)


def _capture_args() -> list[str]:
    """ffmpeg input args per platform: PulseAudio/PipeWire on Linux,
    avfoundation on macOS (REPLACEME_MIC is the audio device index there,
    e.g. ":0"; list devices with
    `ffmpeg -f avfoundation -list_devices true -i ""`)."""
    source = _mic_source()
    if sys.platform == "darwin":
        if not source.startswith(":"):
            source = f":{source}"
        return ["-f", "avfoundation", "-i", source]
    return ["-f", "pulse", "-i", source]


def _load_model():
    from faster_whisper import WhisperModel  # heavy import, keep it lazy

    name = os.environ.get("REPLACEME_STT_MODEL", "small")
    log.info("loading whisper model %r (first run downloads it)...", name)
    started = time.monotonic()
    model = WhisperModel(name, device="cpu", compute_type="int8")
    log.info("model ready in %.1fs", time.monotonic() - started)
    return model


def _normalize(text: str) -> str:
    return re.sub(r"[^\w]+", "", text.lower())


def _is_self_echo(text: str) -> bool:
    """The mute window can leak the tail of the avatar's own playback (sink
    latency, room reverb). If what we just heard is contained in something
    it said in the last minute — or vice versa — it's its own voice."""
    heard = _normalize(text)
    if len(heard) < 6:
        return False
    now = time.time()
    for line in transcript.read_last(8):
        if line.who != transcript.WHO_AVATAR or now - line.ts > 60:
            continue
        said = _normalize(line.text)
        if heard in said or (len(said) >= 6 and said in heard):
            return True
    return False


def _rms(frame: bytes) -> float:
    samples = np.frombuffer(frame, dtype=np.int16).astype(np.float64)
    return math.sqrt(float(np.mean(samples * samples))) if samples.size else 0.0


def _transcribe(model, pcm: bytes) -> str:
    audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
    segments, _info = model.transcribe(
        audio, language=character.get().language, vad_filter=True, beam_size=5
    )
    text = " ".join(segment.text.strip() for segment in segments).strip()
    if not text or _HALLUCINATIONS.match(text):
        return ""
    return text


async def run(sink: EventSink, muted: "asyncio.Event | None" = None) -> None:
    """Capture forever; append utterances to the transcript, feed the face.

    While `muted` is set the stream keeps draining (the pipe must not back
    up) but nothing is processed — that's how the avatar avoids
    transcribing its own voice while it speaks.
    """
    model = await asyncio.to_thread(_load_model)
    process = await asyncio.create_subprocess_exec(
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        *_capture_args(),
        "-ac", "1", "-ar", str(RATE), "-f", "s16le", "-",
        stdout=asyncio.subprocess.PIPE,
    )
    assert process.stdout is not None
    log.info("ears open (source=%s)", _mic_source())

    noise_floor = 150.0
    voiced_run = 0
    silent_run = 0
    in_utterance = False
    utterance = bytearray()
    utterance_started = 0.0

    try:
        while True:
            frame = await process.stdout.readexactly(FRAME_BYTES)
            if muted is not None and muted.is_set():
                utterance.clear()
                in_utterance = False
                voiced_run = silent_run = 0
                continue
            rms = _rms(frame)
            threshold = max(noise_floor * SPEECH_FLOOR_RATIO, SPEECH_MIN_RMS)
            voiced = rms > threshold

            if not voiced:
                noise_floor += NOISE_ADAPT * (rms - noise_floor)
            elif rms > noise_floor * LOUD_RATIO:
                await sink("loud", "")

            if voiced:
                voiced_run += 1
                silent_run = 0
            else:
                silent_run += 1
                voiced_run = 0

            if not in_utterance:
                if voiced:
                    utterance.extend(frame)  # keep the onset
                    if voiced_run >= START_FRAMES:
                        in_utterance = True
                        utterance_started = time.monotonic()
                        await sink("voice", "")
                else:
                    utterance.clear()
                continue

            utterance.extend(frame)
            over_time = time.monotonic() - utterance_started > MAX_UTTERANCE_S
            if silent_run >= HANG_FRAMES or over_time:
                pcm = bytes(utterance)
                utterance.clear()
                in_utterance = False
                await sink("thinking", "")
                text = await asyncio.to_thread(_transcribe, model, pcm)
                if text and _is_self_echo(text):
                    log.info("dropped self-echo: %s", text)
                elif text:
                    line = transcript.append(text)
                    log.info("heard: %s", text)
                    await sink("line", line.text)
                await sink("quiet", "")
    except (asyncio.IncompleteReadError, ConnectionResetError):
        raise RuntimeError("microphone stream ended — is the source right?")
    finally:
        with contextlib.suppress(ProcessLookupError):
            process.kill()

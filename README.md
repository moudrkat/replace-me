# replace-me

> I will not wait for AI to replace me at work. If AI is going to replace
> me, **I am the one who will design that replacement.**

So here it is: my replacement, under construction, MIT licensed so you
can build yours too. An avatar that sits in your meetings as a face on a
screen. It is not ready to replace anyone yet — for now it listens, makes
faces, comments as often as you dare (from a dry remark when addressed up
to `--chatty`, which is exactly as annoying as it sounds), takes the
minutes, and when the room asks it to actually do something, it politely
checks whether it may hand the task to a bigger brain. Which, to be fair,
is already more process than some colleagues follow. And the entire
personality is one markdown file: swap the file, get a different
colleague. No code.

Fully local by default: microphone → [faster-whisper](https://github.com/SYSTRAN/faster-whisper)
on your CPU, an animated SVG face served on `localhost`, and any
OpenAI-compatible model you already run (ollama, vLLM, llama.cpp,
[brainscope](https://github.com/moudrkat/brainscope)) as the standing
brain. No cloud account required to get a face that judges your standup.

> Disclaimer: I am of course not encouraging anyone to be replaced at
> work by a pink-haired SVG. The thought never even crossed my mind.

## What it does

- **Ears** — energy VAD + whisper, everything transcribed to a local
  JSONL file. Audio never leaves the machine.
- **Face** — an SVG character in your browser: blinks, reacts to what it
  hears (regex reflexes: swearing → shocked, "deadline" → skeptical),
  gets sleepy when the meeting drags. Recording indicator always visible.
- **Voice** — a speech bubble (≤140 chars), read aloud via edge-tts.
  Leave `voice:` empty for a character that only writes.
- **Brain** — a small local model that replies when addressed by name
  (`--chatty` if you enjoy chaos), and can glance through the webcam:
  frames go to the *local* model only; text comes out.
- **Minutes** — "Avatar, start the meeting" / "end the meeting": the
  local model writes markdown minutes to `~/.replace-me/minutes/`. They
  never leave the machine.
- **Handoff** — "Avatar, fix the login bug" → it asks the room "Should I
  hand this to the big brain?" → on a spoken yes (or a button click) the
  local model writes a short task **brief** — no verbatim quotes — as a
  `[HANDOFF]` line that an attached agent session picks up.
- **Buttons** — meeting controls and Hand over / Keep it, right on the
  face page.
- **MCP server** — plug [Claude Code](https://claude.com/claude-code) (or
  any MCP client) into the room: `room_listen`, `room_say`, `room_react`,
  `room_look`, `room_recent`.

## Privacy, honestly

| What | Where it goes |
|---|---|
| room audio | nowhere — transcribed locally, deleted with the buffer |
| transcript | local JSONL file |
| camera frames | local model only; there is no code path that returns the image |
| meeting minutes | local files |
| the avatar's own bubble text | edge-tts (Microsoft) for synthesis — unless `voice:` is empty |
| handoff briefs | your attached agent session, after the room says yes |
| raw transcript via MCP | full-presence mode only; `REPLACEME_PRIVATE=1` **enforces** briefs-only (and refuses camera captions too — the camera looks at the meeting) |

One honest footnote: "local model" means **whatever `REPLACEME_LLM_URL`
points at**. Every guarantee above assumes that's your machine or your
LAN. Point it at a cloud API and the frames, transcripts-as-context, and
minutes follow the URL — the code won't stop you, so the .env is where
this promise is actually kept.

## Setup

Needs Python 3.11+, `ffmpeg`, a microphone, and some OpenAI-compatible
model server.

```bash
pip install git+https://github.com/moudrkat/replace-me
cp .env.example .env        # then edit: at minimum REPLACEME_LLM_URL
replace-me                  # daemon: ears + face at http://127.0.0.1:8765/
replace-me-brain            # the local brain (another terminal)
```

Ollama example: `REPLACEME_LLM_URL=http://127.0.0.1:11434/v1`,
`REPLACEME_MODEL=gemma-4-e4b` (multimodal, so the camera works). Text-only
backend? Set `REPLACEME_LLM_VISION=0` and the character will admit to
being blind instead of hallucinating a camera.

Plug in Claude Code:

```bash
claude mcp add replace-me replace-me-mcp
```

## Make it yours

Copy `characters/example.md` to `CHARACTER.md` and edit. That one file is
the whole personality, and the example lists **every** knob with its
default: name, wake words, voice, face colors, button labels, system
prompt, tone examples, reflex rules, every bubble and every cue regex.
Delete any section you don't care about — built-in defaults cover it.
Different language? Set `language:` (whisper + prompts follow). The
engine never needs a fork to host someone else.

Lazy mode: tell your agent session to do it. "Open CHARACTER.md and make
her 20% more dramatic" is a perfectly valid workflow — the file is plain
markdown, and the operator stays in the loop. (The avatar deliberately
cannot rewrite itself from room speech: the transcript carries no speaker
identity, and a colleague who can be reprogrammed by anyone within mic
range is not a colleague, it's a vulnerability.)

**Match it to you.** It's supposed to be *your* replacement, after all:

- `characters/interview.md` is a ready-made prompt — hand it to your
  agent session (or your local model), answer a few questions about how
  you talk in meetings and what you mock, and it writes a CHARACTER.md
  that is recognizably inspired by you without claiming to be you.
- `replace-me-style photo.jpg` derives the face colors from a portrait —
  the photo goes to your **local** model only (same rule as camera
  frames: never to any cloud API, never stored) and out comes a
  `theme_*` block to paste into your character file.

The character file format is shared with
[paralel-discordverse](https://github.com/moudrkat/paralel-discordverse) —
one character, two bodies: a face in the room and a webhook persona in
your Discord.

## Read its mind

If you are going to design your replacement, you might as well watch it
think. Point `REPLACEME_LLM_URL` at
[brainscope](https://github.com/moudrkat/brainscope) and the avatar's
every reply streams through a live view of the model's residual stream —
logit lens, attention, steering vectors, the works. Run the brain with
`--observed` and the character *knows* it is being watched mid-thought
(and may, rarely, comment on it — sticking to the real mechanism, because
inventing fake interpretability for a joke would be embarrassing).
Brainscope is text-only, so pair it with `REPLACEME_LLM_VISION=0`.

Design the replacement. Read its mind. Checkmate.

## Honest limitations

- No speaker diarization: whisper hears words, not people. Minutes name
  someone only if the room said the name out loud.
- Consent detection is regex over ASR output; "yes, hand it over" works
  better than a mumbled "yeah". That's what the buttons are for.
- The face's *colors* are themable; its geometry (the hair, the earring)
  is currently one drawing. PRs welcome.
- One brain at a time for banter: if your agent session does full room
  duty, don't run `replace-me-brain` in chatty mode too, or the avatar
  argues with itself. (Local brain + session in private mode is the
  intended pairing.)

## Tests

```bash
python3 tests/run_all.py
```

Real brain/daemon/MCP code against a stub model and a temp dir — no mic,
no TTS, no network beyond 127.0.0.1, about a minute.

## License

MIT.

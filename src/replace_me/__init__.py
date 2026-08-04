"""replace-me: a local avatar that sits in your meetings so that,
sooner or later, it can replace you at work.

Fully local by default: faster-whisper ears, an animated SVG face, a
small local model as the standing brain, and an MCP server so a real
agent session can plug in for the actual work — after the room says yes.
"""

from replace_me.transcript import Line, read_last, tail

__all__ = ["Line", "read_last", "tail"]

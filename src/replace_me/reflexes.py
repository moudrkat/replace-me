"""Reflexes: instant, dumb facial reactions to what was just heard.

Two layers of mimicry exist on purpose. This one is the spinal cord —
regex over the transcribed sentence, fires within milliseconds, no model
in the loop. The clever layer is a brain (local or an attached agent
session) reacting after actually reading the transcript; that one is
slower and smarter. Keep these rules obvious and a little conservative:
a face that overreacts to every sentence stops meaning anything.

The wake names and the rule table come from the character file
(`character.py`); first matching rule wins, order is intent.
"""

import re
from functools import lru_cache

from replace_me import character


@lru_cache(maxsize=1)
def _rules() -> list[tuple[re.Pattern[str], str]]:
    return character.get().compiled_reflexes()


def react_to(text: str) -> str | None:
    """Expression name for this utterance, or None to leave the face alone."""
    lowered = text.lower()
    if any(name.lower() in lowered for name in character.get().names):
        return "beam"  # someone said my name — light up
    for pattern, expression in _rules():
        if pattern.search(text):
            return expression
    return None

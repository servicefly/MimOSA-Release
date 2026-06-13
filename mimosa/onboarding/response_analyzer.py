"""Response-depth analysis for MimOSA onboarding (M3).

When someone answers an onboarding question we want to gauge *how much* they
shared so the conversation can adapt:

* **shallow** — one or two words ("hiking", "I'm a dev"); MimOSA should warmly
  nudge for a little more.
* **medium** — a sentence or two; a natural amount, maybe one gentle follow-up.
* **deep** — a rich, multi-sentence answer; MimOSA can simply acknowledge and
  move on.

The analysis is intentionally lightweight and deterministic (word/sentence
counts plus a few cues) so it is fully hermetic in tests and needs no models.
"""

from __future__ import annotations

import random
import re
from enum import Enum
from typing import List, Optional

__all__ = [
    "ResponseDepth",
    "analyze_response_depth",
    "is_vague",
    "encouragement_for",
]


class ResponseDepth(str, Enum):
    """How much the user revealed in an answer."""

    SHALLOW = "shallow"
    MEDIUM = "medium"
    DEEP = "deep"


_WORD_RE = re.compile(r"[^\s]+")
_SENTENCE_RE = re.compile(r"[.!?]+")

# Phrases that signal a non-answer even if a few words long.
_VAGUE_MARKERS = (
    "i don't know",
    "i dont know",
    "not sure",
    "dunno",
    "no idea",
    "nothing",
    "nope",
    "n/a",
    "na",
    "skip",
    "pass",
    "maybe",
    "idk",
    "whatever",
    "nothing really",
    "not really",
    "no",
    "none",
)


def _word_count(text: str) -> int:
    return len(_WORD_RE.findall(text or ""))


def _sentence_count(text: str) -> int:
    if not text:
        return 0
    parts = [p for p in _SENTENCE_RE.split(text) if p.strip()]
    return max(1, len(parts)) if text.strip() else 0


def is_vague(text: str) -> bool:
    """Return True when *text* is effectively a non-answer."""

    if not text or not text.strip():
        return True
    cleaned = text.strip().lower().strip(".!? ")
    if cleaned in _VAGUE_MARKERS:
        return True
    # Very short answers that are *only* a vague marker plus filler.
    words = cleaned.split()
    if len(words) <= 3 and any(m == cleaned for m in _VAGUE_MARKERS):
        return True
    return False


def analyze_response_depth(text: str) -> ResponseDepth:
    """Classify *text* as shallow, medium, or deep."""

    if is_vague(text):
        return ResponseDepth.SHALLOW

    words = _word_count(text)
    sentences = _sentence_count(text)

    if words <= 3:
        return ResponseDepth.SHALLOW
    if words >= 25 or sentences >= 3:
        return ResponseDepth.DEEP
    # 4-24 words / one or two sentences — a natural amount of detail.
    return ResponseDepth.MEDIUM


# ---------------------------------------------------------------------------
# Encouragement copy — warm, friendly nudges for thin answers.
# ---------------------------------------------------------------------------

_GENERIC_ENCOURAGEMENT = (
    "Ooh, tell me a bit more!",
    "That's a start — give me a little more to go on?",
    "Don't be shy, I'd love the details!",
    "Nice — can you say a touch more about that?",
    "I'm all ears if you want to expand on that.",
)

_VAGUE_ENCOURAGEMENT = (
    "No worries if you're not sure — even a rough idea helps!",
    "Totally fine to not have it all figured out. What comes to mind first?",
    "That's okay! Maybe just whatever pops into your head?",
)

# Topic-flavoured encouragement keyed by topic id.
_TOPIC_ENCOURAGEMENT = {
    "introduction": (
        "I'd really love to know more about you!",
        "Come on, paint me a picture — what makes you, you?",
    ),
    "professional_life": (
        "What does a typical day look like for you?",
        "What drew you to that line of work?",
    ),
    "interests_hobbies": (
        "What is it you enjoy most about it?",
        "How did you first get into that?",
    ),
    "lifestyle_preferences": (
        "What does your usual day tend to look like?",
    ),
    "system_usage": (
        "Which tools could you not live without?",
    ),
    "assistance_style": (
        "However you like to work, I'll adapt to it!",
    ),
    "relationships_goals": (
        "Even a small goal counts — what's on your mind?",
    ),
}


def encouragement_for(
    topic_id: Optional[str] = None,
    *,
    vague: bool = False,
    rng: Optional[random.Random] = None,
) -> str:
    """Return a warm nudge encouraging the user to share a bit more.

    *topic_id* lets the nudge be topic-flavoured; *vague* picks softer copy for
    "I don't know"-style answers.  A ``random.Random`` may be injected for
    deterministic tests.
    """

    chooser = rng or random
    pool: List[str] = []
    if vague:
        pool.extend(_VAGUE_ENCOURAGEMENT)
    if topic_id and topic_id in _TOPIC_ENCOURAGEMENT:
        pool.extend(_TOPIC_ENCOURAGEMENT[topic_id])
    pool.extend(_GENERIC_ENCOURAGEMENT)
    if not pool:
        return _GENERIC_ENCOURAGEMENT[0]
    return chooser.choice(pool)

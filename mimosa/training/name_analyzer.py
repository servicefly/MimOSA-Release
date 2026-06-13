"""Wake-word *name* analysis (Milestone 2, requirement #2).

Before a user commits to training a custom wake word we give them an honest,
friendly read on **how well that name is likely to work**. Some names are much
easier to detect reliably than others:

* **Syllable count** -- one-syllable names ("Max") give the model very little to
  latch onto and are easy to false-trigger or miss; two-to-three syllables
  ("Jarvis", "Computer") are the sweet spot; very long names get unwieldy.
* **Phonetic distinctiveness** -- names built from punchy, varied consonants and
  vowels ("Jarvis") stand out from background speech far better than soft,
  vowel-heavy mumbles ("Aria" said quietly).
* **Common-word / dictionary collision** -- if the name is an everyday word
  ("Computer", "Hey", "Okay") it will fire during normal conversation
  (false triggers). We flag that risk.
* **Length** -- extremely short or extremely long names both hurt accuracy.

This module is **pure logic** with no heavy dependencies, so it imports cleanly
on any machine and is trivially unit-testable. The public entry point is
:func:`analyze_wake_word`, which returns a :class:`NameAnalysis` (a dataclass
that also behaves like a ``dict`` for convenience, honouring the documented
``analyze_wake_word(name, hardware_capability) -> dict`` contract).

Nothing here ever raises on bad input -- an empty or junk name yields a result
with clear warnings and a low success probability, so the UI can guide the user
instead of crashing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, List, Mapping

# ---------------------------------------------------------------------------
# Tunable heuristics / catalogues
# ---------------------------------------------------------------------------

#: Difficulty buckets, easiest -> hardest. Mirrored into success probability.
DIFFICULTY_EASY = "easy"
DIFFICULTY_MODERATE = "moderate"
DIFFICULTY_HARD = "hard"
DIFFICULTY_VERY_HARD = "very hard"

#: Capability levels we understand (mirrors
#: :mod:`mimosa.system.capability_detector`). Anything else is treated as
#: "unknown" and estimated conservatively.
CAP_GPU = "gpu"
CAP_CPU = "cpu"

#: Rough wall-clock training-time anchors (minutes) for a typical custom model.
#: These are *estimates* shown to set expectations, not promises. GPU training
#: is dramatically faster than CPU.
_BASE_TRAIN_MINUTES_GPU = 12.0
_BASE_TRAIN_MINUTES_CPU = 95.0

#: Very common English words that, if used verbatim as a wake word, will fire
#: constantly during normal speech. Kept deliberately small and high-signal;
#: this is a false-trigger heuristic, not a full dictionary.
_COMMON_WORDS = frozenset(
    {
        "the", "a", "an", "and", "or", "but", "if", "then", "so", "as",
        "of", "to", "in", "on", "at", "by", "for", "with", "from", "up",
        "i", "you", "he", "she", "it", "we", "they", "me", "him", "her",
        "is", "am", "are", "was", "were", "be", "been", "being", "do", "did",
        "does", "have", "has", "had", "will", "would", "can", "could", "should",
        "yes", "no", "okay", "ok", "hi", "hey", "hello", "bye", "please",
        "what", "when", "where", "why", "how", "who", "which", "this", "that",
        "here", "there", "now", "today", "go", "stop", "start", "open", "close",
        "play", "pause", "next", "back", "home", "work", "time", "day", "night",
        "good", "bad", "yeah", "nope", "thanks", "sorry", "wait", "help",
        "computer", "phone", "light", "music", "volume", "call", "text",
    }
)

#: Generic, low-distinctiveness names that tend to be hard to detect or collide
#: with assistant-y speech. Used only to nudge warnings, never to block.
_WEAK_NAMES = frozenset({"hey", "okay", "ok", "yo", "hi", "computer", "assistant"})

_VOWELS = "aeiouy"
_NAME_RE = re.compile(r"[a-z]+", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass
class NameAnalysis(Mapping):
    """Structured verdict on a candidate wake word.

    Implements :class:`collections.abc.Mapping` so callers can treat it like the
    documented ``dict`` (``result["difficulty"]``, ``dict(result)``,
    ``json.dumps(result.to_dict())``) while UI/test code can use attributes.

    Attributes:
        name: The cleaned name that was analysed.
        difficulty: One of ``easy``/``moderate``/``hard``/``very hard``.
        success_probability: Estimated detection-quality score in ``[0, 1]``.
        phonetic_analysis: Sub-dict with ``syllables``, ``letters``,
            ``distinctiveness`` (0-1), ``vowel_ratio`` and a short ``summary``.
        warnings: Human-readable cautions (may be empty).
        estimated_training_time_gpu: Minutes (float) to train on a GPU.
        estimated_training_time_cpu: Minutes (float) to train on CPU only.
        recommendation: A warm, plain-language suggestion for the user.
        is_trainable: Whether the name is usable at all (False only for
            empty/garbage input).
    """

    name: str
    difficulty: str
    success_probability: float
    phonetic_analysis: Dict[str, Any]
    warnings: List[str] = field(default_factory=list)
    estimated_training_time_gpu: float = 0.0
    estimated_training_time_cpu: float = 0.0
    recommendation: str = ""
    is_trainable: bool = True

    # -- dict-like conveniences -------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        """Return a plain ``dict`` snapshot (deep-copied where it matters)."""
        return {
            "name": self.name,
            "difficulty": self.difficulty,
            "success_probability": self.success_probability,
            "phonetic_analysis": dict(self.phonetic_analysis),
            "warnings": list(self.warnings),
            "estimated_training_time_gpu": self.estimated_training_time_gpu,
            "estimated_training_time_cpu": self.estimated_training_time_cpu,
            "recommendation": self.recommendation,
            "is_trainable": self.is_trainable,
        }

    def __getitem__(self, key: str) -> Any:
        try:
            return self.to_dict()[key]
        except KeyError as exc:  # pragma: no cover - trivial
            raise KeyError(key) from exc

    def __iter__(self) -> Iterator[str]:
        return iter(self.to_dict())

    def __len__(self) -> int:
        return len(self.to_dict())

    # -- presentation helpers ---------------------------------------------

    def estimated_time_text(self, capability: str = "") -> str:
        """A friendly "about N minutes" string for the relevant hardware."""
        cap = (capability or "").strip().lower()
        minutes = (
            self.estimated_training_time_gpu
            if cap == CAP_GPU
            else self.estimated_training_time_cpu
        )
        return _humanize_minutes(minutes)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def analyze_wake_word(name: str, hardware_capability: str = "") -> NameAnalysis:
    """Analyse how well ``name`` will work as a custom wake word.

    Args:
        name: The candidate wake word (e.g. ``"Jarvis"``). Leading/trailing
            whitespace is ignored; internal spaces (multi-word phrases like
            "hey buddy") are supported.
        hardware_capability: Optional capability hint (``"gpu"``/``"cpu"``);
            only affects which training-time estimate is emphasised by helpers.
            Both GPU and CPU estimates are always populated.

    Returns:
        A :class:`NameAnalysis`. Never raises; junk input returns a non-trainable
        result with explanatory warnings.
    """
    cleaned = _clean_name(name)
    if not cleaned:
        return NameAnalysis(
            name="",
            difficulty=DIFFICULTY_VERY_HARD,
            success_probability=0.0,
            phonetic_analysis={
                "syllables": 0,
                "letters": 0,
                "distinctiveness": 0.0,
                "vowel_ratio": 0.0,
                "words": 0,
                "summary": "No pronounceable name was provided.",
            },
            warnings=["Please enter a name made of letters so MimOSA can learn it."],
            estimated_training_time_gpu=0.0,
            estimated_training_time_cpu=0.0,
            recommendation=(
                "Type a name you'd enjoy saying out loud — two or three "
                "syllables works best (for example, \"Jarvis\")."
            ),
            is_trainable=False,
        )

    words = _NAME_RE.findall(cleaned)
    letters = sum(len(w) for w in words)
    syllables = sum(_count_syllables(w) for w in words)
    vowel_ratio = _vowel_ratio(cleaned)
    distinctiveness = _distinctiveness(cleaned, words)

    warnings: List[str] = []

    # -- scoring -----------------------------------------------------------
    score = 0.70  # neutral baseline

    # Syllables: 2-3 is ideal; 1 is risky; 4+ gets unwieldy.
    if syllables <= 1:
        score -= 0.22
        warnings.append(
            "Short, one-syllable names are easy to miss or mistake for other "
            "sounds. A two- or three-syllable name detects much more reliably."
        )
    elif syllables in (2, 3):
        score += 0.16
    elif syllables == 4:
        score -= 0.04
    else:  # 5+
        score -= 0.16
        warnings.append(
            "That's quite a long wake word. Longer phrases take more effort to "
            "say each time and can be harder to train cleanly."
        )

    # Length in letters: very short or very long both hurt.
    if letters <= 2:
        score -= 0.18
        warnings.append(
            "Very short names give the model little to recognise. Try something "
            "with a few more sounds."
        )
    elif letters >= 16:
        score -= 0.08

    # Distinctiveness: reward punchy, varied phonetics.
    score += (distinctiveness - 0.5) * 0.30

    # Vowel-heavy / soft names are harder to pick out of background speech.
    if vowel_ratio >= 0.6 and syllables <= 2:
        score -= 0.08
        warnings.append(
            "This name is quite soft/vowel-heavy, which can blend into normal "
            "speech. A crisper consonant sound would stand out more."
        )

    # Common-word / dictionary collision -> false triggers.
    lowered_words = [w.lower() for w in words]
    common_hits = [w for w in lowered_words if w in _COMMON_WORDS]
    if common_hits:
        score -= 0.20
        warnings.append(
            "\u201c{}\u201d is an everyday word, so MimOSA may wake up during "
            "normal conversation (false triggers). A more unusual name avoids "
            "that.".format(common_hits[0])
        )
    if cleaned.lower() in _WEAK_NAMES:
        score -= 0.10

    success_probability = round(_clamp(score, 0.05, 0.97), 2)
    difficulty = _difficulty_for(success_probability)

    # -- training-time estimates ------------------------------------------
    # Harder names benefit from a little more training; scale gently.
    effort = 1.0 + max(0.0, (0.75 - success_probability)) * 0.8
    gpu_minutes = round(_BASE_TRAIN_MINUTES_GPU * effort, 1)
    cpu_minutes = round(_BASE_TRAIN_MINUTES_CPU * effort, 1)

    phonetic_analysis = {
        "syllables": syllables,
        "letters": letters,
        "words": len(words),
        "distinctiveness": round(distinctiveness, 2),
        "vowel_ratio": round(vowel_ratio, 2),
        "summary": _phonetic_summary(syllables, distinctiveness, vowel_ratio),
    }

    recommendation = _recommendation(
        difficulty, success_probability, syllables, bool(common_hits)
    )

    return NameAnalysis(
        name=cleaned,
        difficulty=difficulty,
        success_probability=success_probability,
        phonetic_analysis=phonetic_analysis,
        warnings=warnings,
        estimated_training_time_gpu=gpu_minutes,
        estimated_training_time_cpu=cpu_minutes,
        recommendation=recommendation,
        is_trainable=True,
    )


# ---------------------------------------------------------------------------
# Internal heuristics
# ---------------------------------------------------------------------------


def _clean_name(name: str) -> str:
    """Trim and collapse whitespace; return ``""`` if nothing usable remains."""
    try:
        text = str(name)
    except Exception:  # pragma: no cover - defensive
        return ""
    text = " ".join(text.split())
    # Keep it only if it contains at least one letter.
    return text if _NAME_RE.search(text) else ""


def _count_syllables(word: str) -> int:
    """Estimate syllables in a single word via vowel-group counting.

    A robust, dependency-free heuristic: count contiguous vowel runs, drop a
    silent trailing "e", and never return less than 1 for a non-empty word.
    """
    word = word.lower()
    if not word:
        return 0
    groups = re.findall(r"[aeiouy]+", word)
    count = len(groups)
    # Silent trailing 'e' (e.g. "jane" -> 1, not 2) -- but keep words like "the".
    if word.endswith("e") and count > 1 and not word.endswith(("le", "ie")):
        count -= 1
    return max(1, count)


def _vowel_ratio(text: str) -> float:
    """Fraction of alphabetic characters that are vowels (incl. 'y')."""
    letters = [c for c in text.lower() if c.isalpha()]
    if not letters:
        return 0.0
    vowels = sum(1 for c in letters if c in _VOWELS)
    return vowels / len(letters)


def _distinctiveness(text: str, words: List[str]) -> float:
    """Estimate phonetic distinctiveness in ``[0, 1]``.

    Rewards a varied mix of unique consonants and a balanced vowel ratio;
    penalises repetitive or extremely vowel-heavy names. This is a heuristic
    proxy for "how well this stands out from everyday background speech".
    """
    letters = [c for c in text.lower() if c.isalpha()]
    if not letters:
        return 0.0

    unique_ratio = len(set(letters)) / len(letters)

    consonants = [c for c in letters if c not in _VOWELS]
    unique_consonants = len(set(consonants))
    # Up to ~5 distinct consonants saturates the "rich consonant" signal.
    consonant_richness = min(1.0, unique_consonants / 5.0)

    vr = _vowel_ratio(text)
    # Ideal vowel ratio is ~0.4; distance from that reduces the score.
    vowel_balance = 1.0 - min(1.0, abs(vr - 0.4) / 0.4)

    score = 0.40 * unique_ratio + 0.35 * consonant_richness + 0.25 * vowel_balance
    return _clamp(score, 0.0, 1.0)


def _difficulty_for(probability: float) -> str:
    if probability >= 0.78:
        return DIFFICULTY_EASY
    if probability >= 0.60:
        return DIFFICULTY_MODERATE
    if probability >= 0.40:
        return DIFFICULTY_HARD
    return DIFFICULTY_VERY_HARD


def _phonetic_summary(syllables: int, distinctiveness: float, vowel_ratio: float) -> str:
    syl_text = (
        "one syllable" if syllables == 1 else f"{syllables} syllables"
    )
    if distinctiveness >= 0.65:
        dist_text = "a crisp, distinctive sound"
    elif distinctiveness >= 0.45:
        dist_text = "a reasonably distinctive sound"
    else:
        dist_text = "a soft sound that can blend into speech"
    return f"{syl_text.capitalize()} with {dist_text}."


def _recommendation(
    difficulty: str,
    probability: float,
    syllables: int,
    has_common_word: bool,
) -> str:
    pct = int(round(probability * 100))
    if difficulty == DIFFICULTY_EASY:
        return (
            f"Great choice! This name should work really well "
            f"(about {pct}% expected reliability). You're good to train it."
        )
    if difficulty == DIFFICULTY_MODERATE:
        tip = ""
        if syllables <= 1:
            tip = " Adding a syllable would make it even more reliable."
        elif has_common_word:
            tip = " A more unusual word would cut down on accidental wake-ups."
        return (
            f"This should work well (around {pct}% expected reliability)."
            f"{tip} Happy to train it whenever you're ready."
        )
    if difficulty == DIFFICULTY_HARD:
        return (
            f"This one's workable but a bit tricky (about {pct}% expected "
            "reliability). You can still train it, or pick a punchier two-to-"
            "three-syllable name for better results."
        )
    return (
        f"This name will be hard to detect reliably (around {pct}%). I'd gently "
        "suggest a more distinctive two- or three-syllable name — but it's your "
        "call, and you can always keep \u201cMimOSA\u201d instead."
    )


def _humanize_minutes(minutes: float) -> str:
    """Turn a minute count into a friendly approximate duration string."""
    minutes = max(0.0, float(minutes))
    if minutes < 1:
        return "under a minute"
    if minutes < 60:
        return f"about {int(round(minutes))} minutes"
    hours = minutes / 60.0
    if hours < 1.5:
        return "about an hour"
    return f"about {hours:.1f} hours"


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(value)))

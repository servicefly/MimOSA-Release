"""Memory consolidation helpers for MimOSA (M3 — Onboarding & Memory).

The onboarding conversation and continuous-learning pipeline both emit a
stream of *facts* about the user.  Over time these accumulate duplicates and
near-duplicates ("I like hiking" / "I enjoy hiking", or the same preference
phrased two slightly different ways).  This module provides small, dependency
free helpers that *consolidate* those facts so the profile and the vector
store stay tidy.

Design goals
------------
* **Never raise.**  Consolidation is a best-effort housekeeping step; if
  anything goes wrong we simply return the input unchanged.
* **No heavy deps.**  We use a cheap normalised-text + token-overlap
  similarity so the logic is fully deterministic and hermetic in tests.  When
  an embedder/semantic store is available the caller may pass a
  ``similarity`` callable to use richer semantics, but it is entirely
  optional.
* **Stable ordering.**  The first occurrence of a fact wins; later
  duplicates are merged into it.  This keeps output deterministic.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, List, Optional, Sequence

__all__ = [
    "normalise_text",
    "text_similarity",
    "consolidate_texts",
    "consolidate_facts",
    "ConsolidationResult",
]


_WORD_RE = re.compile(r"[a-z0-9']+")

# Common filler words that should not drive similarity.
_STOPWORDS = frozenset(
    {
        "a", "an", "the", "i", "im", "i'm", "me", "my", "mine", "you", "your",
        "is", "am", "are", "was", "were", "be", "been", "being", "to", "of",
        "in", "on", "at", "for", "and", "or", "but", "so", "with", "that",
        "this", "it", "as", "do", "does", "did", "really", "very", "just",
        "like", "love", "enjoy", "prefer", "want", "would", "also", "too",
        "have", "has", "had", "get", "got", "kind", "sort", "bit",
    }
)


def normalise_text(text: str) -> str:
    """Return a lowercased, whitespace-collapsed version of *text*."""

    if not text:
        return ""
    return re.sub(r"\s+", " ", str(text).strip().lower())


def _tokens(text: str) -> set:
    """Return the set of meaningful (non-stopword) tokens in *text*."""

    words = _WORD_RE.findall(normalise_text(text))
    meaningful = {w for w in words if w not in _STOPWORDS}
    # If stripping stopwords removed everything, fall back to all words so
    # short facts ("I cook") still compare sensibly.
    return meaningful or set(words)


def text_similarity(a: str, b: str) -> float:
    """Return a Jaccard-style token-overlap similarity in ``[0.0, 1.0]``."""

    na, nb = normalise_text(a), normalise_text(b)
    if not na and not nb:
        return 1.0
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    inter = len(ta & tb)
    union = len(ta | tb)
    return inter / union if union else 0.0


@dataclass
class ConsolidationResult:
    """Outcome of a consolidation pass."""

    items: List = field(default_factory=list)
    removed: int = 0

    def __iter__(self):  # pragma: no cover - convenience
        return iter(self.items)

    def __len__(self) -> int:  # pragma: no cover - convenience
        return len(self.items)


def consolidate_texts(
    texts: Iterable[str],
    *,
    threshold: float = 0.8,
    similarity: Optional[Callable[[str, str], float]] = None,
) -> ConsolidationResult:
    """Collapse near-duplicate strings, keeping the first (often longest) one.

    When two texts exceed *threshold* similarity the *longer* of the two is
    retained (more information), but its original position is preserved.
    """

    sim = similarity or text_similarity
    kept: List[str] = []
    removed = 0
    for raw in texts:
        text = (raw or "").strip()
        if not text:
            continue
        match_idx = -1
        for idx, existing in enumerate(kept):
            try:
                score = sim(text, existing)
            except Exception:
                score = text_similarity(text, existing)
            if score >= threshold:
                match_idx = idx
                break
        if match_idx == -1:
            kept.append(text)
        else:
            removed += 1
            # Prefer the more informative (longer) phrasing.
            if len(text) > len(kept[match_idx]):
                kept[match_idx] = text
    return ConsolidationResult(items=kept, removed=removed)


def _fact_key(fact: Dict) -> str:
    """Return a comparison key describing the (field, key) a fact targets."""

    field_name = str(fact.get("field", "")).strip().lower()
    sub_key = str(fact.get("key", "")).strip().lower()
    return f"{field_name}::{sub_key}" if sub_key else field_name


def _fact_value_text(fact: Dict) -> str:
    value = fact.get("value", "")
    if isinstance(value, (list, tuple)):
        return ", ".join(str(v) for v in value)
    return str(value)


def consolidate_facts(
    facts: Sequence[Dict],
    *,
    threshold: float = 0.8,
    similarity: Optional[Callable[[str, str], float]] = None,
) -> ConsolidationResult:
    """Deduplicate a list of fact dicts.

    Facts are grouped by their target ``field`` (and ``key`` for dict-valued
    fields).  Within a group, near-duplicate *values* are collapsed using
    :func:`consolidate_texts`.  The function never raises; on error it returns
    the original facts untouched.
    """

    try:
        groups: "Dict[str, List[Dict]]" = {}
        order: List[str] = []
        for fact in facts:
            if not isinstance(fact, dict):
                continue
            key = _fact_key(fact)
            if key not in groups:
                groups[key] = []
                order.append(key)
            groups[key].append(fact)

        out: List[Dict] = []
        removed = 0
        for key in order:
            group = groups[key]
            seen: List[str] = []
            for fact in group:
                value_text = _fact_value_text(fact)
                dup = False
                for existing in seen:
                    try:
                        score = (similarity or text_similarity)(value_text, existing)
                    except Exception:
                        score = text_similarity(value_text, existing)
                    if score >= threshold:
                        dup = True
                        break
                if dup:
                    removed += 1
                    continue
                seen.append(value_text)
                out.append(fact)
        return ConsolidationResult(items=out, removed=removed)
    except Exception:
        return ConsolidationResult(items=list(facts), removed=0)

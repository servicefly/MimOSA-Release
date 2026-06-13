"""Suggestion policy & lifecycle for MimOSA (M4).

:class:`SuggestionEngine` is the gatekeeper around
:class:`~mimosa.suggestions.proactive_suggester.ProactiveSuggester`. It applies
the milestone's suggestion rules:

* only surface a suggestion when **confidence > threshold** (default 0.7),
* **never suggest the same thing twice in a row**,
* respect the user's **"proactive suggestions" setting** (off = silent),
* track a **success rate** (did the user accept?) so the system stays honest.

It holds only tiny in-memory state (last suggestion + counters); nothing here
needs persistence to be useful, and everything is defensive.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from mimosa.suggestions.proactive_suggester import ProactiveSuggester, Suggestion

logger = logging.getLogger(__name__)

__all__ = ["SuggestionEngine"]


class SuggestionEngine:
    """Decide whether/what to suggest, and learn from the outcome.

    Args:
        suggester: A :class:`ProactiveSuggester` (created lazily if omitted).
        min_confidence: Minimum confidence to surface a suggestion.
        enabled: Master on/off (mirror of the user's setting).
        pattern_detector / context_analyzer / profile_manager: forwarded to a
            lazily-created suggester when ``suggester`` is not supplied.
    """

    def __init__(
        self,
        *,
        suggester: Any = None,
        min_confidence: float = 0.7,
        enabled: bool = True,
        pattern_detector: Any = None,
        context_analyzer: Any = None,
        profile_manager: Any = None,
    ) -> None:
        self.min_confidence = float(min_confidence)
        self.enabled = bool(enabled)
        if suggester is not None:
            self.suggester = suggester
        else:
            self.suggester = ProactiveSuggester(
                context_analyzer=context_analyzer,
                pattern_detector=pattern_detector,
                profile_manager=profile_manager,
            )
        self._last_key: Optional[str] = None
        self._offered = 0
        self._accepted = 0

    # -- main API ---------------------------------------------------------
    def get_suggestion(
        self, *, now: Optional[float] = None, busy: bool = False
    ) -> Optional[Suggestion]:
        """Return a suggestion to surface, or ``None``. Never raises."""
        if not self.enabled or busy:
            return None
        try:
            candidates = self.suggester.make_suggestions(now=now)
        except Exception:  # pragma: no cover - defensive
            return None
        for cand in candidates:
            if cand.confidence < self.min_confidence:
                continue
            if cand.key and cand.key == self._last_key:
                continue  # don't repeat the same thing back-to-back
            return cand
        return None

    def offer(self, suggestion: Suggestion) -> None:
        """Record that *suggestion* was actually shown to the user."""
        if suggestion is None:
            return
        self._last_key = suggestion.key or suggestion.text
        self._offered += 1

    def record_outcome(self, accepted: bool) -> None:
        """Record whether the user accepted the last offered suggestion."""
        if accepted:
            self._accepted += 1

    # -- introspection ----------------------------------------------------
    @property
    def offered_count(self) -> int:
        return self._offered

    @property
    def accepted_count(self) -> int:
        return self._accepted

    def success_rate(self) -> float:
        """Fraction of offered suggestions the user accepted (0.0 if none)."""
        return (self._accepted / self._offered) if self._offered else 0.0

    def stats(self) -> Dict[str, Any]:
        return {
            "offered": self._offered,
            "accepted": self._accepted,
            "success_rate": round(self.success_rate(), 3),
            "enabled": self.enabled,
        }

    def reset_repeat_guard(self) -> None:
        """Allow the last suggestion to be offered again."""
        self._last_key = None

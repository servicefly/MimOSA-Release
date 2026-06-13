"""Generate context-aware suggestions for MimOSA (M4).

:class:`ProactiveSuggester` looks at *what's happening now* (via
:class:`~mimosa.learning.context_analyzer.ContextAnalyzer`) and *what it has
learned* (via :class:`~mimosa.learning.pattern_detector.PatternDetector` and the
user profile) to produce candidate :class:`Suggestion` objects.

It only *generates* ideas -- the surrounding :class:`SuggestionEngine` decides
whether one is worth surfacing (confidence gate, repetition guard, settings).
Generation is deterministic and never raises.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

__all__ = ["Suggestion", "ProactiveSuggester"]


@dataclass
class Suggestion:
    """A helpful nudge MimOSA could offer.

    Attributes:
        text: The conversational suggestion ("Want me to open VS Code?").
        kind: "tool" | "break" | "schedule" | "wellbeing" | "interest".
        confidence: ``0.0..1.0`` -- how relevant/likely-helpful it is.
        key: Stable key for de-dup / "don't repeat" tracking.
        action_hint: Optional machine hint for the caller (e.g. ``"open:code"``).
    """

    text: str
    kind: str = "tool"
    confidence: float = 0.0
    key: str = ""
    action_hint: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "text": self.text,
            "kind": self.kind,
            "confidence": round(float(self.confidence), 3),
            "key": self.key,
            "action_hint": self.action_hint,
            "metadata": dict(self.metadata),
        }


class ProactiveSuggester:
    """Build candidate suggestions from context + learned behaviour.

    Args:
        context_analyzer: A :class:`ContextAnalyzer` (created lazily if omitted).
        pattern_detector: Optional :class:`PatternDetector` for tool patterns.
        profile_manager: Optional :class:`ProfileManager` for interests, etc.
    """

    def __init__(
        self,
        *,
        context_analyzer: Any = None,
        pattern_detector: Any = None,
        profile_manager: Any = None,
    ) -> None:
        self.pattern_detector = pattern_detector
        self.profile_manager = profile_manager
        if context_analyzer is not None:
            self.context_analyzer = context_analyzer
        else:
            from mimosa.learning.context_analyzer import ContextAnalyzer

            self.context_analyzer = ContextAnalyzer(pattern_detector)

    def make_suggestions(
        self, *, now: Optional[float] = None
    ) -> List[Suggestion]:
        """Return candidate suggestions, highest-confidence first. Never raises."""
        out: List[Suggestion] = []
        try:
            ctx = self.context_analyzer.analyze(now)
        except Exception:  # pragma: no cover - defensive
            return out
        try:
            out.extend(self._tool_suggestions(ctx))
        except Exception:  # pragma: no cover - defensive
            logger.debug("tool suggestions failed", exc_info=True)
        try:
            out.extend(self._wellbeing_suggestions(ctx))
        except Exception:  # pragma: no cover - defensive
            logger.debug("wellbeing suggestions failed", exc_info=True)
        out.sort(key=lambda s: s.confidence, reverse=True)
        return out

    # -- generators -------------------------------------------------------
    def _tool_suggestions(self, ctx) -> List[Suggestion]:
        out: List[Suggestion] = []
        if self.pattern_detector is None:
            return out
        # Suggest the dominant tool during work hours.
        if not ctx.is_work_hours:
            return out
        for pat in self.pattern_detector.detect_patterns():
            if pat.kind != "tool":
                continue
            tool = pat.metadata.get("tool", pat.key.split(":")[-1])
            label = str(tool).capitalize()
            out.append(
                Suggestion(
                    text=(
                        f"It's {ctx.part_of_day} and you usually use {label} "
                        f"around now — want me to open it?"
                    ),
                    kind="tool",
                    confidence=round(min(1.0, pat.confidence), 3),
                    key=f"tool:{tool}:{ctx.part_of_day}",
                    action_hint=f"open:{tool}",
                    metadata={"tool": tool},
                )
            )
            break  # only the single most-used tool
        return out

    def _wellbeing_suggestions(self, ctx) -> List[Suggestion]:
        out: List[Suggestion] = []
        # Late-night gentle nudge.
        if ctx.part_of_day == "night" and ctx.hour >= 23:
            out.append(
                Suggestion(
                    text="It's getting late — want to wrap up and pick this up tomorrow?",
                    kind="wellbeing",
                    confidence=0.72,
                    key="wellbeing:late_night",
                )
            )
        return out

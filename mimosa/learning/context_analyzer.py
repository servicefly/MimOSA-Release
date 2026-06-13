"""Current-moment context analysis for MimOSA (M4).

:class:`ContextAnalyzer` answers the question *"what's going on right now?"* so
the proactive systems can make timely, relevant suggestions ("it's 9am, you
usually start coding now…"). It deliberately holds **no** long-term state -- it
combines the wall clock with the (optional) :class:`PatternDetector` to produce
a small, immutable :class:`ContextSnapshot`.

Everything is local and side-effect free; :meth:`analyze` never raises.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

__all__ = ["ContextSnapshot", "ContextAnalyzer", "time_of_day"]


def time_of_day(hour: int) -> str:
    """Map a 0-23 hour to a friendly part-of-day label."""
    try:
        h = int(hour) % 24
    except Exception:
        return "day"
    if 5 <= h < 12:
        return "morning"
    if 12 <= h < 17:
        return "afternoon"
    if 17 <= h < 22:
        return "evening"
    return "night"


@dataclass
class ContextSnapshot:
    """An immutable summary of the present moment.

    Attributes:
        timestamp: Epoch seconds the snapshot was taken.
        hour: Hour of day (0-23).
        weekday: 0=Monday .. 6=Sunday.
        part_of_day: "morning"/"afternoon"/"evening"/"night".
        is_weekend: Whether it's Saturday/Sunday.
        is_work_hours: Whether it's a weekday between 9am-5pm.
        active_patterns: Pattern descriptions currently considered relevant.
        greeting: A time-appropriate greeting phrase.
    """

    timestamp: float
    hour: int
    weekday: int
    part_of_day: str
    is_weekend: bool
    is_work_hours: bool
    active_patterns: List[str] = field(default_factory=list)
    greeting: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "hour": self.hour,
            "weekday": self.weekday,
            "part_of_day": self.part_of_day,
            "is_weekend": self.is_weekend,
            "is_work_hours": self.is_work_hours,
            "active_patterns": list(self.active_patterns),
            "greeting": self.greeting,
        }


_GREETINGS = {
    "morning": "Good morning",
    "afternoon": "Good afternoon",
    "evening": "Good evening",
    "night": "Hi",
}


class ContextAnalyzer:
    """Summarise the current moment for proactive features.

    Args:
        pattern_detector: Optional :class:`PatternDetector` used to surface
            currently-relevant behavioural patterns. May be ``None``.
        clock: Callable returning epoch seconds (injectable for tests).
    """

    def __init__(self, pattern_detector=None, *, clock=time.time) -> None:
        self.pattern_detector = pattern_detector
        self._clock = clock

    def analyze(self, now: Optional[float] = None) -> ContextSnapshot:
        """Return a :class:`ContextSnapshot` for *now* (or the current time)."""
        try:
            ts = float(now) if now is not None else float(self._clock())
        except Exception:  # pragma: no cover - defensive
            ts = time.time()
        try:
            dt = datetime.fromtimestamp(ts)
            hour, weekday = dt.hour, dt.weekday()
        except Exception:  # pragma: no cover - defensive
            hour, weekday = 12, 0
        part = time_of_day(hour)
        is_weekend = weekday >= 5
        is_work_hours = (not is_weekend) and (9 <= hour < 17)
        patterns: List[str] = []
        if self.pattern_detector is not None:
            try:
                patterns = [
                    p.description
                    for p in self.pattern_detector.detect_patterns()
                ]
            except Exception:  # pragma: no cover - defensive
                patterns = []
        return ContextSnapshot(
            timestamp=ts,
            hour=hour,
            weekday=weekday,
            part_of_day=part,
            is_weekend=is_weekend,
            is_work_hours=is_work_hours,
            active_patterns=patterns,
            greeting=_GREETINGS.get(part, "Hi"),
        )

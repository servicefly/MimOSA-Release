"""Behavioural pattern detection for MimOSA (M4).

:class:`PatternDetector` ingests a stream of lightweight *events* (a tool was
opened, a message was sent, a task completed) and surfaces **patterns** MimOSA
can act on -- e.g. "uses Firefox a lot", "works 9am-5pm on weekdays", "tends to
give short answers".

Design
------
* **Deterministic & dependency-free.** Counts and simple ratios only -- no ML,
  so it's fully hermetic in tests and cheap at runtime.
* **Never raises.** Recording and detection swallow errors; a bad event is
  dropped rather than crashing the conversation loop.
* **Persistable.** :meth:`to_state` / :meth:`load_state` round-trip to a small
  JSON document (see :func:`mimosa.memory.paths.patterns_path`) so patterns
  survive restarts and strengthen over time.

A *pattern* is only reported once it has enough supporting evidence
(``min_count``) and enough confidence (``min_confidence``), so MimOSA never
jumps to conclusions from a single data point.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from mimosa.memory.paths import patterns_path

logger = logging.getLogger(__name__)

__all__ = ["DetectedPattern", "PatternDetector"]

#: Event kinds the detector understands.
KIND_TOOL = "tool"
KIND_MESSAGE = "message"
KIND_TASK = "task"

#: A "short" user message (words) hints at a terse communication style.
SHORT_MESSAGE_WORDS = 6
#: A "long" message hints at a detailed communication style.
LONG_MESSAGE_WORDS = 30


@dataclass
class DetectedPattern:
    """A behavioural pattern MimOSA has noticed.

    Attributes:
        kind: One of ``"tool"``, ``"time"``, ``"communication"``, ``"task"``.
        key: A stable machine key (e.g. ``"tool:firefox"``) for dedup/lookup.
        description: A friendly, human-readable sentence ("Uses Firefox a lot").
        confidence: ``0.0..1.0`` -- how sure MimOSA is.
        count: How many supporting observations back this pattern.
        metadata: Extra structured detail (counts, ranges…).
    """

    kind: str
    key: str
    description: str
    confidence: float = 0.0
    count: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "key": self.key,
            "description": self.description,
            "confidence": round(float(self.confidence), 3),
            "count": int(self.count),
            "metadata": dict(self.metadata),
        }


def _normalise_name(name: str) -> str:
    return str(name or "").strip().lower()


class PatternDetector:
    """Aggregate events and detect behavioural patterns.

    Args:
        state_path: Where to persist counters. Defaults to
            :func:`mimosa.memory.paths.patterns_path`. Pass ``None`` to stay
            purely in-memory (handy for tests).
        autosave: Persist after each recorded event when ``True``.
        clock: Callable returning the current epoch seconds (injectable for
            deterministic tests).
    """

    def __init__(
        self,
        state_path: Optional[Union[str, Path]] = "__default__",
        *,
        autosave: bool = False,
        clock=time.time,
    ) -> None:
        if state_path == "__default__":
            try:
                state_path = patterns_path()
            except Exception:  # pragma: no cover - defensive
                state_path = None
        self.state_path = Path(state_path).expanduser() if state_path else None
        self.autosave = autosave
        self._clock = clock
        #: tool name -> count
        self._tool_counts: Dict[str, int] = {}
        #: hour-of-day (0-23) -> count of activity
        self._hour_counts: Dict[int, int] = {}
        #: weekday (0=Mon..6=Sun) -> count
        self._weekday_counts: Dict[int, int] = {}
        #: message length buckets
        self._short_messages = 0
        self._long_messages = 0
        self._total_messages = 0
        self._task_count = 0
        if self.state_path is not None and self.state_path.exists():
            self.load_state()

    # -- recording --------------------------------------------------------
    def record_event(
        self,
        kind: str,
        name: str = "",
        *,
        timestamp: Optional[float] = None,
        text: str = "",
    ) -> None:
        """Record a single event. Never raises."""
        try:
            ts = float(timestamp) if timestamp is not None else float(self._clock())
            self._record_time(ts)
            k = _normalise_name(kind)
            if k == KIND_TOOL and name:
                tool = _normalise_name(name)
                self._tool_counts[tool] = self._tool_counts.get(tool, 0) + 1
            elif k == KIND_MESSAGE:
                self._record_message_length(text or name)
            elif k == KIND_TASK:
                self._task_count += 1
            self._maybe_save()
        except Exception:  # pragma: no cover - defensive
            logger.debug("Could not record event %r/%r", kind, name, exc_info=True)

    def record_tool_use(self, tool_name: str, *, timestamp: Optional[float] = None) -> None:
        """Convenience wrapper for ``record_event(KIND_TOOL, tool_name)``."""
        self.record_event(KIND_TOOL, tool_name, timestamp=timestamp)

    def record_message(self, text: str, *, timestamp: Optional[float] = None) -> None:
        """Record a user message (drives communication-style detection)."""
        self.record_event(KIND_MESSAGE, timestamp=timestamp, text=text)

    def record_task(self, *, timestamp: Optional[float] = None) -> None:
        """Record a completed task."""
        self.record_event(KIND_TASK, timestamp=timestamp)

    # -- internal counters ------------------------------------------------
    def _record_time(self, ts: float) -> None:
        try:
            dt = datetime.fromtimestamp(ts)
            self._hour_counts[dt.hour] = self._hour_counts.get(dt.hour, 0) + 1
            self._weekday_counts[dt.weekday()] = (
                self._weekday_counts.get(dt.weekday(), 0) + 1
            )
        except Exception:  # pragma: no cover - defensive
            pass

    def _record_message_length(self, text: str) -> None:
        words = len(str(text or "").split())
        if words <= 0:
            return
        self._total_messages += 1
        if words <= SHORT_MESSAGE_WORDS:
            self._short_messages += 1
        elif words >= LONG_MESSAGE_WORDS:
            self._long_messages += 1

    # -- queries ----------------------------------------------------------
    def tool_count(self, tool_name: str) -> int:
        return self._tool_counts.get(_normalise_name(tool_name), 0)

    def top_tools(self, n: int = 5) -> List[tuple]:
        return sorted(self._tool_counts.items(), key=lambda kv: kv[1], reverse=True)[:n]

    def detect_patterns(
        self, *, min_count: int = 5, min_confidence: float = 0.6
    ) -> List[DetectedPattern]:
        """Return all patterns meeting the evidence/confidence bars. Sorted by
        descending confidence. Never raises."""
        patterns: List[DetectedPattern] = []
        try:
            patterns.extend(self._detect_tool_patterns(min_count))
            patterns.extend(self._detect_time_patterns(min_count))
            patterns.extend(self._detect_communication_pattern(min_count))
        except Exception:  # pragma: no cover - defensive
            logger.debug("detect_patterns failed", exc_info=True)
        filtered = [p for p in patterns if p.confidence >= min_confidence]
        filtered.sort(key=lambda p: p.confidence, reverse=True)
        return filtered

    def _detect_tool_patterns(self, min_count: int) -> List[DetectedPattern]:
        out: List[DetectedPattern] = []
        total = sum(self._tool_counts.values())
        for tool, count in self._tool_counts.items():
            if count < min_count:
                continue
            # Confidence grows with absolute usage and dominance over others.
            dominance = (count / total) if total else 0.0
            volume = min(1.0, count / (min_count * 2.0))
            confidence = round(min(1.0, 0.5 * volume + 0.5 * dominance + 0.3), 3)
            confidence = min(1.0, confidence)
            label = tool.capitalize()
            out.append(
                DetectedPattern(
                    kind="tool",
                    key=f"tool:{tool}",
                    description=f"Uses {label} a lot",
                    confidence=confidence,
                    count=count,
                    metadata={"tool": tool, "uses": count, "dominance": round(dominance, 2)},
                )
            )
        return out

    def _detect_time_patterns(self, min_count: int) -> List[DetectedPattern]:
        out: List[DetectedPattern] = []
        total = sum(self._hour_counts.values())
        if total < min_count:
            return out
        work_hours = sum(c for h, c in self._hour_counts.items() if 9 <= h < 17)
        ratio = work_hours / total if total else 0.0
        if ratio >= 0.6:
            out.append(
                DetectedPattern(
                    kind="time",
                    key="time:work_hours",
                    description="Most active during work hours (9am-5pm)",
                    confidence=round(min(1.0, ratio), 3),
                    count=total,
                    metadata={"work_hour_ratio": round(ratio, 2)},
                )
            )
        # Weekday vs weekend
        wd_total = sum(self._weekday_counts.values())
        if wd_total >= min_count:
            weekday = sum(c for d, c in self._weekday_counts.items() if d < 5)
            wd_ratio = weekday / wd_total if wd_total else 0.0
            if wd_ratio >= 0.7:
                out.append(
                    DetectedPattern(
                        kind="time",
                        key="time:weekdays",
                        description="Mainly active on weekdays",
                        confidence=round(min(1.0, wd_ratio), 3),
                        count=wd_total,
                        metadata={"weekday_ratio": round(wd_ratio, 2)},
                    )
                )
        return out

    def _detect_communication_pattern(self, min_count: int) -> List[DetectedPattern]:
        out: List[DetectedPattern] = []
        if self._total_messages < min_count:
            return out
        short_ratio = self._short_messages / self._total_messages
        long_ratio = self._long_messages / self._total_messages
        if short_ratio >= 0.6:
            out.append(
                DetectedPattern(
                    kind="communication",
                    key="comm:concise",
                    description="Prefers short, to-the-point messages",
                    confidence=round(min(1.0, short_ratio), 3),
                    count=self._total_messages,
                    metadata={"short_ratio": round(short_ratio, 2)},
                )
            )
        elif long_ratio >= 0.5:
            out.append(
                DetectedPattern(
                    kind="communication",
                    key="comm:detailed",
                    description="Tends to share detailed messages",
                    confidence=round(min(1.0, long_ratio), 3),
                    count=self._total_messages,
                    metadata={"long_ratio": round(long_ratio, 2)},
                )
            )
        return out

    # -- persistence ------------------------------------------------------
    def to_state(self) -> Dict[str, Any]:
        return {
            "version": 1,
            "tool_counts": dict(self._tool_counts),
            "hour_counts": {str(k): v for k, v in self._hour_counts.items()},
            "weekday_counts": {str(k): v for k, v in self._weekday_counts.items()},
            "short_messages": self._short_messages,
            "long_messages": self._long_messages,
            "total_messages": self._total_messages,
            "task_count": self._task_count,
        }

    def load_state(self, state: Optional[Dict[str, Any]] = None) -> bool:
        """Load counters from *state* (or from disk if omitted). Never raises."""
        try:
            if state is None:
                if self.state_path is None or not self.state_path.exists():
                    return False
                with open(self.state_path, "r", encoding="utf-8") as fh:
                    state = json.load(fh)
            if not isinstance(state, dict):
                return False
            self._tool_counts = {
                str(k): int(v) for k, v in (state.get("tool_counts") or {}).items()
            }
            self._hour_counts = {
                int(k): int(v) for k, v in (state.get("hour_counts") or {}).items()
            }
            self._weekday_counts = {
                int(k): int(v) for k, v in (state.get("weekday_counts") or {}).items()
            }
            self._short_messages = int(state.get("short_messages", 0))
            self._long_messages = int(state.get("long_messages", 0))
            self._total_messages = int(state.get("total_messages", 0))
            self._task_count = int(state.get("task_count", 0))
            return True
        except Exception:  # pragma: no cover - corrupt state => fresh start
            logger.debug("Could not load pattern state", exc_info=True)
            return False

    def save(self) -> bool:
        """Atomically persist counters to disk. Never raises."""
        if self.state_path is None:
            return False
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=str(self.state_path.parent), suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    json.dump(self.to_state(), fh, indent=2)
                os.replace(tmp, self.state_path)
            finally:
                if os.path.exists(tmp):
                    os.remove(tmp)
            return True
        except Exception:  # pragma: no cover - best-effort
            logger.debug("Could not save pattern state", exc_info=True)
            return False

    def _maybe_save(self) -> None:
        if self.autosave:
            self.save()

    def clear(self) -> None:
        """Forget all observed patterns (privacy control)."""
        self._tool_counts.clear()
        self._hour_counts.clear()
        self._weekday_counts.clear()
        self._short_messages = 0
        self._long_messages = 0
        self._total_messages = 0
        self._task_count = 0
        self._maybe_save()

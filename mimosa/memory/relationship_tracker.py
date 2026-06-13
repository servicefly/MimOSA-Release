"""Relationship-depth tracking for MimOSA (M4).

MimOSA is meant to feel like a companion that *grows* with you. The
:class:`RelationshipTracker` records the simple facts that let MimOSA's tone
evolve from a polite new acquaintance into an old friend:

* how long you've used MimOSA (days since install),
* how many conversations you've had,
* how many tasks you've completed together,
* how much you've shared (known profile facts).

From those it computes a **stage** -- ``new`` -> ``familiar`` -> ``close`` --
and a short *tone guidance* string that skills inject into the LLM system
prompt so replies get warmer and more concise as the relationship deepens.

State persists to ``relationship.json``. Everything is local and defensive.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Union

from mimosa.memory.paths import relationship_path

logger = logging.getLogger(__name__)

__all__ = [
    "RelationshipTracker",
    "RelationshipSummary",
    "STAGE_NEW",
    "STAGE_FAMILIAR",
    "STAGE_CLOSE",
]

STAGE_NEW = "new"
STAGE_FAMILIAR = "familiar"
STAGE_CLOSE = "close"

_DAY_SECONDS = 86400.0

#: Tone guidance injected into the system prompt per stage.
_STAGE_TONE = {
    STAGE_NEW: (
        "You and the user are still getting to know each other. Be warm, "
        "polite and a little more explanatory; offer choices and confirm "
        "preferences rather than assuming them."
    ),
    STAGE_FAMILIAR: (
        "You and the user know each other reasonably well now. Be friendly and "
        "relaxed, assume some shared context, and lean on what you've learned "
        "about their preferences without over-explaining."
    ),
    STAGE_CLOSE: (
        "You and the user are close, like old friends. Be casual, concise and "
        "proactive; use the preferences you know, skip the hand-holding, and "
        "just get things done with an easy, familiar tone."
    ),
}


@dataclass
class RelationshipSummary:
    """Human-readable snapshot for the Settings UI."""

    stage: str
    days_known: int
    conversations: int
    tasks_completed: int
    facts_shared: int

    def friendly_lines(self) -> Dict[str, str]:
        day_word = "day" if self.days_known == 1 else "days"
        return {
            "duration": f"Friends for: {self.days_known} {day_word}",
            "conversations": f"Conversations: {self.conversations}",
            "tasks": f"Tasks done together: {self.tasks_completed}",
            "stage": f"Relationship depth: {self.stage.capitalize()}",
        }


class RelationshipTracker:
    """Track and evolve the MimOSA↔user relationship over time.

    Args:
        state_path: JSON state file. Defaults to
            :func:`mimosa.memory.paths.relationship_path`. ``None`` keeps state
            in memory only (tests).
        autosave: Persist after each mutation when ``True``.
        clock: Injectable epoch-seconds clock for deterministic tests.
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
                state_path = relationship_path()
            except Exception:  # pragma: no cover - defensive
                state_path = None
        self.state_path = Path(state_path).expanduser() if state_path else None
        self.autosave = autosave
        self._clock = clock
        self.install_date: float = 0.0
        self.total_conversations: int = 0
        self.total_tasks_completed: int = 0
        self.facts_shared: int = 0
        loaded = False
        if self.state_path is not None and self.state_path.exists():
            loaded = self.load()
        if not loaded and not self.install_date:
            # First-ever run: stamp the install date now.
            self.install_date = float(self._clock())
            self._maybe_save()

    # -- recording --------------------------------------------------------
    def record_conversation(self, count: int = 1) -> None:
        try:
            self.total_conversations += max(0, int(count))
            self._maybe_save()
        except Exception:  # pragma: no cover - defensive
            pass

    def record_task(self, count: int = 1) -> None:
        try:
            self.total_tasks_completed += max(0, int(count))
            self._maybe_save()
        except Exception:  # pragma: no cover - defensive
            pass

    def set_facts_shared(self, count: int) -> None:
        try:
            self.facts_shared = max(0, int(count))
            self._maybe_save()
        except Exception:  # pragma: no cover - defensive
            pass

    # -- computation ------------------------------------------------------
    def days_since_install(self, now: Optional[float] = None) -> int:
        ts = float(now) if now is not None else float(self._clock())
        if not self.install_date:
            return 0
        return max(0, int((ts - self.install_date) // _DAY_SECONDS))

    def stage(self, now: Optional[float] = None) -> str:
        """Return the current relationship stage.

        A stage is reached when *either* the time threshold *or* the
        conversation threshold is met -- so an intense first week can deepen the
        bond faster, and a light-but-long relationship deepens with time.
        """
        days = self.days_since_install(now)
        convos = self.total_conversations
        if days >= 30 or convos >= 200:
            return STAGE_CLOSE
        if days >= 7 or convos >= 50:
            return STAGE_FAMILIAR
        return STAGE_NEW

    def tone_guidance(self, now: Optional[float] = None) -> str:
        """Return the system-prompt tone clause for the current stage."""
        return _STAGE_TONE.get(self.stage(now), _STAGE_TONE[STAGE_NEW])

    def summary(self, now: Optional[float] = None) -> RelationshipSummary:
        return RelationshipSummary(
            stage=self.stage(now),
            days_known=self.days_since_install(now),
            conversations=self.total_conversations,
            tasks_completed=self.total_tasks_completed,
            facts_shared=self.facts_shared,
        )

    # -- persistence ------------------------------------------------------
    def to_state(self) -> Dict[str, Any]:
        return {
            "version": 1,
            "install_date": self.install_date,
            "total_conversations": self.total_conversations,
            "total_tasks_completed": self.total_tasks_completed,
            "facts_shared": self.facts_shared,
        }

    def load(self, state: Optional[Dict[str, Any]] = None) -> bool:
        try:
            if state is None:
                if self.state_path is None or not self.state_path.exists():
                    return False
                with open(self.state_path, "r", encoding="utf-8") as fh:
                    state = json.load(fh)
            if not isinstance(state, dict):
                return False
            self.install_date = float(state.get("install_date", 0.0) or 0.0)
            self.total_conversations = int(state.get("total_conversations", 0) or 0)
            self.total_tasks_completed = int(state.get("total_tasks_completed", 0) or 0)
            self.facts_shared = int(state.get("facts_shared", 0) or 0)
            return True
        except Exception:  # pragma: no cover - corrupt => fresh
            logger.debug("Could not load relationship state", exc_info=True)
            return False

    def save(self) -> bool:
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
            logger.debug("Could not save relationship state", exc_info=True)
            return False

    def _maybe_save(self) -> None:
        if self.autosave:
            self.save()

    def reset(self) -> None:
        """Reset the relationship (e.g. on "clear all memories")."""
        self.install_date = float(self._clock())
        self.total_conversations = 0
        self.total_tasks_completed = 0
        self.facts_shared = 0
        self._maybe_save()

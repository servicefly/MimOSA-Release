"""Proactive question scheduling for MimOSA (M4).

A good friend asks about your life -- but picks their moment and doesn't
interrogate you. :class:`ProactiveQuestioner` is MimOSA's sense of tact:

* It keeps a **pending** queue of candidate questions (fed from
  :class:`~mimosa.learning.continuous_learner.LearningOpportunity` objects and
  detected patterns).
* It records what's already been **asked** (and the answer) so it never repeats
  itself.
* It enforces a **daily rate limit** (from the user's
  :class:`~mimosa.utils.config.LearningSettings`) so MimOSA never feels naggy.
* It refuses to ask during **urgent** moments -- the caller passes ``busy`` /
  ``urgent`` context and gets ``None`` back.

State persists to ``proactive_questions.json`` matching the milestone's
documented shape (``asked`` / ``pending`` / ``patterns_detected``). Every method
is defensive and never raises into the conversation loop.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from mimosa.memory.paths import proactive_questions_path

logger = logging.getLogger(__name__)

__all__ = ["ProactiveQuestion", "ProactiveQuestioner"]

_PRIORITY_RANK = {"high": 3, "medium": 2, "low": 1}


@dataclass
class ProactiveQuestion:
    """A single question MimOSA might ask.

    Attributes:
        text: The conversational question itself.
        kind: "preference" | "context" | "pattern" | "optimization".
        priority: "low" | "medium" | "high".
        subject: What it concerns (tool/person/pattern key) -- used for dedup.
        id: Stable identifier.
        created_at: Epoch seconds when enqueued.
    """

    text: str
    kind: str = "preference"
    priority: str = "low"
    subject: str = ""
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "question": self.text,
            "kind": self.kind,
            "priority": self.priority,
            "subject": self.subject,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ProactiveQuestion":
        return cls(
            text=str(data.get("question") or data.get("text") or ""),
            kind=str(data.get("kind") or "preference"),
            priority=str(data.get("priority") or "low"),
            subject=str(data.get("subject") or ""),
            id=str(data.get("id") or uuid.uuid4().hex[:12]),
            created_at=float(data.get("created_at") or time.time()),
        )


class ProactiveQuestioner:
    """Decide *when* and *what* MimOSA asks, politely.

    Args:
        state_path: JSON state file. Defaults to
            :func:`mimosa.memory.paths.proactive_questions_path`. ``None`` keeps
            state in memory only (tests).
        max_per_day: Daily question cap. May be refreshed from settings via
            :meth:`set_daily_limit`.
        autosave: Persist after each mutation when ``True``.
        clock: Injectable epoch-seconds clock for deterministic tests.
    """

    def __init__(
        self,
        state_path: Optional[Union[str, Path]] = "__default__",
        *,
        max_per_day: int = 2,
        autosave: bool = False,
        clock=time.time,
    ) -> None:
        if state_path == "__default__":
            try:
                state_path = proactive_questions_path()
            except Exception:  # pragma: no cover - defensive
                state_path = None
        self.state_path = Path(state_path).expanduser() if state_path else None
        self.max_per_day = max(0, int(max_per_day))
        self.autosave = autosave
        self._clock = clock
        self._pending: List[ProactiveQuestion] = []
        self._asked: List[Dict[str, Any]] = []
        self._patterns: List[Dict[str, Any]] = []
        if self.state_path is not None and self.state_path.exists():
            self.load()

    # -- configuration ----------------------------------------------------
    def set_daily_limit(self, limit: int) -> None:
        self.max_per_day = max(0, int(limit))

    # -- enqueueing -------------------------------------------------------
    def enqueue(
        self,
        text: str,
        *,
        kind: str = "preference",
        priority: str = "low",
        subject: str = "",
    ) -> Optional[ProactiveQuestion]:
        """Add a question unless a duplicate is already pending/asked.

        Returns the queued :class:`ProactiveQuestion`, or ``None`` if skipped.
        """
        text = (text or "").strip()
        if not text:
            return None
        if self._is_duplicate(text, subject):
            return None
        q = ProactiveQuestion(text=text, kind=kind, priority=priority, subject=subject)
        self._pending.append(q)
        self._maybe_save()
        return q

    def enqueue_opportunity(self, opportunity: Any) -> Optional[ProactiveQuestion]:
        """Queue a :class:`LearningOpportunity` (duck-typed)."""
        try:
            return self.enqueue(
                getattr(opportunity, "question", ""),
                kind=getattr(opportunity, "kind", "preference"),
                priority=getattr(opportunity, "priority", "low"),
                subject=getattr(opportunity, "subject", ""),
            )
        except Exception:  # pragma: no cover - defensive
            return None

    def register_patterns(self, patterns: Any) -> int:
        """Record detected patterns (the ``patterns_detected`` list).

        Stores them for transparency/UI. Returns the number recorded. Does not
        itself enqueue questions -- that's the learner's job via
        :meth:`enqueue_opportunity`.
        """
        recorded = 0
        try:
            for pat in patterns or []:
                entry = {
                    "pattern": getattr(pat, "description", str(pat)),
                    "key": getattr(pat, "key", ""),
                    "confidence": round(float(getattr(pat, "confidence", 0.0)), 3),
                }
                # Replace existing same-key entry to keep latest confidence.
                self._patterns = [
                    p for p in self._patterns if p.get("key") != entry["key"]
                ]
                self._patterns.append(entry)
                recorded += 1
            if recorded:
                self._maybe_save()
        except Exception:  # pragma: no cover - defensive
            logger.debug("register_patterns failed", exc_info=True)
        return recorded

    # -- asking -----------------------------------------------------------
    def should_ask(self, *, now: Optional[float] = None, busy: bool = False) -> bool:
        """Whether MimOSA is allowed to ask a question right now."""
        if busy or self.max_per_day <= 0 or not self._pending:
            return False
        return self._asked_today(now) < self.max_per_day

    def next_question(
        self, *, now: Optional[float] = None, busy: bool = False
    ) -> Optional[ProactiveQuestion]:
        """Return the best question to ask now, or ``None``.

        Picks the highest-priority (then oldest) pending question, subject to
        the daily limit and the ``busy`` flag. The returned question stays in
        the pending queue until :meth:`record_answer` (or :meth:`dismiss`) is
        called, so a question shown but unanswered isn't lost.
        """
        if not self.should_ask(now=now, busy=busy):
            return None
        return self._best_pending()

    def _best_pending(self) -> Optional[ProactiveQuestion]:
        if not self._pending:
            return None
        return sorted(
            self._pending,
            key=lambda q: (_PRIORITY_RANK.get(q.priority, 1), -q.created_at),
            reverse=True,
        )[0]

    def record_answer(
        self, question_id: str, answer: str = "", *, now: Optional[float] = None
    ) -> bool:
        """Mark a pending question answered and move it to ``asked``."""
        q = self._pop_pending(question_id)
        if q is None:
            return False
        ts = float(now) if now is not None else float(self._clock())
        self._asked.append(
            {
                "id": q.id,
                "question": q.text,
                "kind": q.kind,
                "subject": q.subject,
                "answer": str(answer or ""),
                "date": datetime.fromtimestamp(ts).strftime("%Y-%m-%d"),
                "timestamp": ts,
            }
        )
        self._maybe_save()
        return True

    def dismiss(self, question_id: str) -> bool:
        """Drop a pending question without recording an answer."""
        q = self._pop_pending(question_id)
        if q is not None:
            self._maybe_save()
            return True
        return False

    # -- introspection ----------------------------------------------------
    @property
    def pending(self) -> List[ProactiveQuestion]:
        return list(self._pending)

    @property
    def asked(self) -> List[Dict[str, Any]]:
        return list(self._asked)

    @property
    def patterns_detected(self) -> List[Dict[str, Any]]:
        return list(self._patterns)

    def asked_today(self, now: Optional[float] = None) -> int:
        return self._asked_today(now)

    # -- internals --------------------------------------------------------
    def _pop_pending(self, question_id: str) -> Optional[ProactiveQuestion]:
        for i, q in enumerate(self._pending):
            if q.id == question_id:
                return self._pending.pop(i)
        return None

    def _asked_today(self, now: Optional[float] = None) -> int:
        ts = float(now) if now is not None else float(self._clock())
        today = datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
        return sum(1 for a in self._asked if a.get("date") == today)

    def _is_duplicate(self, text: str, subject: str) -> bool:
        norm = text.strip().lower()
        subj = (subject or "").strip().lower()
        for q in self._pending:
            if q.text.strip().lower() == norm:
                return True
            if subj and q.subject.strip().lower() == subj and q.kind:
                return True
        for a in self._asked:
            if str(a.get("question", "")).strip().lower() == norm:
                return True
            if subj and str(a.get("subject", "")).strip().lower() == subj:
                return True
        return False

    # -- persistence ------------------------------------------------------
    def to_state(self) -> Dict[str, Any]:
        return {
            "proactive_questions": {
                "asked": list(self._asked),
                "pending": [q.to_dict() for q in self._pending],
                "patterns_detected": list(self._patterns),
            }
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
            blob = state.get("proactive_questions", state)
            if not isinstance(blob, dict):
                return False
            self._asked = [a for a in (blob.get("asked") or []) if isinstance(a, dict)]
            self._pending = [
                ProactiveQuestion.from_dict(p)
                for p in (blob.get("pending") or [])
                if isinstance(p, dict)
            ]
            self._patterns = [
                p for p in (blob.get("patterns_detected") or []) if isinstance(p, dict)
            ]
            return True
        except Exception:  # pragma: no cover - corrupt => fresh
            logger.debug("Could not load proactive question state", exc_info=True)
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
            logger.debug("Could not save proactive question state", exc_info=True)
            return False

    def _maybe_save(self) -> None:
        if self.autosave:
            self.save()

    def clear(self) -> None:
        """Forget all questions/patterns (privacy control)."""
        self._pending.clear()
        self._asked.clear()
        self._patterns.clear()
        self._maybe_save()

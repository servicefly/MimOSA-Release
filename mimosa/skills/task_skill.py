"""Task-control skill -- manage background jobs by voice (M7.4).

This is the user-facing entry point for Milestone 7's background task queue. It
recognises conversational requests to check on, **pause**, **resume**, or
**cancel** long-running background work ("what are you working on?", "hold on a
sec", "carry on", "cancel the indexing") and drives a
:class:`~mimosa.tasks.task_queue.TaskQueue`.

Privacy & degradation
----------------------
* Purely **local** (:attr:`uses_llm` is ``False``): it never sends anything to
  the cloud and never touches the network. It just inspects/controls the
  on-device queue.
* **Safe by default**: if no queue is wired, it lazily builds an in-memory one
  so the skill always responds gracefully instead of crashing.
* Errors never crash the voice loop -- :meth:`BaseSkill.run` wraps everything.
"""

from __future__ import annotations

import re
from typing import List, Optional

from mimosa.skills.base_skill import BaseSkill, SkillResult
from mimosa.tasks.task_queue import TaskStatus

#: Verbs that mean "stop for now" (pause), "carry on" (resume), or "cancel".
_PAUSE_RE = re.compile(
    r"\b(pause|hold on|hold up|wait a (sec|minute|moment)|hang on|stop for now|"
    r"take a break|suspend)\b",
    re.IGNORECASE,
)
_RESUME_RE = re.compile(
    r"\b(resume|carry on|carry on with|continue|keep going|pick (it |that )?back up|"
    r"unpause|go ahead)\b",
    re.IGNORECASE,
)
_CANCEL_RE = re.compile(
    r"\b(cancel|abort|stop (the|that|it)|never ?mind|forget (it|that)|drop (it|that))\b",
    re.IGNORECASE,
)
_STATUS_RE = re.compile(
    r"\b(status|progress|what('?s| is| are)|how('?s| is| are)|working on|"
    r"running|going on|tasks?|jobs?|queue)\b",
    re.IGNORECASE,
)
_ALL_RE = re.compile(r"\b(all|everything)\b", re.IGNORECASE)


def _fmt_task(task) -> str:
    """A short, speakable one-line description of a task and its state."""
    pct = f" ({task.progress:.0%})" if task.status == TaskStatus.RUNNING else ""
    return f"{task.name} — {task.status.value}{pct}"


class TaskControlSkill(BaseSkill):
    """Inspect and control background tasks conversationally."""

    name = "tasks"
    intents = ["task_control"]
    uses_llm = False

    def __init__(self, llm_provider=None, *, queue=None) -> None:
        super().__init__(llm_provider=llm_provider)
        self._queue = queue

    @property
    def queue(self):
        """The task queue, lazily built as an in-memory default if none wired."""
        if self._queue is None:
            from mimosa.tasks.task_queue import TaskQueue

            self._queue = TaskQueue(db_path=":memory:")
        return self._queue

    # -- helpers -----------------------------------------------------------

    def _match_task(self, text: str):
        """Find the active task whose name best matches free-text ``text``.

        Returns the single best match, or ``None`` when there is no confident
        match. Matching is case-insensitive substring overlap on the task name.
        """
        active = self.queue.active_tasks()
        if not active:
            return None
        low = text.lower()
        # Prefer a task whose full name appears as a whole word/phrase in the
        # utterance (word boundaries avoid matching short names like "A" inside
        # unrelated words such as "carry").
        for task in active:
            name = task.name.lower()
            if name and re.search(rf"\b{re.escape(name)}\b", low):
                return task
        for task in active:
            words = [w for w in re.split(r"\W+", task.name.lower()) if len(w) > 3]
            if any(w in low for w in words):
                return task
        return None

    def _status_text(self) -> SkillResult:
        active = self.queue.active_tasks()
        if not active:
            return SkillResult(
                text="You have no background tasks running right now.",
                skill=self.name,
                metadata={"active": 0},
            )
        lines = [_fmt_task(t) for t in active]
        head = "Here's what I'm working on:" if len(active) > 1 else "Current task:"
        return SkillResult(
            text=head + " " + "; ".join(lines) + ".",
            skill=self.name,
            metadata={
                "active": len(active),
                "tasks": [t.to_dict() for t in active],
            },
        )

    # -- main dispatch -----------------------------------------------------

    def handle(self, text: str, context: Optional[List] = None) -> SkillResult:
        text = (text or "").strip()
        wants_all = bool(_ALL_RE.search(text))

        # Order matters: cancel and resume verbs can co-occur with status words,
        # so check explicit control verbs before the status fallback.
        if _CANCEL_RE.search(text):
            return self._handle_cancel(text, wants_all)
        if _RESUME_RE.search(text):
            return self._handle_resume(text, wants_all)
        if _PAUSE_RE.search(text):
            return self._handle_pause(text, wants_all)
        # Default: report status.
        return self._status_text()

    def _handle_pause(self, text: str, wants_all: bool) -> SkillResult:
        active = self.queue.active_tasks()
        if not active:
            return SkillResult(
                text="There's nothing running to pause.",
                skill=self.name, metadata={"action": "pause", "affected": 0},
            )
        if wants_all or len(active) == 1:
            n = self.queue.pause_all() if wants_all else (1 if self.queue.pause(active[0].id) else 0)
            label = "everything" if wants_all else active[0].name
            return SkillResult(
                text=f"Okay, I've paused {label}. Say \"carry on\" when you're ready.",
                skill=self.name, metadata={"action": "pause", "affected": n},
            )
        task = self._match_task(text)
        if task is None:
            return SkillResult(
                text="Which task should I pause? You can also say \"pause everything\".",
                success=False, skill=self.name,
                metadata={"action": "pause", "affected": 0, "reason": "ambiguous"},
            )
        ok = self.queue.pause(task.id)
        return SkillResult(
            text=f"Paused {task.name}." if ok else f"I couldn't pause {task.name}.",
            success=ok, skill=self.name,
            metadata={"action": "pause", "task_id": task.id, "affected": int(ok)},
        )

    def _handle_resume(self, text: str, wants_all: bool) -> SkillResult:
        paused = self.queue.list_tasks(status=TaskStatus.PAUSED)
        if not paused:
            return SkillResult(
                text="There's nothing paused to resume.",
                skill=self.name, metadata={"action": "resume", "affected": 0},
            )
        if wants_all or len(paused) == 1:
            n = self.queue.resume_all()
            label = "everything" if (wants_all or len(paused) > 1) else paused[0].name
            return SkillResult(
                text=f"Resuming {label}.",
                skill=self.name, metadata={"action": "resume", "affected": n},
            )
        task = self._match_task(text)
        if task is None or task.status != TaskStatus.PAUSED:
            # A generic resume phrase ("carry on", "continue") with no specific
            # task named is treated as "resume what you were doing" -- resume all.
            n = self.queue.resume_all()
            return SkillResult(
                text="Resuming everything.",
                skill=self.name, metadata={"action": "resume", "affected": n},
            )
        ok = self.queue.resume(task.id)
        return SkillResult(
            text=f"Resuming {task.name}." if ok else f"I couldn't resume {task.name}.",
            success=ok, skill=self.name,
            metadata={"action": "resume", "task_id": task.id, "affected": int(ok)},
        )

    def _handle_cancel(self, text: str, wants_all: bool) -> SkillResult:
        active = self.queue.active_tasks()
        if not active:
            return SkillResult(
                text="There's nothing running to cancel.",
                skill=self.name, metadata={"action": "cancel", "affected": 0},
            )
        if wants_all:
            n = sum(1 for t in active if self.queue.cancel(t.id))
            return SkillResult(
                text=f"Cancelled {n} task{'s' if n != 1 else ''}.",
                skill=self.name, metadata={"action": "cancel", "affected": n},
            )
        if len(active) == 1:
            ok = self.queue.cancel(active[0].id)
            return SkillResult(
                text=f"Cancelled {active[0].name}." if ok else "I couldn't cancel that.",
                success=ok, skill=self.name,
                metadata={"action": "cancel", "task_id": active[0].id, "affected": int(ok)},
            )
        task = self._match_task(text)
        if task is None:
            return SkillResult(
                text="Which task should I cancel? You can also say \"cancel everything\".",
                success=False, skill=self.name,
                metadata={"action": "cancel", "affected": 0, "reason": "ambiguous"},
            )
        ok = self.queue.cancel(task.id)
        return SkillResult(
            text=f"Cancelled {task.name}." if ok else f"I couldn't cancel {task.name}.",
            success=ok, skill=self.name,
            metadata={"action": "cancel", "task_id": task.id, "affected": int(ok)},
        )

    def _error_message(self) -> str:
        return "Sorry, I couldn't manage your background tasks right now."

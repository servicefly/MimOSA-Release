"""SQLite-backed background task queue for MimOSA (M7.1).

Some things MimOSA does take a while -- indexing a folder, running a multi-step
research job, batch-converting files. Blocking the voice loop while they run
would make the assistant feel frozen, so M7 introduces a small **background
task queue** that runs work off the conversational thread, *persists* every
task to disk so nothing is lost across a restart, and lets the user
**pause and resume** long jobs by voice ("hold on", "carry on").

Design principles
-----------------
* **Local & private.** Tasks live in a single on-device SQLite database
  (``~/.local/share/mimosa/tasks.db`` by default). No telemetry; nothing leaves
  the machine. The queue itself never calls an LLM or the network -- it only
  *runs handlers* the rest of MimOSA registers.
* **Headless & dependency-free.** Standard-library :mod:`sqlite3` and
  :mod:`threading` only. It imports no GTK/audio/ML, so it loads and unit-tests
  on a bare headless machine.
* **Bounded concurrency.** At most :data:`DEFAULT_MAX_CONCURRENT` (2) tasks run
  at once, so background work can never swamp a modest laptop. An optional
  *resource gate* (the M7.2 monitor) can defer new starts when the system is
  already busy.
* **Cooperative pause/cancel.** Handlers periodically call
  :meth:`TaskControl.checkpoint`; that is where a paused or cancelled task
  unwinds cleanly, preserving its progress. There is no thread-killing.
* **Deterministic to test.** The clock and the resource gate are injectable,
  and the queue can run **synchronously** (:meth:`TaskQueue.process_next` /
  :meth:`TaskQueue.drain`) so tests never depend on thread timing. A real
  background mode (:meth:`TaskQueue.start` / :meth:`TaskQueue.stop`) is layered
  on top of the same primitives.

Data model
----------
One row per task in ``tasks``::

    id          TEXT PRIMARY KEY      -- uuid4 hex
    name        TEXT                  -- human label ("Index Documents")
    kind        TEXT                  -- handler key ("index", "research", ...)
    payload     TEXT                  -- JSON args for the handler
    status      TEXT                  -- see TaskStatus
    priority    INTEGER               -- higher runs first
    progress    REAL                  -- 0.0 .. 1.0
    message     TEXT                  -- last progress/status message
    result      TEXT                  -- JSON result on success
    error       TEXT                  -- error string on failure
    created_at / updated_at / started_at / finished_at  REAL epoch seconds
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Union

from mimosa.memory.paths import tasks_db_path

logger = logging.getLogger("mimosa.tasks.task_queue")

SCHEMA_VERSION = 1

#: Maximum number of tasks allowed to run concurrently.
DEFAULT_MAX_CONCURRENT = 2

#: Poll interval (seconds) for background worker threads.
DEFAULT_POLL_INTERVAL = 0.1


class TaskStatus(str, Enum):
    """Lifecycle states of a background task.

    ``str`` mixin so values serialize directly to the SQLite ``status`` column
    and compare cleanly against plain strings.
    """

    QUEUED = "queued"        # waiting to be claimed by a worker
    RUNNING = "running"      # actively executing in a worker
    PAUSED = "paused"        # cooperatively suspended; resumable
    COMPLETED = "completed"  # finished successfully
    FAILED = "failed"        # handler raised; see ``error``
    CANCELLED = "cancelled"  # cancelled by the user before/while running

    @property
    def is_terminal(self) -> bool:
        """Whether the task has reached an end state and won't run again."""
        return self in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED)

    @property
    def is_active(self) -> bool:
        """Whether the task is queued/running/paused (i.e. still in flight)."""
        return self in (TaskStatus.QUEUED, TaskStatus.RUNNING, TaskStatus.PAUSED)


# Exceptions used for cooperative unwinding inside handlers via checkpoint().
class _TaskInterrupt(Exception):
    """Base for cooperative interruptions raised from :meth:`TaskControl.checkpoint`."""


class TaskPaused(_TaskInterrupt):
    """Raised inside a handler when the user has requested a pause."""


class TaskCancelled(_TaskInterrupt):
    """Raised inside a handler when the task has been cancelled."""


@dataclass
class Task:
    """A unit of background work and its current state.

    Attributes mirror the ``tasks`` table columns. ``payload`` and ``result``
    are decoded JSON (``dict``/value) for ergonomic access.
    """

    id: str
    name: str
    kind: str
    payload: Dict[str, Any] = field(default_factory=dict)
    status: TaskStatus = TaskStatus.QUEUED
    priority: int = 0
    progress: float = 0.0
    message: str = ""
    result: Any = None
    error: Optional[str] = None
    created_at: float = 0.0
    updated_at: float = 0.0
    started_at: Optional[float] = None
    finished_at: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        """Return a plain, JSON-friendly ``dict`` of the task."""
        return {
            "id": self.id,
            "name": self.name,
            "kind": self.kind,
            "payload": self.payload,
            "status": self.status.value,
            "priority": self.priority,
            "progress": round(self.progress, 4),
            "message": self.message,
            "result": self.result,
            "error": self.error,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }

    @classmethod
    def _from_row(cls, row: sqlite3.Row) -> "Task":
        def _loads(blob: Optional[str], default):
            if not blob:
                return default
            try:
                return json.loads(blob)
            except (ValueError, TypeError):  # pragma: no cover - defensive
                return default

        return cls(
            id=row["id"],
            name=row["name"],
            kind=row["kind"],
            payload=_loads(row["payload"], {}),
            status=TaskStatus(row["status"]),
            priority=row["priority"],
            progress=row["progress"],
            message=row["message"] or "",
            result=_loads(row["result"], None),
            error=row["error"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            started_at=row["started_at"],
            finished_at=row["finished_at"],
        )


#: Signature of a task handler: ``handler(task, control) -> result``.
#: The handler does the work, calls ``control.checkpoint()`` periodically to
#: honour pause/cancel, and may report progress via ``control.set_progress``.
TaskHandler = Callable[["Task", "TaskControl"], Any]


class TaskControl:
    """Handle passed to a running handler for progress + cooperative control.

    The handler should call :meth:`checkpoint` at safe points in its loop. That
    is the *only* place a task is paused or cancelled, guaranteeing the work
    unwinds cleanly (no killed threads, no half-written state).
    """

    def __init__(self, queue: "TaskQueue", task_id: str) -> None:
        self._queue = queue
        self._task_id = task_id

    @property
    def task_id(self) -> str:
        return self._task_id

    def set_progress(self, fraction: float, message: str = "") -> None:
        """Report progress in ``[0, 1]`` and an optional status message."""
        self._queue._update_progress(self._task_id, fraction, message)

    def checkpoint(self) -> None:
        """Honour any pending pause/cancel request.

        Raises:
            TaskCancelled: if the task has been cancelled.
            TaskPaused: if the user/queue requested a pause.
        """
        signal = self._queue._poll_control(self._task_id)
        if signal == "cancel":
            raise TaskCancelled(self._task_id)
        if signal == "pause":
            raise TaskPaused(self._task_id)


class TaskQueue:
    """A persistent, bounded-concurrency background task queue.

    Args:
        db_path: SQLite path. ``None`` uses the default under the user data
            dir; ``":memory:"`` keeps everything in RAM (tests).
        max_concurrent: Maximum tasks allowed to run at once.
        clock: Injectable time source (``() -> float``) for deterministic tests.
        resource_gate: Optional callable ``() -> bool``; when it returns
            ``False`` the queue defers starting *new* tasks (already-running
            tasks continue). Wired to the M7.2 resource monitor in production.
    """

    def __init__(
        self,
        db_path: Optional[Union[str, Path]] = None,
        *,
        max_concurrent: int = DEFAULT_MAX_CONCURRENT,
        clock: Callable[[], float] = time.time,
        resource_gate: Optional[Callable[[], bool]] = None,
    ) -> None:
        if db_path is None:
            db_path = tasks_db_path()
        self._is_memory = str(db_path) == ":memory:"
        self.db_path = str(db_path)
        self.max_concurrent = max(1, int(max_concurrent))
        self._clock = clock
        self._resource_gate = resource_gate
        self._handlers: Dict[str, TaskHandler] = {}
        self._lock = threading.RLock()

        # In-memory control signals: task_id -> "pause" | "cancel".
        self._control: Dict[str, str] = {}
        # Global pause flag (pause/resume *all*).
        self._paused_all = False

        # Background worker machinery (only used in start()/stop()).
        self._workers: List[threading.Thread] = []
        self._stop_event = threading.Event()
        self._poll_interval = DEFAULT_POLL_INTERVAL

        if not self._is_memory:
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)

        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()
        # Recover any tasks left RUNNING by an unclean shutdown: requeue them so
        # they aren't stuck forever (idempotent handlers expected).
        self._recover_orphans()

    # -- schema ------------------------------------------------------------

    def _init_schema(self) -> None:
        with self._lock:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                    id          TEXT PRIMARY KEY,
                    name        TEXT NOT NULL,
                    kind        TEXT NOT NULL,
                    payload     TEXT,
                    status      TEXT NOT NULL,
                    priority    INTEGER NOT NULL DEFAULT 0,
                    progress    REAL NOT NULL DEFAULT 0.0,
                    message     TEXT,
                    result      TEXT,
                    error       TEXT,
                    created_at  REAL NOT NULL,
                    updated_at  REAL NOT NULL,
                    started_at  REAL,
                    finished_at REAL
                );

                CREATE INDEX IF NOT EXISTS idx_tasks_status
                    ON tasks(status, priority DESC, created_at ASC);
                """
            )
            self._conn.commit()
            cur = self._conn.execute("PRAGMA user_version")
            if cur.fetchone()[0] < SCHEMA_VERSION:
                self._conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
                self._conn.commit()

    def _recover_orphans(self) -> None:
        """Requeue tasks stuck in RUNNING (e.g. after a crash)."""
        with self._lock:
            now = self._clock()
            self._conn.execute(
                "UPDATE tasks SET status = ?, started_at = NULL, updated_at = ? "
                "WHERE status = ?",
                (TaskStatus.QUEUED.value, now, TaskStatus.RUNNING.value),
            )
            self._conn.commit()

    # -- handler registration ---------------------------------------------

    def register_handler(self, kind: str, handler: TaskHandler) -> None:
        """Register the callable that executes tasks of a given ``kind``."""
        kind = (kind or "").strip()
        if not kind:
            raise ValueError("handler kind must be a non-empty string")
        if not callable(handler):
            raise TypeError("handler must be callable")
        with self._lock:
            self._handlers[kind] = handler

    def has_handler(self, kind: str) -> bool:
        """Whether a handler is registered for ``kind``."""
        return (kind or "").strip() in self._handlers

    # -- enqueue / inspect -------------------------------------------------

    def enqueue(
        self,
        name: str,
        kind: str,
        payload: Optional[Dict[str, Any]] = None,
        *,
        priority: int = 0,
    ) -> Task:
        """Add a new task to the queue and return it (status ``QUEUED``)."""
        name = (name or "").strip() or "Untitled task"
        kind = (kind or "").strip()
        if not kind:
            raise ValueError("task kind must be a non-empty string")
        now = self._clock()
        task = Task(
            id=uuid.uuid4().hex,
            name=name,
            kind=kind,
            payload=dict(payload or {}),
            status=TaskStatus.QUEUED,
            priority=int(priority),
            created_at=now,
            updated_at=now,
        )
        with self._lock:
            self._conn.execute(
                "INSERT INTO tasks (id, name, kind, payload, status, priority, "
                "progress, message, result, error, created_at, updated_at, "
                "started_at, finished_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    task.id, task.name, task.kind, json.dumps(task.payload),
                    task.status.value, task.priority, task.progress, task.message,
                    None, None, task.created_at, task.updated_at, None, None,
                ),
            )
            self._conn.commit()
        logger.debug("Enqueued task %s (%s/%s)", task.id, task.kind, task.name)
        return task

    def get(self, task_id: str) -> Optional[Task]:
        """Return the task with ``task_id``, or ``None`` if unknown."""
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM tasks WHERE id = ?", (task_id,)
            ).fetchone()
        return Task._from_row(row) if row else None

    def list_tasks(
        self,
        *,
        status: Optional[Union[TaskStatus, str]] = None,
        limit: Optional[int] = None,
    ) -> List[Task]:
        """Return tasks, newest first, optionally filtered by ``status``."""
        sql = "SELECT * FROM tasks"
        params: List[Any] = []
        if status is not None:
            sql += " WHERE status = ?"
            params.append(status.value if isinstance(status, TaskStatus) else str(status))
        sql += " ORDER BY created_at DESC"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(int(limit))
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [Task._from_row(r) for r in rows]

    def active_tasks(self) -> List[Task]:
        """Return all queued/running/paused tasks (oldest first)."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM tasks WHERE status IN (?,?,?) ORDER BY created_at ASC",
                (TaskStatus.QUEUED.value, TaskStatus.RUNNING.value, TaskStatus.PAUSED.value),
            ).fetchall()
        return [Task._from_row(r) for r in rows]

    def counts(self) -> Dict[str, int]:
        """Return a ``{status: count}`` summary across all tasks."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT status, COUNT(*) AS n FROM tasks GROUP BY status"
            ).fetchall()
        return {r["status"]: r["n"] for r in rows}

    def running_count(self) -> int:
        """Number of tasks currently in the RUNNING state."""
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS n FROM tasks WHERE status = ?",
                (TaskStatus.RUNNING.value,),
            ).fetchone()
        return int(row["n"])

    # -- pause / resume / cancel ------------------------------------------

    def pause(self, task_id: str) -> bool:
        """Request a pause for one task.

        A QUEUED task flips to PAUSED immediately; a RUNNING task is signalled
        to pause at its next :meth:`TaskControl.checkpoint`. Returns ``True`` if
        the task exists and is pausable.
        """
        task = self.get(task_id)
        if task is None or task.status.is_terminal:
            return False
        with self._lock:
            if task.status == TaskStatus.QUEUED:
                self._set_status(task_id, TaskStatus.PAUSED)
            elif task.status == TaskStatus.RUNNING:
                self._control[task_id] = "pause"
        return True

    def resume(self, task_id: str) -> bool:
        """Resume a PAUSED task by re-queueing it. Returns success."""
        task = self.get(task_id)
        if task is None or task.status != TaskStatus.PAUSED:
            return False
        with self._lock:
            self._control.pop(task_id, None)
            self._set_status(task_id, TaskStatus.QUEUED)
        return True

    def cancel(self, task_id: str) -> bool:
        """Cancel a task.

        QUEUED/PAUSED tasks are marked CANCELLED immediately; a RUNNING task is
        signalled to cancel at its next checkpoint. Returns ``True`` if the task
        existed and was not already terminal.
        """
        task = self.get(task_id)
        if task is None or task.status.is_terminal:
            return False
        with self._lock:
            if task.status == TaskStatus.RUNNING:
                self._control[task_id] = "cancel"
            else:
                self._finish(task_id, TaskStatus.CANCELLED)
        return True

    def pause_all(self) -> int:
        """Pause every active task. Returns the number affected."""
        affected = 0
        for task in self.active_tasks():
            if task.status != TaskStatus.PAUSED and self.pause(task.id):
                affected += 1
        with self._lock:
            self._paused_all = True
        return affected

    def resume_all(self) -> int:
        """Resume every paused task. Returns the number affected."""
        with self._lock:
            self._paused_all = False
        affected = 0
        for task in self.list_tasks(status=TaskStatus.PAUSED):
            if self.resume(task.id):
                affected += 1
        return affected

    @property
    def is_paused_all(self) -> bool:
        """Whether a global pause is in effect (no new tasks will start)."""
        return self._paused_all

    # -- internal state mutations -----------------------------------------

    def _set_status(self, task_id: str, status: TaskStatus) -> None:
        self._conn.execute(
            "UPDATE tasks SET status = ?, updated_at = ? WHERE id = ?",
            (status.value, self._clock(), task_id),
        )
        self._conn.commit()

    def _update_progress(self, task_id: str, fraction: float, message: str) -> None:
        frac = max(0.0, min(1.0, float(fraction)))
        with self._lock:
            self._conn.execute(
                "UPDATE tasks SET progress = ?, message = ?, updated_at = ? WHERE id = ?",
                (frac, message or "", self._clock(), task_id),
            )
            self._conn.commit()

    def _poll_control(self, task_id: str) -> Optional[str]:
        """Return a pending control signal for a task ('pause'/'cancel')."""
        with self._lock:
            if task_id in self._control:
                return self._control[task_id]
            if self._paused_all:
                return "pause"
        return None

    def _finish(
        self,
        task_id: str,
        status: TaskStatus,
        *,
        result: Any = None,
        error: Optional[str] = None,
    ) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE tasks SET status = ?, result = ?, error = ?, "
                "finished_at = ?, updated_at = ? WHERE id = ?",
                (
                    status.value,
                    json.dumps(result) if result is not None else None,
                    error,
                    self._clock(),
                    self._clock(),
                    task_id,
                ),
            )
            self._conn.commit()
            self._control.pop(task_id, None)

    # -- claiming & running ------------------------------------------------

    def _claim_next(self) -> Optional[Task]:
        """Atomically move the best QUEUED task to RUNNING and return it.

        Respects ``max_concurrent``, the global pause flag, the resource gate,
        and that a handler is registered for the task's kind. Returns ``None``
        when nothing is runnable right now.
        """
        with self._lock:
            if self._paused_all:
                return None
            if self.running_count() >= self.max_concurrent:
                return None
            if self._resource_gate is not None:
                try:
                    if not self._resource_gate():
                        return None
                except Exception:  # pragma: no cover - gate must never crash claim
                    logger.warning("resource_gate raised; allowing task start")
            rows = self._conn.execute(
                "SELECT * FROM tasks WHERE status = ? "
                "ORDER BY priority DESC, created_at ASC",
                (TaskStatus.QUEUED.value,),
            ).fetchall()
            for row in rows:
                task = Task._from_row(row)
                if task.kind not in self._handlers:
                    # No handler yet -- leave it queued for later registration.
                    continue
                now = self._clock()
                self._conn.execute(
                    "UPDATE tasks SET status = ?, started_at = COALESCE(started_at, ?), "
                    "updated_at = ? WHERE id = ?",
                    (TaskStatus.RUNNING.value, now, now, task.id),
                )
                self._conn.commit()
                task.status = TaskStatus.RUNNING
                task.started_at = task.started_at or now
                return task
        return None

    def _run_task(self, task: Task) -> Task:
        """Execute a claimed task's handler, recording the outcome."""
        handler = self._handlers.get(task.kind)
        control = TaskControl(self, task.id)
        if handler is None:  # pragma: no cover - guarded by _claim_next
            self._finish(task.id, TaskStatus.FAILED, error="no handler registered")
            return self.get(task.id)  # type: ignore[return-value]
        try:
            result = handler(task, control)
            self._finish(task.id, TaskStatus.COMPLETED, result=result)
        except TaskCancelled:
            self._finish(task.id, TaskStatus.CANCELLED)
        except TaskPaused:
            with self._lock:
                self._control.pop(task.id, None)
                self._set_status(task.id, TaskStatus.PAUSED)
        except Exception as exc:  # noqa: BLE001 - boundary: never crash worker
            logger.exception("Task %s (%s) failed: %s", task.id, task.kind, exc)
            self._finish(task.id, TaskStatus.FAILED, error=str(exc))
        return self.get(task.id)  # type: ignore[return-value]

    # -- synchronous driving (deterministic; great for tests) -------------

    def process_next(self) -> Optional[Task]:
        """Claim and run a single runnable task in the calling thread.

        Returns the finished :class:`Task` (its terminal/paused state), or
        ``None`` if nothing was runnable. Fully synchronous -- ideal for tests
        and for embedding the queue in an existing loop.
        """
        task = self._claim_next()
        if task is None:
            return None
        return self._run_task(task)

    def drain(self, max_tasks: Optional[int] = None) -> int:
        """Run runnable tasks until none remain (or ``max_tasks`` is reached).

        Returns the number of tasks processed. Note: a task that pauses itself
        is counted but leaves the queue; a fresh resume re-queues it.
        """
        processed = 0
        while max_tasks is None or processed < max_tasks:
            if self.process_next() is None:
                break
            processed += 1
        return processed

    # -- background driving (real threads) --------------------------------

    def start(self) -> None:
        """Start background worker threads (idempotent)."""
        with self._lock:
            if self._workers:
                return
            self._stop_event.clear()
            for i in range(self.max_concurrent):
                t = threading.Thread(
                    target=self._worker_loop, name=f"mimosa-task-worker-{i}", daemon=True
                )
                self._workers.append(t)
                t.start()
        logger.debug("Started %d task workers", len(self._workers))

    def _worker_loop(self) -> None:
        while not self._stop_event.is_set():
            task = self._claim_next()
            if task is None:
                self._stop_event.wait(self._poll_interval)
                continue
            self._run_task(task)

    def stop(self, *, wait: bool = True, timeout: float = 5.0) -> None:
        """Signal workers to stop and (optionally) join them."""
        self._stop_event.set()
        if wait:
            for t in self._workers:
                t.join(timeout=timeout)
        with self._lock:
            self._workers = []

    # -- maintenance -------------------------------------------------------

    def purge(
        self,
        *,
        status: Optional[Union[TaskStatus, str]] = None,
        before: Optional[float] = None,
    ) -> int:
        """Delete tasks (terminal ones by default). Returns rows removed.

        * ``purge()`` -- remove all terminal (completed/failed/cancelled) tasks.
        * ``purge(status=...)`` -- remove tasks in a specific state.
        * ``purge(before=ts)`` -- additionally restrict to ``created_at < ts``.
        """
        clauses: List[str] = []
        params: List[Any] = []
        if status is not None:
            clauses.append("status = ?")
            params.append(status.value if isinstance(status, TaskStatus) else str(status))
        else:
            clauses.append("status IN (?,?,?)")
            params.extend([
                TaskStatus.COMPLETED.value,
                TaskStatus.FAILED.value,
                TaskStatus.CANCELLED.value,
            ])
        if before is not None:
            clauses.append("created_at < ?")
            params.append(float(before))
        sql = "DELETE FROM tasks WHERE " + " AND ".join(clauses)
        with self._lock:
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            return cur.rowcount or 0

    def clear_all(self) -> None:
        """Remove every task (factory reset)."""
        with self._lock:
            self._conn.execute("DELETE FROM tasks")
            self._conn.commit()
            self._control.clear()

    def close(self) -> None:
        """Stop workers and close the database connection (idempotent)."""
        try:
            self.stop(wait=True)
        except Exception:  # pragma: no cover - defensive
            pass
        with self._lock:
            try:
                self._conn.close()
            except Exception:  # pragma: no cover - defensive
                pass

    def __enter__(self) -> "TaskQueue":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def __repr__(self) -> str:  # pragma: no cover - trivial
        loc = ":memory:" if self._is_memory else self.db_path
        return f"TaskQueue(path={loc!r}, max_concurrent={self.max_concurrent})"

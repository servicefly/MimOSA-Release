"""Runtime service wiring for MimOSA (Milestone 8.3).

This module is the single place that assembles MimOSA's optional "advanced
features" (the Milestone 7 stack) and the cross-cutting graceful-error reporter
(Milestone 8.1) according to the user's :class:`~mimosa.utils.config.TasksSettings`.

Everything is opt-in and degrades gracefully:

* When ``background_tasks_enabled`` is False, no :class:`TaskQueue` is built and
  no worker threads start.
* When ``resource_monitoring`` is False (or ``psutil`` is missing), the queue
  runs without a resource gate.
* When ``learn_error_fixes`` is False, the :class:`ErrorFixLearner` is created
  in a disabled state so nothing is recorded, but recall still degrades cleanly.

The class is deliberately dependency-injection friendly so it can be exercised
hermetically in tests: pass ``:memory:`` database paths and injected
``clock``/``sampler``/learner objects and nothing touches the real user data
directory or the network.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable, Optional

from mimosa.utils.config import AppConfig, PrivacySettings, TasksSettings

logger = logging.getLogger(__name__)

__all__ = ["MaintenanceReport", "AppServices"]


@dataclass
class MaintenanceReport:
    """Outcome of a single :meth:`AppServices.run_maintenance` pass."""

    messages_purged: int = 0
    conversation_vacuumed: bool = False
    ran: bool = False

    def summary(self) -> str:
        if not self.ran:
            return "Maintenance skipped."
        parts = [f"purged {self.messages_purged} message(s)"]
        if self.conversation_vacuumed:
            parts.append("vacuumed conversation DB")
        return "Maintenance: " + ", ".join(parts) + "."


@dataclass
class AppServices:
    """Assembles and owns MimOSA's optional runtime services.

    Construct via :meth:`from_config` in normal use. Direct construction with
    injected collaborators is supported for tests.
    """

    tasks: TasksSettings = field(default_factory=TasksSettings)
    privacy: PrivacySettings = field(default_factory=PrivacySettings)

    # Injected / lazily-built collaborators.
    preference_learner: object = None
    conversation_store: object = None
    error_learner: object = None
    resource_monitor: object = None
    task_queue: object = None
    error_reporter: object = None

    # Injection hooks for hermetic tests.
    clock: Optional[Callable[[], float]] = None
    sampler: Optional[Callable] = None
    tasks_db_path: Optional[str] = None

    _started: bool = field(default=False, init=False, repr=False)

    # -- construction ------------------------------------------------------

    @classmethod
    def from_config(
        cls,
        config: AppConfig,
        *,
        preference_learner=None,
        conversation_store=None,
        clock: Optional[Callable[[], float]] = None,
        sampler: Optional[Callable] = None,
        tasks_db_path: Optional[str] = None,
    ) -> "AppServices":
        """Build the service bundle from a validated :class:`AppConfig`."""
        services = cls(
            tasks=config.tasks,
            privacy=config.privacy,
            preference_learner=preference_learner,
            conversation_store=conversation_store,
            clock=clock,
            sampler=sampler,
            tasks_db_path=tasks_db_path,
        )
        services.build()
        return services

    def build(self) -> "AppServices":
        """Instantiate the optional services according to the settings.

        Idempotent and defensive: any failure to build a component is logged and
        leaves that component as ``None`` rather than crashing startup.
        """
        self._build_error_stack()
        self._build_resource_monitor()
        self._build_task_queue()
        return self

    def _build_error_stack(self) -> None:
        from mimosa.core.error_reporter import ErrorReporter
        from mimosa.tasks.error_learner import ErrorFixLearner

        if self.error_learner is None:
            try:
                self.error_learner = ErrorFixLearner(
                    self.preference_learner,
                    enabled=bool(self.tasks.learn_error_fixes),
                )
            except Exception:  # pragma: no cover - defensive
                logger.debug("Could not build ErrorFixLearner", exc_info=True)
                self.error_learner = None

        if self.error_reporter is None:
            try:
                self.error_reporter = ErrorReporter(learner=self.error_learner)
            except Exception:  # pragma: no cover - defensive
                logger.debug("Could not build ErrorReporter", exc_info=True)
                self.error_reporter = None

    def _build_resource_monitor(self) -> None:
        if not self.tasks.resource_monitoring:
            self.resource_monitor = None
            return
        if self.resource_monitor is not None:
            return
        from mimosa.tasks.resource_monitor import ResourceMonitor

        kwargs = dict(
            cpu_threshold=self.tasks.cpu_threshold,
            mem_threshold=self.tasks.mem_threshold,
        )
        if self.sampler is not None:
            kwargs["sampler"] = self.sampler
        if self.clock is not None:
            kwargs["clock"] = self.clock
        try:
            self.resource_monitor = ResourceMonitor(**kwargs)
        except Exception:  # pragma: no cover - defensive
            logger.debug("Could not build ResourceMonitor", exc_info=True)
            self.resource_monitor = None

    def _build_task_queue(self) -> None:
        if not self.tasks.background_tasks_enabled:
            self.task_queue = None
            return
        if self.task_queue is not None:
            return
        from mimosa.tasks.task_queue import TaskQueue

        resource_gate = None
        if self.resource_monitor is not None:
            try:
                resource_gate = self.resource_monitor.gate()
            except Exception:  # pragma: no cover - defensive
                resource_gate = None

        kwargs = dict(
            max_concurrent=self.tasks.max_concurrent,
            resource_gate=resource_gate,
        )
        if self.tasks_db_path is not None:
            kwargs["db_path"] = self.tasks_db_path
        if self.clock is not None:
            kwargs["clock"] = self.clock
        try:
            self.task_queue = TaskQueue(**kwargs)
        except Exception:  # pragma: no cover - defensive
            logger.debug("Could not build TaskQueue", exc_info=True)
            self.task_queue = None

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        """Start background workers (idempotent). No-op when tasks disabled."""
        if self._started:
            return
        if self.task_queue is not None:
            try:
                self.task_queue.start()
            except Exception:  # pragma: no cover - defensive
                logger.warning("Task queue failed to start", exc_info=True)
        self._started = True

    def shutdown(self) -> None:
        """Stop workers and close stores (idempotent, never raises)."""
        if self.task_queue is not None:
            try:
                self.task_queue.stop(wait=True)
            except Exception:  # pragma: no cover - defensive
                logger.debug("Task queue stop failed", exc_info=True)
            try:
                self.task_queue.close()
            except Exception:  # pragma: no cover - defensive
                pass
        if self.conversation_store is not None:
            try:
                self.conversation_store.close()
            except Exception:  # pragma: no cover - defensive
                pass
        self._started = False

    # -- maintenance -------------------------------------------------------

    def run_maintenance(self, *, vacuum: bool = True) -> MaintenanceReport:
        """Apply the data-retention policy and reclaim disk space.

        Purges conversation messages older than ``privacy.data_retention_days``
        (a no-op when retention is 0 / "keep forever") and then optionally runs
        ``VACUUM`` on the conversation store. Fully defensive: maintenance never
        raises into the caller.
        """
        report = MaintenanceReport()
        store = self.conversation_store
        if store is None:
            return report
        report.ran = True

        days = getattr(self.privacy, "data_retention_days", 0)
        try:
            if days and days > 0:
                report.messages_purged = int(store.purge_older_than(days))
        except Exception:  # pragma: no cover - defensive
            logger.debug("Retention purge failed", exc_info=True)

        if vacuum:
            try:
                report.conversation_vacuumed = bool(store.vacuum())
            except Exception:  # pragma: no cover - defensive
                logger.debug("Vacuum failed", exc_info=True)

        logger.info(report.summary())
        return report

    # -- context-manager sugar --------------------------------------------

    def __enter__(self) -> "AppServices":
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.shutdown()

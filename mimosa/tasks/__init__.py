"""Advanced background-processing subsystem for MimOSA (M7 — Advanced Features).

This package lets MimOSA do slow work *off* the conversational thread without
ever swamping the user's machine, and it learns from its own mistakes — all
locally and privately:

* :mod:`mimosa.tasks.task_queue` (M7.1) — :class:`TaskQueue`, a persistent
  (SQLite) background task queue with bounded concurrency and cooperative
  **pause/resume/cancel**.
* :mod:`mimosa.tasks.resource_monitor` (M7.2) — :class:`ResourceMonitor`, a
  ``psutil``-backed CPU/memory sampler with trend prediction that acts as the
  queue's *admission gate*, degrading gracefully when ``psutil`` is absent.
* :mod:`mimosa.tasks.error_learner` (M7.3) — :class:`ErrorFixLearner`, on-device
  learning of which fixes resolve which errors, built on the M5.2
  preference learner.

Everything here is standard-library + optional ``psutil``; nothing imports
GTK/audio/network, so the package loads and unit-tests on a headless machine.
"""

from mimosa.tasks.error_learner import (
    ERROR_LEARNING_CATEGORY,
    ErrorFixLearner,
    FixSuggestion,
    normalize_error,
)
from mimosa.tasks.resource_monitor import (
    DEFAULT_CPU_THRESHOLD,
    DEFAULT_MEM_THRESHOLD,
    HAS_PSUTIL,
    ResourceMonitor,
    ResourceSnapshot,
)
from mimosa.tasks.task_queue import (
    DEFAULT_MAX_CONCURRENT,
    Task,
    TaskCancelled,
    TaskControl,
    TaskPaused,
    TaskQueue,
    TaskStatus,
)

__all__ = [
    # task queue (M7.1)
    "TaskQueue",
    "Task",
    "TaskStatus",
    "TaskControl",
    "TaskPaused",
    "TaskCancelled",
    "DEFAULT_MAX_CONCURRENT",
    # resource monitor (M7.2)
    "ResourceMonitor",
    "ResourceSnapshot",
    "HAS_PSUTIL",
    "DEFAULT_CPU_THRESHOLD",
    "DEFAULT_MEM_THRESHOLD",
    # error-fix learning (M7.3)
    "ErrorFixLearner",
    "FixSuggestion",
    "normalize_error",
    "ERROR_LEARNING_CATEGORY",
]

"""System resource monitoring & admission prediction for MimOSA (M7.2).

Background work is only friendly if it doesn't make the user's machine crawl.
This module samples CPU, memory (and load average) via :mod:`psutil`, keeps a
short rolling history, and answers two questions the task queue cares about:

* *"Is the system busy right now?"* -- :meth:`ResourceMonitor.is_busy`.
* *"Should I admit another background task?"* -- :meth:`ResourceMonitor.can_start_task`
  (used as the :class:`~mimosa.tasks.task_queue.TaskQueue` *resource gate*).

It also offers a tiny **prediction** based on the recent trend, so a system
whose memory is climbing fast is treated as busier than its instantaneous
reading suggests.

Design principles
-----------------
* **Local & private.** Pure on-device sampling; no network, no LLM, no
  telemetry.
* **Graceful degradation.** :mod:`psutil` is optional. Without it (or if a
  sample raises), the monitor reports an *unknown* snapshot and -- crucially --
  **fails open**: it admits tasks rather than blocking the queue forever. The
  capability is advertised via :data:`HAS_PSUTIL`.
* **Headless.** Imports no GTK/audio; safe to load on a bare VM.
* **Deterministic to test.** The sampler and clock are injectable, so tests
  feed synthetic readings and never depend on the host's real load.
"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass
from typing import Callable, Deque, List, Optional

logger = logging.getLogger("mimosa.tasks.resource_monitor")

# psutil is optional. The monitor degrades to "unknown / fail-open" without it.
try:  # pragma: no cover - availability is environmental
    import psutil  # type: ignore

    HAS_PSUTIL = True
except Exception:  # pragma: no cover - psutil missing is a valid state
    psutil = None  # type: ignore
    HAS_PSUTIL = False

#: Default thresholds (percent) above which the system is considered "busy".
DEFAULT_CPU_THRESHOLD = 85.0
DEFAULT_MEM_THRESHOLD = 85.0

#: How many recent samples to retain for trend/prediction.
DEFAULT_HISTORY = 10


@dataclass
class ResourceSnapshot:
    """A single point-in-time reading of system resource usage.

    Attributes:
        cpu_percent: System-wide CPU utilisation (0-100), or ``None`` if unknown.
        mem_percent: Used physical memory (0-100), or ``None`` if unknown.
        mem_available_mb: Available physical memory in MiB, or ``None``.
        load1: 1-minute load average, or ``None`` where unavailable.
        timestamp: Epoch seconds the sample was taken.
        available: ``True`` when real metrics were captured (psutil present and
            the sample succeeded); ``False`` for an "unknown" snapshot.
    """

    cpu_percent: Optional[float] = None
    mem_percent: Optional[float] = None
    mem_available_mb: Optional[float] = None
    load1: Optional[float] = None
    timestamp: float = 0.0
    available: bool = False

    def to_dict(self) -> dict:
        return {
            "cpu_percent": self.cpu_percent,
            "mem_percent": self.mem_percent,
            "mem_available_mb": self.mem_available_mb,
            "load1": self.load1,
            "timestamp": self.timestamp,
            "available": self.available,
        }

    def summary(self) -> str:
        """A short, speakable one-line description of the reading."""
        if not self.available:
            return "system load is currently unknown"
        cpu = f"{self.cpu_percent:.0f}% CPU" if self.cpu_percent is not None else "CPU n/a"
        mem = f"{self.mem_percent:.0f}% memory" if self.mem_percent is not None else "memory n/a"
        return f"{cpu}, {mem}"


#: Callable returning a raw reading ``(cpu_percent, mem_percent, mem_available_mb, load1)``.
#: Any element may be ``None``. Injectable so tests feed synthetic load.
Sampler = Callable[[], "tuple[Optional[float], Optional[float], Optional[float], Optional[float]]"]


def _psutil_sampler() -> "tuple[Optional[float], Optional[float], Optional[float], Optional[float]]":
    """Default sampler reading live metrics from psutil (best-effort)."""
    if not HAS_PSUTIL:  # pragma: no cover - exercised via HAS_PSUTIL=False path
        return (None, None, None, None)
    cpu = mem_pct = mem_avail = load1 = None
    try:
        # interval=None -> non-blocking, compares against the previous call.
        cpu = float(psutil.cpu_percent(interval=None))
    except Exception:  # pragma: no cover - defensive
        cpu = None
    try:
        vm = psutil.virtual_memory()
        mem_pct = float(vm.percent)
        mem_avail = float(vm.available) / (1024 * 1024)
    except Exception:  # pragma: no cover - defensive
        mem_pct = mem_avail = None
    try:
        load1 = float(psutil.getloadavg()[0])
    except (AttributeError, OSError):  # getloadavg absent on some platforms
        load1 = None
    return (cpu, mem_pct, mem_avail, load1)


class ResourceMonitor:
    """Sample and reason about system resource pressure.

    Args:
        cpu_threshold: CPU percent at/above which the system is "busy".
        mem_threshold: Memory percent at/above which the system is "busy".
        history: Number of recent samples to retain for trend prediction.
        sampler: Injectable raw reader (defaults to a psutil-backed sampler).
        clock: Injectable time source (``() -> float``).
    """

    def __init__(
        self,
        *,
        cpu_threshold: float = DEFAULT_CPU_THRESHOLD,
        mem_threshold: float = DEFAULT_MEM_THRESHOLD,
        history: int = DEFAULT_HISTORY,
        sampler: Optional[Sampler] = None,
        clock: Optional[Callable[[], float]] = None,
    ) -> None:
        self.cpu_threshold = float(cpu_threshold)
        self.mem_threshold = float(mem_threshold)
        self._sampler = sampler or _psutil_sampler
        if clock is None:
            import time as _time
            clock = _time.time
        self._clock = clock
        self._history: Deque[ResourceSnapshot] = deque(maxlen=max(1, int(history)))

    @property
    def available(self) -> bool:
        """Whether real metrics can be captured (psutil present)."""
        return HAS_PSUTIL

    # -- sampling ----------------------------------------------------------

    def sample(self) -> ResourceSnapshot:
        """Take a reading, append it to history, and return it."""
        ts = self._clock()
        try:
            cpu, mem_pct, mem_avail, load1 = self._sampler()
            available = any(v is not None for v in (cpu, mem_pct, mem_avail, load1))
            snap = ResourceSnapshot(
                cpu_percent=cpu,
                mem_percent=mem_pct,
                mem_available_mb=mem_avail,
                load1=load1,
                timestamp=ts,
                available=available,
            )
        except Exception as exc:  # noqa: BLE001 - never let sampling crash callers
            logger.warning("Resource sampling failed: %s", exc)
            snap = ResourceSnapshot(timestamp=ts, available=False)
        self._history.append(snap)
        return snap

    @property
    def latest(self) -> Optional[ResourceSnapshot]:
        """The most recent snapshot, or ``None`` if nothing sampled yet."""
        return self._history[-1] if self._history else None

    def history(self) -> List[ResourceSnapshot]:
        """A copy of the retained recent snapshots (oldest first)."""
        return list(self._history)

    # -- reasoning ---------------------------------------------------------

    def is_busy(self, snapshot: Optional[ResourceSnapshot] = None) -> bool:
        """Whether the system is under pressure right now.

        Uses the supplied/last snapshot (sampling a fresh one if needed). An
        *unknown* snapshot is treated as **not busy** (fail-open) so a missing
        psutil never stalls the queue.
        """
        snap = snapshot or self.latest or self.sample()
        if not snap.available:
            return False
        if snap.cpu_percent is not None and snap.cpu_percent >= self.cpu_threshold:
            return True
        if snap.mem_percent is not None and snap.mem_percent >= self.mem_threshold:
            return True
        return False

    def predict_pressure(self) -> Optional[float]:
        """Predict near-future CPU pressure (percent) from the recent trend.

        Returns a clamped 0-100 estimate by linearly extrapolating the last two
        available CPU readings, or ``None`` when there isn't enough signal.
        """
        cpu_points = [s.cpu_percent for s in self._history if s.available and s.cpu_percent is not None]
        if len(cpu_points) < 2:
            return cpu_points[0] if cpu_points else None
        prev, last = cpu_points[-2], cpu_points[-1]
        predicted = last + (last - prev)  # one-step linear extrapolation
        return max(0.0, min(100.0, predicted))

    def can_start_task(self, *, predicted: bool = True) -> bool:
        """Admission decision used as the queue's resource gate.

        Returns ``True`` when it's reasonable to start another background task.
        Fails **open** when metrics are unavailable. When ``predicted`` is set,
        a rising CPU trend can defer a start even if the instantaneous reading
        is just under the threshold.
        """
        snap = self.sample()
        if not snap.available:
            return True  # unknown -> don't block the queue
        if self.is_busy(snap):
            return False
        if predicted:
            pred = self.predict_pressure()
            if pred is not None and pred >= self.cpu_threshold:
                return False
        return True

    def gate(self) -> Callable[[], bool]:
        """Return a zero-arg callable suitable as ``TaskQueue(resource_gate=...)``."""
        return lambda: self.can_start_task()

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return (
            f"ResourceMonitor(cpu_threshold={self.cpu_threshold}, "
            f"mem_threshold={self.mem_threshold}, has_psutil={HAS_PSUTIL})"
        )

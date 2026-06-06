"""Hermetic tests for the resource monitor (M7.2).

The sampler and clock are injected so every test feeds synthetic readings and
never depends on the host machine's real CPU/memory load.
"""

from __future__ import annotations

from mimosa.tasks.resource_monitor import (
    DEFAULT_CPU_THRESHOLD,
    DEFAULT_MEM_THRESHOLD,
    HAS_PSUTIL,
    ResourceMonitor,
    ResourceSnapshot,
)


def _sampler(cpu=10.0, mem=20.0, avail=4096.0, load1=0.5):
    return lambda: (cpu, mem, avail, load1)


# --------------------------------------------------------------------------
# sampling
# --------------------------------------------------------------------------

def test_sample_returns_snapshot():
    rm = ResourceMonitor(sampler=_sampler(cpu=33.0), clock=lambda: 100.0)
    snap = rm.sample()
    assert isinstance(snap, ResourceSnapshot)
    assert snap.cpu_percent == 33.0
    assert snap.timestamp == 100.0
    assert snap.available is True


def test_sample_appends_to_history():
    rm = ResourceMonitor(sampler=_sampler(), history=3)
    for _ in range(5):
        rm.sample()
    assert len(rm.history()) == 3  # capped


def test_latest_none_before_sample():
    rm = ResourceMonitor(sampler=_sampler())
    assert rm.latest is None
    rm.sample()
    assert rm.latest is not None


def test_unknown_snapshot_when_all_none():
    rm = ResourceMonitor(sampler=lambda: (None, None, None, None))
    snap = rm.sample()
    assert snap.available is False


def test_sampler_exception_yields_unknown():
    def bad():
        raise RuntimeError("sensor failure")
    rm = ResourceMonitor(sampler=bad)
    snap = rm.sample()
    assert snap.available is False


# --------------------------------------------------------------------------
# is_busy
# --------------------------------------------------------------------------

def test_busy_when_cpu_over_threshold():
    rm = ResourceMonitor(sampler=_sampler(cpu=90.0), cpu_threshold=85.0)
    assert rm.is_busy() is True


def test_busy_when_mem_over_threshold():
    rm = ResourceMonitor(sampler=_sampler(cpu=10.0, mem=95.0), mem_threshold=85.0)
    assert rm.is_busy() is True


def test_not_busy_when_under_thresholds():
    rm = ResourceMonitor(sampler=_sampler(cpu=20.0, mem=30.0))
    assert rm.is_busy() is False


def test_unknown_is_not_busy_failopen():
    rm = ResourceMonitor(sampler=lambda: (None, None, None, None))
    assert rm.is_busy() is False


def test_is_busy_accepts_explicit_snapshot():
    rm = ResourceMonitor(sampler=_sampler())
    snap = ResourceSnapshot(cpu_percent=99.0, available=True)
    assert rm.is_busy(snap) is True


# --------------------------------------------------------------------------
# can_start_task (admission)
# --------------------------------------------------------------------------

def test_can_start_when_idle():
    rm = ResourceMonitor(sampler=_sampler(cpu=10.0, mem=10.0))
    assert rm.can_start_task() is True


def test_cannot_start_when_busy():
    rm = ResourceMonitor(sampler=_sampler(cpu=95.0))
    assert rm.can_start_task() is False


def test_can_start_failopen_when_unknown():
    rm = ResourceMonitor(sampler=lambda: (None, None, None, None))
    assert rm.can_start_task() is True


def test_prediction_defers_start_on_rising_trend():
    # Two rising CPU readings (70 then 80) -> predicted 90 >= 85 threshold.
    readings = iter([(70.0, 20.0, 1.0, 0.5), (80.0, 20.0, 1.0, 0.5)])
    rm = ResourceMonitor(sampler=lambda: next(readings), cpu_threshold=85.0)
    rm.sample()  # 70
    # second sample taken inside can_start_task -> 80, predict 90
    assert rm.can_start_task(predicted=True) is False


def test_prediction_disabled_allows_start():
    readings = iter([(70.0, 20.0, 1.0, 0.5), (80.0, 20.0, 1.0, 0.5)])
    rm = ResourceMonitor(sampler=lambda: next(readings), cpu_threshold=85.0)
    rm.sample()
    assert rm.can_start_task(predicted=False) is True


# --------------------------------------------------------------------------
# predict_pressure
# --------------------------------------------------------------------------

def test_predict_pressure_none_without_data():
    rm = ResourceMonitor(sampler=_sampler())
    assert rm.predict_pressure() is None


def test_predict_pressure_single_point_returns_it():
    rm = ResourceMonitor(sampler=_sampler(cpu=40.0))
    rm.sample()
    assert rm.predict_pressure() == 40.0


def test_predict_pressure_extrapolates():
    readings = iter([(50.0, 0, 0, 0), (60.0, 0, 0, 0)])
    rm = ResourceMonitor(sampler=lambda: next(readings))
    rm.sample()
    rm.sample()
    assert rm.predict_pressure() == 70.0  # 60 + (60-50)


def test_predict_pressure_clamped_to_100():
    readings = iter([(80.0, 0, 0, 0), (95.0, 0, 0, 0)])
    rm = ResourceMonitor(sampler=lambda: next(readings))
    rm.sample()
    rm.sample()
    assert rm.predict_pressure() == 100.0  # 95 + 15 -> clamped


# --------------------------------------------------------------------------
# gate() integration helper
# --------------------------------------------------------------------------

def test_gate_returns_callable():
    rm = ResourceMonitor(sampler=_sampler(cpu=10.0))
    gate = rm.gate()
    assert callable(gate)
    assert gate() is True


def test_gate_reflects_busy_state():
    rm = ResourceMonitor(sampler=_sampler(cpu=99.0))
    assert rm.gate()() is False


# --------------------------------------------------------------------------
# snapshot helpers & misc
# --------------------------------------------------------------------------

def test_snapshot_summary_available():
    snap = ResourceSnapshot(cpu_percent=42.0, mem_percent=55.0, available=True)
    s = snap.summary()
    assert "42% CPU" in s and "55% memory" in s


def test_snapshot_summary_unknown():
    snap = ResourceSnapshot(available=False)
    assert "unknown" in snap.summary()


def test_snapshot_to_dict():
    snap = ResourceSnapshot(cpu_percent=1.0, available=True)
    d = snap.to_dict()
    assert d["cpu_percent"] == 1.0 and d["available"] is True


def test_available_property_matches_flag():
    rm = ResourceMonitor(sampler=_sampler())
    assert rm.available == HAS_PSUTIL


def test_default_thresholds():
    assert DEFAULT_CPU_THRESHOLD == 85.0
    assert DEFAULT_MEM_THRESHOLD == 85.0

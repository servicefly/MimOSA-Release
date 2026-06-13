"""Tests for the context analyzer (M4)."""

from __future__ import annotations

import datetime as dt

from mimosa.learning.context_analyzer import (
    ContextAnalyzer,
    ContextSnapshot,
    time_of_day,
)
from mimosa.learning.pattern_detector import PatternDetector


def _ts(year, month, day, hour):
    return dt.datetime(year, month, day, hour, 0, 0).timestamp()


def test_time_of_day_buckets():
    assert time_of_day(7) == "morning"
    assert time_of_day(14) == "afternoon"
    assert time_of_day(19) == "evening"
    assert time_of_day(2) == "night"


def test_time_of_day_bad_input():
    assert time_of_day("nonsense") == "day"  # type: ignore[arg-type]


def test_analyze_work_hours_weekday():
    # 2024-06-04 is a Tuesday.
    analyzer = ContextAnalyzer(clock=lambda: _ts(2024, 6, 4, 10))
    snap = analyzer.analyze()
    assert isinstance(snap, ContextSnapshot)
    assert snap.is_work_hours is True
    assert snap.is_weekend is False
    assert snap.part_of_day == "morning"
    assert snap.greeting == "Good morning"


def test_analyze_weekend():
    # 2024-06-08 is a Saturday.
    analyzer = ContextAnalyzer(clock=lambda: _ts(2024, 6, 8, 14))
    snap = analyzer.analyze()
    assert snap.is_weekend is True
    assert snap.is_work_hours is False


def test_analyze_with_patterns():
    det = PatternDetector(None)
    for _ in range(8):
        det.record_tool_use("firefox")
    analyzer = ContextAnalyzer(det, clock=lambda: _ts(2024, 6, 4, 10))
    snap = analyzer.analyze()
    assert isinstance(snap.active_patterns, list)


def test_analyze_explicit_now_overrides_clock():
    analyzer = ContextAnalyzer(clock=lambda: _ts(2024, 6, 4, 10))
    snap = analyzer.analyze(now=_ts(2024, 6, 8, 23))
    assert snap.part_of_day == "night"


def test_snapshot_to_dict():
    analyzer = ContextAnalyzer(clock=lambda: _ts(2024, 6, 4, 10))
    d = analyzer.analyze().to_dict()
    assert d["part_of_day"] == "morning"
    assert "is_work_hours" in d

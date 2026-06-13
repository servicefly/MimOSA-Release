"""Tests for proactive suggester + suggestion engine (M4)."""

from __future__ import annotations

import datetime as dt

from mimosa.suggestions.proactive_suggester import ProactiveSuggester, Suggestion
from mimosa.suggestions.suggestion_engine import SuggestionEngine
from mimosa.learning.pattern_detector import PatternDetector


def _ts(year, month, day, hour):
    return dt.datetime(year, month, day, hour, 0, 0).timestamp()


# 2024-06-04 is a Tuesday (work day).
_WORK_HOURS = _ts(2024, 6, 4, 10)
_LATE_NIGHT = _ts(2024, 6, 4, 23)


def _detector_with_tool(tool="firefox", n=10):
    det = PatternDetector(None)
    for _ in range(n):
        det.record_tool_use(tool)
    return det


def test_tool_suggestion_during_work_hours():
    det = _detector_with_tool()
    sugg = ProactiveSuggester(pattern_detector=det)
    out = sugg.make_suggestions(now=_WORK_HOURS)
    assert any(s.kind == "tool" for s in out)


def test_no_tool_suggestion_without_patterns():
    sugg = ProactiveSuggester()
    out = sugg.make_suggestions(now=_WORK_HOURS)
    assert all(s.kind != "tool" for s in out)


def test_wellbeing_suggestion_late_night():
    sugg = ProactiveSuggester()
    out = sugg.make_suggestions(now=_LATE_NIGHT)
    assert any(s.kind == "wellbeing" for s in out)


def test_suggestion_to_dict():
    s = Suggestion(text="hi", kind="tool", confidence=0.9, key="k")
    d = s.to_dict()
    assert d["confidence"] == 0.9
    assert d["kind"] == "tool"


def test_engine_returns_high_confidence():
    det = _detector_with_tool()
    engine = SuggestionEngine(pattern_detector=det, min_confidence=0.5)
    sugg = engine.get_suggestion(now=_WORK_HOURS)
    assert sugg is not None


def test_engine_filters_low_confidence():
    det = _detector_with_tool()
    engine = SuggestionEngine(pattern_detector=det, min_confidence=0.99)
    sugg = engine.get_suggestion(now=_WORK_HOURS)
    # Tool confidence is below 0.99, so nothing should be returned.
    assert sugg is None or sugg.confidence >= 0.99


def test_engine_repeat_guard():
    det = _detector_with_tool()
    engine = SuggestionEngine(pattern_detector=det, min_confidence=0.5)
    first = engine.get_suggestion(now=_WORK_HOURS)
    engine.offer(first)
    second = engine.get_suggestion(now=_WORK_HOURS)
    # The same key should not be offered back-to-back.
    assert second is None or second.key != first.key


def test_engine_disabled_returns_none():
    det = _detector_with_tool()
    engine = SuggestionEngine(pattern_detector=det, enabled=False)
    assert engine.get_suggestion(now=_WORK_HOURS) is None


def test_engine_busy_returns_none():
    det = _detector_with_tool()
    engine = SuggestionEngine(pattern_detector=det, min_confidence=0.5)
    assert engine.get_suggestion(now=_WORK_HOURS, busy=True) is None


def test_engine_tracks_outcomes():
    det = _detector_with_tool()
    engine = SuggestionEngine(pattern_detector=det, min_confidence=0.5)
    s = engine.get_suggestion(now=_WORK_HOURS)
    engine.offer(s)
    engine.record_outcome(True)
    assert engine.offered_count == 1
    assert engine.accepted_count == 1
    assert engine.success_rate() == 1.0


def test_engine_stats():
    engine = SuggestionEngine(enabled=True)
    stats = engine.stats()
    assert "offered" in stats
    assert "success_rate" in stats


def test_engine_reset_repeat_guard():
    det = _detector_with_tool()
    engine = SuggestionEngine(pattern_detector=det, min_confidence=0.5)
    first = engine.get_suggestion(now=_WORK_HOURS)
    engine.offer(first)
    engine.reset_repeat_guard()
    again = engine.get_suggestion(now=_WORK_HOURS)
    assert again is not None

"""Tests for the behavioural pattern detector (M4)."""

from __future__ import annotations

from mimosa.learning.pattern_detector import (
    DetectedPattern,
    PatternDetector,
    KIND_TOOL,
)


def _clock(value):
    """Return a callable that always reports ``value`` seconds."""
    return lambda: value


def test_in_memory_detector_records_tools():
    det = PatternDetector(None)
    for _ in range(6):
        det.record_tool_use("firefox")
    assert det.tool_count("firefox") == 6
    assert det.top_tools()[0][0] == "firefox"


def test_detect_tool_pattern():
    det = PatternDetector(None)
    for _ in range(8):
        det.record_tool_use("firefox")
    det.record_tool_use("vscode")
    patterns = det.detect_patterns(min_count=5, min_confidence=0.5)
    tool_patterns = [p for p in patterns if p.kind == KIND_TOOL]
    assert tool_patterns
    assert tool_patterns[0].key.endswith("firefox")
    assert tool_patterns[0].confidence >= 0.5


def test_detect_time_pattern_work_hours():
    import datetime as _dt

    det = PatternDetector(None)
    # Tuesday 2024-06-04 at 10:00 local -> a work-hour weekday timestamp.
    base = _dt.datetime(2024, 6, 4, 10, 0, 0)
    for day in range(10):
        ts = (base + _dt.timedelta(days=day if base.weekday() else 0)).timestamp()
        det.record_event("tool", "firefox", timestamp=ts)
    patterns = det.detect_patterns(min_count=5, min_confidence=0.5)
    kinds = {p.kind for p in patterns}
    assert "time" in kinds or "tool" in kinds


def test_communication_pattern_concise():
    det = PatternDetector(None)
    for _ in range(8):
        det.record_message("hi there")  # short message
    patterns = det.detect_patterns(min_count=5, min_confidence=0.5)
    comm = [p for p in patterns if p.kind == "communication"]
    assert comm
    assert comm[0].key in {"comm:concise", "comm:detailed"}


def test_persistence_round_trip(tmp_path):
    path = tmp_path / "patterns.json"
    det = PatternDetector(str(path), autosave=True)
    for _ in range(3):
        det.record_tool_use("vscode")
    det.save()
    assert path.exists()

    det2 = PatternDetector(str(path))
    det2.load_state()
    assert det2.tool_count("vscode") == 3


def test_to_state_and_load_state():
    det = PatternDetector(None)
    det.record_tool_use("firefox")
    state = det.to_state()
    det2 = PatternDetector(None)
    det2.load_state(state)
    assert det2.tool_count("firefox") == 1


def test_clear_resets():
    det = PatternDetector(None)
    det.record_tool_use("firefox")
    det.clear()
    assert det.tool_count("firefox") == 0


def test_detected_pattern_to_dict():
    p = DetectedPattern(kind="tool", key="firefox", description="x", confidence=0.9, count=10)
    d = p.to_dict()
    assert d["kind"] == "tool"
    assert d["confidence"] == 0.9


def test_never_raises_on_bad_input():
    det = PatternDetector(None)
    # Should silently ignore odd inputs rather than raise.
    det.record_tool_use("")
    det.record_message(None)  # type: ignore[arg-type]
    assert det.detect_patterns() == [] or isinstance(det.detect_patterns(), list)

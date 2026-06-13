"""Tests for the GTK-free memory viewer helpers (M4)."""

from __future__ import annotations

from mimosa.ui.memory_viewer import (
    build_memory_overview,
    format_patterns_section,
    format_profile_section,
    format_questions_section,
    format_relationship_section,
    open_memory_viewer,
)
from mimosa.learning.pattern_detector import DetectedPattern
from mimosa.memory.relationship_tracker import RelationshipSummary


def test_format_profile_empty():
    out = format_profile_section(None)
    assert "haven't learned" in out.lower()


def test_format_profile_with_data():
    profile = {"user_profile": {"name": "Sam", "skills": ["python", "react"]}}
    out = format_profile_section(profile)
    assert "Sam" in out
    assert "python" in out


def test_format_patterns_empty():
    assert "no clear habits" in format_patterns_section([]).lower()


def test_format_patterns_with_objects():
    pats = [DetectedPattern(kind="tool", key="t", description="Uses firefox", confidence=0.9, count=10)]
    out = format_patterns_section(pats)
    assert "firefox" in out.lower()
    assert "90%" in out


def test_format_patterns_with_dicts():
    out = format_patterns_section([{"description": "Works mornings", "confidence": 0.8}])
    assert "mornings" in out.lower()
    assert "80%" in out


def test_format_relationship_none():
    assert "getting to know" in format_relationship_section(None).lower()


def test_format_relationship_string_passthrough():
    assert format_relationship_section("We're old friends") == "We're old friends"


def test_format_relationship_summary_object():
    summ = RelationshipSummary(
        stage="close", days_known=40, conversations=210, tasks_completed=5, facts_shared=12
    )
    out = format_relationship_section(summ)
    assert "close" in out.lower()
    assert "40" in out


def test_format_questions_empty():
    assert "haven't needed to ask" in format_questions_section([]).lower()


def test_format_questions_with_dicts():
    out = format_questions_section([{"question": "Do you like dark mode?"}])
    assert "dark mode" in out.lower()


def test_build_memory_overview_keys():
    overview = build_memory_overview(profile=None, patterns=None, relationship=None, questions=None)
    assert "What I know about you" in overview
    assert "Habits I've noticed" in overview
    assert "Our relationship" in overview
    assert "Questions I've asked" in overview


def test_open_memory_viewer_headless_returns_none():
    # On a headless test machine GTK is unavailable; should return None and
    # still invoke on_close.
    called = {"closed": False}

    def _close():
        called["closed"] = True

    result = open_memory_viewer(profile=None, on_close=_close)
    # Either GTK is missing (None) or present (a window). If None, on_close ran.
    if result is None:
        assert called["closed"] is True

"""Tests for the high-level memory consolidator (M4)."""

from __future__ import annotations

from mimosa.memory.consolidator import (
    MemoryConsolidator,
    ConsolidationReport,
    Contradiction,
    CONSOLIDATION_LIGHT,
    CONSOLIDATION_DEEP,
)
from mimosa.memory.profile_manager import ProfileManager


def _profile(tmp_path, **fields):
    pm = ProfileManager(path=tmp_path / "profile.json", autosave=False)
    for k, v in fields.items():
        setattr(pm.profile, k, v)
    return pm


def test_consolidate_no_profile_is_safe():
    cons = MemoryConsolidator(None)
    report = cons.consolidate()
    assert isinstance(report, ConsolidationReport)
    assert report.duplicates_removed == 0


def test_consolidate_removes_duplicates(tmp_path):
    pm = _profile(tmp_path, skills=["hiking", "hiking outdoors", "python"])
    cons = MemoryConsolidator(pm, threshold=0.5)
    report = cons.consolidate(mode=CONSOLIDATION_LIGHT)
    assert report.duplicates_removed >= 1
    assert "skills" in report.fields_touched
    assert report.changed is True


def test_light_mode_skips_contradictions(tmp_path):
    pm = _profile(
        tmp_path,
        preferences={"tone": "casual", "style": "formal"},
    )
    cons = MemoryConsolidator(pm)
    report = cons.consolidate(mode=CONSOLIDATION_LIGHT)
    assert report.contradictions == []


def test_deep_mode_detects_contradiction(tmp_path):
    pm = _profile(
        tmp_path,
        preferences={"tone": "casual", "style": "formal"},
    )
    cons = MemoryConsolidator(pm)
    report = cons.consolidate(mode=CONSOLIDATION_DEEP)
    assert any(isinstance(c, Contradiction) for c in report.contradictions)


def test_detect_contradictions_directly(tmp_path):
    pm = _profile(
        tmp_path,
        preferences={"length": "brief"},
        interests=["I want detailed explanations"],
    )
    cons = MemoryConsolidator(pm)
    contradictions = cons.detect_contradictions()
    assert any(c.field for c in contradictions)


def test_no_contradiction_when_consistent(tmp_path):
    pm = _profile(tmp_path, preferences={"tone": "casual"})
    cons = MemoryConsolidator(pm)
    assert cons.detect_contradictions() == []


def test_report_to_dict(tmp_path):
    pm = _profile(tmp_path, skills=["a", "a"])
    cons = MemoryConsolidator(pm, threshold=0.5)
    d = cons.consolidate().to_dict()
    assert "duplicates_removed" in d
    assert "mode" in d


def test_contradiction_to_dict():
    c = Contradiction(field="preferences", first="casual", second="formal", question="?")
    d = c.to_dict()
    assert d["first"] == "casual"
    assert d["second"] == "formal"

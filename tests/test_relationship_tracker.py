"""Tests for the relationship tracker (M4)."""

from __future__ import annotations

from mimosa.memory.relationship_tracker import (
    RelationshipTracker,
    RelationshipSummary,
    STAGE_NEW,
    STAGE_FAMILIAR,
    STAGE_CLOSE,
)

_DAY = 86400.0


def test_new_relationship_starts_new():
    clk = [1_000_000.0]
    rt = RelationshipTracker(None, clock=lambda: clk[0])
    assert rt.stage() == STAGE_NEW


def test_becomes_familiar_by_days():
    clk = [1_000_000.0]
    rt = RelationshipTracker(None, clock=lambda: clk[0])
    clk[0] += 8 * _DAY
    assert rt.stage() == STAGE_FAMILIAR


def test_becomes_familiar_by_conversations():
    clk = [1_000_000.0]
    rt = RelationshipTracker(None, clock=lambda: clk[0])
    rt.record_conversation(60)
    assert rt.stage() == STAGE_FAMILIAR


def test_becomes_close_by_days():
    clk = [1_000_000.0]
    rt = RelationshipTracker(None, clock=lambda: clk[0])
    clk[0] += 31 * _DAY
    assert rt.stage() == STAGE_CLOSE


def test_becomes_close_by_conversations():
    clk = [1_000_000.0]
    rt = RelationshipTracker(None, clock=lambda: clk[0])
    rt.record_conversation(250)
    assert rt.stage() == STAGE_CLOSE


def test_tone_guidance_changes_with_stage():
    clk = [1_000_000.0]
    rt = RelationshipTracker(None, clock=lambda: clk[0])
    new_tone = rt.tone_guidance()
    clk[0] += 31 * _DAY
    close_tone = rt.tone_guidance()
    assert new_tone != close_tone
    assert isinstance(close_tone, str) and close_tone


def test_days_since_install():
    clk = [1_000_000.0]
    rt = RelationshipTracker(None, clock=lambda: clk[0])
    clk[0] += 10 * _DAY
    assert rt.days_since_install() == 10


def test_summary():
    clk = [1_000_000.0]
    rt = RelationshipTracker(None, clock=lambda: clk[0])
    rt.record_conversation(5)
    rt.record_task(2)
    rt.set_facts_shared(3)
    summ = rt.summary()
    assert isinstance(summ, RelationshipSummary)
    assert summ.conversations == 5
    assert summ.tasks_completed == 2
    assert summ.facts_shared == 3


def test_friendly_lines():
    clk = [1_000_000.0]
    rt = RelationshipTracker(None, clock=lambda: clk[0])
    lines = rt.summary().friendly_lines()
    assert isinstance(lines, dict)


def test_persistence_round_trip(tmp_path):
    path = tmp_path / "rel.json"
    clk = [1_000_000.0]
    rt = RelationshipTracker(str(path), autosave=True, clock=lambda: clk[0])
    rt.record_conversation(3)
    rt.save()
    assert path.exists()

    rt2 = RelationshipTracker(str(path), clock=lambda: clk[0])
    assert rt2.total_conversations == 3
    # Install date preserved (not re-stamped).
    assert rt2.install_date == rt.install_date


def test_reset():
    clk = [1_000_000.0]
    rt = RelationshipTracker(None, clock=lambda: clk[0])
    rt.record_conversation(5)
    rt.reset()
    assert rt.total_conversations == 0

"""Tests for memory consolidation helpers (M3)."""

from __future__ import annotations

from mimosa.memory.memory_consolidator import (
    consolidate_facts,
    consolidate_texts,
    normalise_text,
    text_similarity,
)


def test_normalise_text():
    assert normalise_text("  Hello   World  ") == "hello world"
    assert normalise_text("") == ""


def test_text_similarity_identical():
    assert text_similarity("hiking", "hiking") == 1.0


def test_text_similarity_partial():
    assert 0.0 < text_similarity("I like hiking", "I enjoy hiking outdoors") <= 1.0


def test_text_similarity_disjoint():
    assert text_similarity("python", "swimming") == 0.0


def test_consolidate_texts_collapses_near_dupes():
    res = consolidate_texts(
        ["I like hiking", "I really enjoy hiking", "I play guitar"]
    )
    assert res.removed == 1
    assert len(res.items) == 2


def test_consolidate_texts_keeps_longer():
    res = consolidate_texts(["hiking", "hiking in the mountains"], threshold=0.4)
    assert "hiking in the mountains" in res.items


def test_consolidate_texts_skips_empty():
    res = consolidate_texts(["", "  ", "guitar"])
    assert res.items == ["guitar"]


def test_consolidate_facts_groups_by_field():
    facts = [
        {"field": "interests", "value": "hiking"},
        {"field": "interests", "value": "hiking outdoors"},
        {"field": "interests", "value": "guitar"},
        {"field": "tools", "key": "editor", "value": "vim"},
        {"field": "tools", "key": "editor", "value": "vim"},
    ]
    res = consolidate_facts(facts, threshold=0.5)
    fields = [(f["field"], f.get("key"), f["value"]) for f in res.items]
    assert ("interests", None, "guitar") in fields
    assert ("tools", "editor", "vim") in fields
    assert res.removed == 2


def test_consolidate_facts_never_raises():
    res = consolidate_facts([{"field": "x"}, "junk", 123])
    assert isinstance(res.items, list)

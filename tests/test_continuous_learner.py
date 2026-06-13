"""Tests for the continuous learner (M4)."""

from __future__ import annotations

from mimosa.learning.continuous_learner import (
    ContinuousLearner,
    LearningOpportunity,
    detect_tool_mentions,
)
from mimosa.learning.pattern_detector import PatternDetector
from mimosa.memory.profile_manager import ProfileManager


class _StubExtractor:
    """A fact extractor that returns a fixed list."""

    def __init__(self, facts):
        self._facts = facts

    def extract(self, text, topic=None, question=None):
        return list(self._facts)


def test_detect_tool_mentions_known_tool():
    tools = detect_tool_mentions("I use firefox every day")
    assert "firefox" in tools


def test_detect_tool_mentions_open_verb():
    tools = detect_tool_mentions("open inkscape please")
    assert any("inkscape" in t for t in tools)


def test_detect_tool_mentions_empty():
    assert detect_tool_mentions("") == []


def test_analyze_conversation_disabled_is_noop():
    learner = ContinuousLearner(enabled=False)
    report = learner.analyze_conversation("I love firefox")
    assert report == {"facts": [], "tools": [], "stored": False, "applied": 0}


def test_analyze_conversation_extracts_and_applies(tmp_path):
    prof = ProfileManager(path=tmp_path / "profile.json", autosave=False)
    extractor = _StubExtractor([{"field": "skills", "value": "python"}])
    learner = ContinuousLearner(
        fact_extractor=extractor,
        profile_manager=prof,
    )
    report = learner.analyze_conversation("I write python code")
    assert report["applied"] >= 1
    assert "python" in [s.lower() for s in prof.profile.skills]


def test_analyze_conversation_records_patterns():
    det = PatternDetector(None)
    learner = ContinuousLearner(fact_extractor=_StubExtractor([]), pattern_detector=det)
    learner.analyze_conversation("I use firefox a lot")
    assert det.tool_count("firefox") >= 1


def test_analyze_conversation_empty_message():
    learner = ContinuousLearner(fact_extractor=_StubExtractor([]))
    report = learner.analyze_conversation("   ")
    assert report["applied"] == 0


def test_detect_learning_opportunities_from_patterns():
    det = PatternDetector(None)
    for _ in range(10):
        det.record_tool_use("firefox")
    learner = ContinuousLearner(fact_extractor=_StubExtractor([]), pattern_detector=det)
    opps = learner.detect_learning_opportunities()
    assert isinstance(opps, list)
    assert all(isinstance(o, LearningOpportunity) for o in opps)


def test_detect_learning_opportunities_from_history():
    learner = ContinuousLearner(fact_extractor=_StubExtractor([]))
    history = ["I had lunch with Sarah today", "She is my colleague"]
    opps = learner.detect_learning_opportunities(history)
    assert isinstance(opps, list)


def test_opportunity_to_dict():
    opp = LearningOpportunity(
        kind="preference", subject="firefox", question="Do you prefer firefox?", confidence=0.8
    )
    d = opp.to_dict()
    assert d["subject"] == "firefox"
    assert d["confidence"] == 0.8

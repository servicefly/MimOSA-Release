"""Tests for the proactive questioner (M4)."""

from __future__ import annotations

import datetime as dt

from mimosa.learning.proactive_questioner import (
    ProactiveQuestion,
    ProactiveQuestioner,
)
from mimosa.learning.continuous_learner import LearningOpportunity


def _ts(year, month, day, hour=12):
    return dt.datetime(year, month, day, hour, 0, 0).timestamp()


def test_enqueue_and_pending():
    q = ProactiveQuestioner(None)
    q.enqueue("Do you prefer firefox?", subject="firefox")
    assert len(q.pending) == 1


def test_enqueue_dedup():
    q = ProactiveQuestioner(None)
    q.enqueue("Do you prefer firefox?", subject="firefox")
    q.enqueue("Do you prefer firefox?", subject="firefox")
    assert len(q.pending) == 1


def test_enqueue_empty_skipped():
    q = ProactiveQuestioner(None)
    assert q.enqueue("   ") is None
    assert len(q.pending) == 0


def test_enqueue_opportunity():
    q = ProactiveQuestioner(None)
    opp = LearningOpportunity(
        kind="preference", subject="vscode", question="Use vscode often?", confidence=0.8
    )
    q.enqueue_opportunity(opp)
    assert len(q.pending) == 1


def test_should_ask_respects_busy():
    q = ProactiveQuestioner(None, max_per_day=2)
    q.enqueue("Question?")
    assert q.should_ask(busy=True) is False
    assert q.should_ask(busy=False) is True


def test_rate_limit_per_day():
    clk = [_ts(2024, 6, 4, 9)]
    q = ProactiveQuestioner(None, max_per_day=1, clock=lambda: clk[0])
    q.enqueue("Q1?")
    q.enqueue("Q2?")
    first = q.next_question()
    assert first is not None
    q.record_answer(first.id, "yes")
    # Daily limit hit -> no more today.
    assert q.should_ask() is False


def test_next_day_resets_limit():
    clk = [_ts(2024, 6, 4, 9)]
    q = ProactiveQuestioner(None, max_per_day=1, clock=lambda: clk[0])
    q.enqueue("Q1?")
    q.enqueue("Q2?")
    first = q.next_question()
    q.record_answer(first.id, "yes")
    assert q.should_ask() is False
    # Advance one day.
    clk[0] = _ts(2024, 6, 5, 9)
    assert q.should_ask() is True


def test_record_answer_moves_to_asked():
    q = ProactiveQuestioner(None)
    qobj = q.enqueue("Q?")
    assert q.record_answer(qobj.id, "an answer") is True
    assert len(q.pending) == 0
    assert len(q.asked) == 1


def test_dismiss():
    q = ProactiveQuestioner(None)
    qobj = q.enqueue("Q?")
    assert q.dismiss(qobj.id) is True
    assert len(q.pending) == 0


def test_register_patterns():
    class _P:
        description = "Uses firefox a lot"
        key = "tool:firefox"
        confidence = 0.9

    q = ProactiveQuestioner(None)
    assert q.register_patterns([_P()]) == 1
    assert len(q.patterns_detected) == 1


def test_persistence_round_trip(tmp_path):
    path = tmp_path / "pq.json"
    q = ProactiveQuestioner(str(path), autosave=True)
    q.enqueue("Persisted?", subject="x")
    q.save()
    assert path.exists()

    q2 = ProactiveQuestioner(str(path))
    assert len(q2.pending) == 1


def test_question_to_from_dict():
    q = ProactiveQuestion(text="Hello?", kind="preference", subject="x")
    restored = ProactiveQuestion.from_dict(q.to_dict())
    assert restored.text == "Hello?"
    assert restored.subject == "x"

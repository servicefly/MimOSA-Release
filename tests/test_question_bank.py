"""Tests for the onboarding question bank (M3)."""

from __future__ import annotations

from mimosa.onboarding.question_bank import (
    QUESTION_BANK,
    all_topics,
    get_topic,
    total_topics,
)


def test_seven_topics():
    assert total_topics() == 7
    assert len(all_topics()) == 7


def test_topics_have_unique_ids():
    ids = [t.id for t in QUESTION_BANK]
    assert len(ids) == len(set(ids))


def test_expected_topic_ids_present():
    ids = {t.id for t in QUESTION_BANK}
    for expected in (
        "introduction",
        "professional_life",
        "interests_hobbies",
        "lifestyle_preferences",
        "system_usage",
        "assistance_style",
        "relationships_goals",
    ):
        assert expected in ids


def test_each_topic_has_questions_and_intro():
    for topic in QUESTION_BANK:
        assert topic.intro
        assert topic.questions
        assert topic.primary is not None
        for q in topic.questions:
            assert q.text
            assert isinstance(q.follow_ups, tuple)
            assert isinstance(q.profile_fields, tuple)


def test_question_ids_unique():
    ids = [q.id for t in QUESTION_BANK for q in t.questions]
    assert len(ids) == len(set(ids))


def test_get_topic():
    assert get_topic("introduction") is not None
    assert get_topic("nope") is None

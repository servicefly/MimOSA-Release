"""Tests for onboarding response-depth analysis (M3)."""

from __future__ import annotations

import random

from mimosa.onboarding.response_analyzer import (
    ResponseDepth,
    analyze_response_depth,
    encouragement_for,
    is_vague,
)


def test_shallow_single_word():
    assert analyze_response_depth("hiking") == ResponseDepth.SHALLOW


def test_shallow_empty():
    assert analyze_response_depth("") == ResponseDepth.SHALLOW


def test_medium_short_sentence():
    assert analyze_response_depth("I work as a software engineer") == ResponseDepth.MEDIUM


def test_deep_multi_sentence():
    text = (
        "I love hiking in the mountains every weekend. It really clears my "
        "head. I also enjoy photography along the way."
    )
    assert analyze_response_depth(text) == ResponseDepth.DEEP


def test_is_vague():
    assert is_vague("not sure")
    assert is_vague("idk")
    assert is_vague("")
    assert not is_vague("I am a teacher")


def test_vague_classified_shallow():
    assert analyze_response_depth("dunno") == ResponseDepth.SHALLOW


def test_encouragement_returns_string():
    rng = random.Random(0)
    msg = encouragement_for("professional_life", rng=rng)
    assert isinstance(msg, str) and msg


def test_encouragement_vague_pool():
    rng = random.Random(0)
    msg = encouragement_for("introduction", vague=True, rng=rng)
    assert isinstance(msg, str) and msg


def test_encouragement_unknown_topic():
    msg = encouragement_for("no_such_topic")
    assert isinstance(msg, str) and msg

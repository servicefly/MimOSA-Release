"""Tests for the Privacy Guard (M5.4 — hybrid sensitive-topic detector)."""

from __future__ import annotations

import pytest

from mimosa.memory.preference_learner import PreferenceLearner
from mimosa.memory.privacy_guard import (
    PrivacyAssessment,
    PrivacyGuard,
    Sensitivity,
)


@pytest.fixture
def guard():
    return PrivacyGuard()


# -- empty / public --------------------------------------------------------


def test_empty_text_is_public(guard):
    a = guard.assess("")
    assert a.is_private is False
    assert a.sensitivity is Sensitivity.PUBLIC


def test_innocuous_text_is_public(guard):
    a = guard.assess("what is the capital of France?")
    assert a.is_private is False
    assert a.source == "none"


def test_is_private_convenience(guard):
    assert guard.is_private("I was diagnosed with cancer") is True
    assert guard.is_private("tell me a joke") is False


def test_should_use_local_alias(guard):
    assert guard.should_use_local("my bank account number is important") is True


# -- keyword tier ----------------------------------------------------------


@pytest.mark.parametrize(
    "text,category",
    [
        ("I was diagnosed with depression", "medical"),
        ("my mortgage and loan debt is huge", "financial"),
        ("I need to talk to my attorney about the lawsuit", "legal"),
        ("here is my password for the account", "credentials"),
        ("my passport number and home address", "personal"),
        ("I'm dealing with an addiction and rehab", "relationships"),
    ],
)
def test_keyword_categories(guard, text, category):
    a = guard.assess(text)
    assert a.is_private is True
    assert category in a.categories


def test_multiple_matches_raise_confidence(guard):
    one = guard.assess("I have anxiety")
    many = guard.assess("I have anxiety, depression, and started therapy and medication")
    assert many.confidence >= one.confidence


def test_keyword_whole_word_only(guard):
    # "class" must not trigger the "ssn" personal keyword via substring.
    a = guard.assess("I attended a class today")
    assert a.is_private is False


def test_matched_terms_reported(guard):
    a = guard.assess("my diagnosis was shared with the therapist")
    assert "diagnosis" in a.matched_terms or "therapist" in a.matched_terms


# -- pattern tier ----------------------------------------------------------


def test_credit_card_pattern(guard):
    a = guard.assess("charge it to 4111 1111 1111 1111 please")
    assert a.is_private is True
    assert a.source == "pattern"
    assert a.confidence >= 0.9


def test_ssn_pattern(guard):
    a = guard.assess("my number is 123-45-6789")
    assert a.is_private is True
    assert "personal" in a.categories


def test_secret_disclosure_pattern(guard):
    a = guard.assess("the api key = sk-abc123xyz")
    assert a.is_private is True
    assert a.source == "pattern"


def test_pattern_beats_keyword_priority(guard):
    # Structured pattern should short-circuit with high confidence.
    a = guard.assess("my password is 123-45-6789")
    assert a.source == "pattern"


# -- threshold / fail-safe -------------------------------------------------


def test_custom_threshold_low_makes_private():
    g = PrivacyGuard(threshold=0.5)
    # A single keyword (base 0.7) is private at default; verify threshold logic.
    assert g.assess("I have anxiety").is_private is True


def test_high_threshold_can_demote_single_keyword():
    g = PrivacyGuard(threshold=0.99)
    a = g.assess("I have anxiety")  # confidence ~0.7 < 0.99
    assert a.is_private is False
    assert a.sensitivity is Sensitivity.UNKNOWN


def test_fail_safe_private_default_true():
    g = PrivacyGuard(fail_safe_private=True)
    a = g.assess("totally neutral sentence about clouds")
    assert a.is_private is True
    assert a.sensitivity is Sensitivity.UNKNOWN


def test_fail_safe_off_default_public(guard):
    a = guard.assess("totally neutral sentence about clouds")
    assert a.is_private is False


# -- extra keywords --------------------------------------------------------


def test_extra_keywords_merged():
    g = PrivacyGuard(extra_keywords={"work": ["projectfalcon"]})
    a = g.assess("how is projectfalcon progressing")
    assert a.is_private is True
    assert "work" in a.categories


# -- learned user terms (tier 2) ------------------------------------------


def test_learned_private_term_detected():
    pl = PreferenceLearner(":memory:")
    g = PrivacyGuard(learner=pl)
    g.learn_private_term("moonbase")
    a = g.assess("any updates on moonbase today")
    assert a.is_private is True
    assert "user_pattern" in a.categories
    pl.close()


def test_learn_private_term_persists(tmp_path):
    db = tmp_path / "prefs.db"
    pl = PreferenceLearner(db)
    g = PrivacyGuard(learner=pl)
    g.learn_private_term("moonbase")
    pl.close()

    pl2 = PreferenceLearner(db)
    g2 = PrivacyGuard(learner=pl2)
    assert g2.is_private("moonbase status") is True
    pl2.close()


def test_learn_private_term_no_learner_noop(guard):
    # No learner attached: should not raise.
    guard.learn_private_term("whatever")
    assert guard.is_private("whatever") is False


def test_learner_bumps_confidence():
    pl = PreferenceLearner(":memory:")
    g = PrivacyGuard(learner=pl)
    g.learn_private_term("moonbase")
    a = g.assess("moonbase")
    assert a.confidence >= 0.7
    pl.close()


# -- LLM tier (tier 3) -----------------------------------------------------


def test_llm_tier_consulted_for_ambiguous():
    calls = []

    def classifier(text):
        calls.append(text)
        return True

    g = PrivacyGuard(llm_classifier=classifier)
    a = g.assess("an ambiguous sentence with no keywords")
    assert calls  # classifier was consulted
    assert a.is_private is True
    assert a.source == "llm"


def test_llm_tier_not_consulted_when_keyword_hits():
    calls = []

    def classifier(text):
        calls.append(text)
        return True

    g = PrivacyGuard(llm_classifier=classifier)
    g.assess("I was diagnosed with cancer")  # keyword short-circuits
    assert calls == []


def test_llm_tier_disabled_per_call():
    g = PrivacyGuard(llm_classifier=lambda t: True)
    a = g.assess("ambiguous text", allow_llm=False)
    assert a.source == "none"


def test_llm_tuple_return():
    g = PrivacyGuard(llm_classifier=lambda t: (True, 0.88))
    a = g.assess("ambiguous text")
    assert a.is_private is True
    assert a.confidence == pytest.approx(0.88)


def test_llm_float_return():
    g = PrivacyGuard(llm_classifier=lambda t: 0.9, threshold=0.6)
    a = g.assess("ambiguous text")
    assert a.is_private is True


def test_llm_exception_is_non_fatal():
    def boom(text):
        raise RuntimeError("model crashed")

    g = PrivacyGuard(llm_classifier=boom)
    a = g.assess("ambiguous text")  # should not raise
    assert a.source == "none"


def test_llm_none_return_falls_through():
    g = PrivacyGuard(llm_classifier=lambda t: None)
    a = g.assess("ambiguous text")
    assert a.source == "none"


# -- redaction -------------------------------------------------------------


def test_redact_credit_card(guard):
    out = guard.redact("pay with 4111 1111 1111 1111 now")
    assert "4111" not in out
    assert "[REDACTED]" in out


def test_redact_ssn(guard):
    out = guard.redact("ssn 123-45-6789")
    assert "123-45-6789" not in out


def test_redact_custom_placeholder(guard):
    out = guard.redact("ssn 123-45-6789", placeholder="***")
    assert "***" in out


def test_redact_empty(guard):
    assert guard.redact("") == ""


def test_redact_leaves_clean_text(guard):
    assert guard.redact("nothing secret here") == "nothing secret here"


# -- provider routing ------------------------------------------------------


def test_create_provider_for_private(guard):
    provider, use_local = guard.create_provider_for("I was diagnosed with cancer")
    assert use_local is True
    assert provider.is_local is True


def test_create_provider_for_public(guard):
    provider, use_local = guard.create_provider_for("what time is it in Tokyo")
    assert use_local is False


# -- dataclass -------------------------------------------------------------


def test_assessment_to_dict():
    a = PrivacyAssessment(True, Sensitivity.SENSITIVE, 0.9, ["medical"], ["cancer"], "keyword", "r")
    d = a.to_dict()
    assert d["is_private"] is True
    assert d["sensitivity"] == "sensitive"
    assert d["categories"] == ["medical"]


def test_repr_smoke(guard):
    assert "PrivacyGuard" in repr(guard)

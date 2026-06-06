"""Hermetic tests for local error-fix learning (M7.3).

Uses an in-memory ``PreferenceLearner`` so nothing touches disk or the network.
"""

from __future__ import annotations

import pytest

from mimosa.memory.preference_learner import PreferenceLearner
from mimosa.tasks.error_learner import (
    ERROR_LEARNING_CATEGORY,
    ErrorFixLearner,
    FixSuggestion,
    normalize_error,
)


@pytest.fixture()
def learner():
    pl = PreferenceLearner(db_path=":memory:")
    yield pl
    pl.close()


@pytest.fixture()
def el(learner):
    return ErrorFixLearner(learner)


# --------------------------------------------------------------------------
# normalize_error
# --------------------------------------------------------------------------

def test_normalize_blank():
    assert normalize_error("") == ""
    assert normalize_error(None) == ""


def test_normalize_lowercases():
    assert normalize_error("ERROR Happened") == "error happened"


def test_normalize_strips_paths():
    sig = normalize_error("cannot open /home/user/file.txt")
    assert "/home/user" not in sig
    assert "<path>" in sig


def test_normalize_strips_numbers():
    sig = normalize_error("failed after 42 retries")
    assert "42" not in sig
    assert "<n>" in sig


def test_normalize_strips_quoted_strings():
    sig = normalize_error("module 'foo' not found")
    assert "foo" not in sig
    assert "<str>" in sig


def test_normalize_strips_hex():
    sig = normalize_error("segfault at 0xdeadbeef")
    assert "0xdeadbeef" not in sig
    assert "<hex>" in sig


def test_normalize_generalizes_across_paths():
    a = normalize_error("FileNotFoundError: /home/a/x.txt missing")
    b = normalize_error("FileNotFoundError: /var/log/y.log missing")
    assert a == b  # same shape -> same signature


def test_normalize_truncates_long():
    sig = normalize_error("e" * 500)
    assert len(sig) <= 120


# --------------------------------------------------------------------------
# record_fix / suggest_fix
# --------------------------------------------------------------------------

def test_record_and_suggest(el):
    for _ in range(3):
        el.record_fix("ConnectionError: timed out", "retry with backoff")
    sug = el.suggest_fix("ConnectionError: timed out")
    assert sug is not None
    assert sug.fix == "retry with backoff"
    assert isinstance(sug, FixSuggestion)


def test_suggest_generalizes(el):
    for _ in range(3):
        el.record_fix("FileNotFoundError: /home/a/x.txt missing", "mkdir parent")
    # Different path, same shape -> still suggested.
    sug = el.suggest_fix("FileNotFoundError: /tmp/other.log missing")
    assert sug is not None
    assert sug.fix == "mkdir parent"


def test_low_evidence_below_threshold(el):
    el.record_fix("RareError: boom", "do thing")  # single observation
    # default threshold 0.6; one observation is damped below that
    assert el.suggest_fix("RareError: boom") is None


def test_threshold_override(el):
    el.record_fix("RareError: boom", "do thing")
    assert el.suggest_fix("RareError: boom", threshold=0.0) is not None


def test_record_blank_fix_noop(el):
    assert el.record_fix("SomeError: x", "  ") is False
    assert el.suggest_fix("SomeError: x", threshold=0.0) is None


def test_record_blank_error_noop(el):
    assert el.record_fix("", "a fix") is False


# --------------------------------------------------------------------------
# disabled / unwired degradation
# --------------------------------------------------------------------------

def test_unwired_learner_is_safe():
    el = ErrorFixLearner(None)
    assert el.available is False
    assert el.record_fix("e", "f") is False
    assert el.suggest_fix("e") is None
    assert el.candidates("e") == []
    assert el.forget("e") == 0


def test_disabled_does_not_record(learner):
    el = ErrorFixLearner(learner, enabled=False)
    assert el.record_fix("Err: x", "fix") is False
    assert el.suggest_fix("Err: x", threshold=0.0) is None


def test_available_true_when_wired(el):
    assert el.available is True


# --------------------------------------------------------------------------
# candidates / explain / forget
# --------------------------------------------------------------------------

def test_candidates_sorted_best_first(el):
    for _ in range(5):
        el.record_fix("Err: x", "good fix")
    el.record_fix("Err: x", "bad fix")
    cands = el.candidates("Err: x")
    assert cands[0].fix == "good fix"
    assert len(cands) == 2


def test_candidates_empty_for_unknown(el):
    assert el.candidates("Never: seen") == []


def test_explain_no_signature(el):
    assert "no error signature" in el.explain("").lower()


def test_explain_unknown(el):
    msg = el.explain("Brand: new error")
    assert "no fix learned yet" in msg.lower()


def test_explain_with_data(el):
    for _ in range(3):
        el.record_fix("Err: x", "the fix")
    msg = el.explain("Err: x")
    assert "the fix" in msg
    assert "%" in msg  # confidence shown


def test_forget_removes(el):
    for _ in range(3):
        el.record_fix("Err: x", "the fix")
    removed = el.forget("Err: x")
    assert removed >= 1
    assert el.suggest_fix("Err: x", threshold=0.0) is None


def test_forget_specific_value(el):
    for _ in range(3):
        el.record_fix("Err: x", "fix a")
        el.record_fix("Err: x", "fix b")
    el.forget("Err: x", "fix a")
    fixes = {c.fix for c in el.candidates("Err: x")}
    assert "fix a" not in fixes
    assert "fix b" in fixes


# --------------------------------------------------------------------------
# storage details
# --------------------------------------------------------------------------

def test_uses_dedicated_category(learner, el):
    el.record_fix("Err: x", "the fix")
    assert ERROR_LEARNING_CATEGORY in learner.categories()


def test_fix_suggestion_to_dict():
    s = FixSuggestion(signature="sig", fix="f", confidence=0.9, count=3)
    d = s.to_dict()
    assert d["fix"] == "f" and d["count"] == 3

"""Tests for the preference learner (M5.2 — Preference Learning)."""

from __future__ import annotations

import time

import pytest

from mimosa.memory.preference_learner import (
    LearnedPreference,
    PreferenceLearner,
)


@pytest.fixture
def learner():
    pl = PreferenceLearner(":memory:", evidence_saturation=3)
    yield pl
    pl.close()


# -- observation -----------------------------------------------------------


def test_observe_creates_row(learner):
    learner.observe("file_open", "pdf", "okular")
    prefs = learner.get_preferences("file_open", "pdf")
    assert len(prefs) == 1
    assert prefs[0].value == "okular"
    assert prefs[0].count == 1


def test_repeated_observation_increments_count(learner):
    for _ in range(4):
        learner.observe("file_open", "pdf", "okular")
    assert learner.get_preferences("file_open", "pdf")[0].count == 4


def test_observe_blank_fields_ignored(learner):
    learner.observe("", "pdf", "okular")
    learner.observe("file_open", "", "okular")
    learner.observe("file_open", "pdf", "")
    assert learner.get_preferences("file_open", "pdf") == []


def test_observe_strips_whitespace(learner):
    learner.observe("  file_open ", " pdf ", " okular ")
    prefs = learner.get_preferences("file_open", "pdf")
    assert prefs[0].value == "okular"


def test_observe_disabled_is_noop():
    pl = PreferenceLearner(":memory:", enabled=False)
    pl.observe("file_open", "pdf", "okular")
    assert pl.get_preferences("file_open", "pdf") == []
    pl.close()


def test_observe_weight_accumulates(learner):
    learner.observe("a", "b", "c", weight=2.5)
    learner.observe("a", "b", "c", weight=1.5)
    pref = learner.get_preferences("a", "b")[0]
    assert pref.weight == pytest.approx(4.0)
    assert pref.count == 2


def test_observe_nonpositive_weight_ignored(learner):
    learner.observe("a", "b", "c", weight=0)
    learner.observe("a", "b", "c", weight=-3)
    assert learner.get_preferences("a", "b") == []


def test_observe_invalid_weight_defaults_to_one(learner):
    learner.observe("a", "b", "c", weight="bad")  # type: ignore[arg-type]
    assert learner.get_preferences("a", "b")[0].weight == pytest.approx(1.0)


# -- confidence ------------------------------------------------------------


def test_confidence_grows_with_evidence(learner):
    learner.observe("file_open", "pdf", "okular")
    c1 = learner.get_preferences("file_open", "pdf")[0].confidence
    for _ in range(5):
        learner.observe("file_open", "pdf", "okular")
    c2 = learner.get_preferences("file_open", "pdf")[0].confidence
    assert c2 > c1


def test_confidence_reflects_dominance(learner):
    for _ in range(8):
        learner.observe("file_open", "pdf", "okular")
    for _ in range(2):
        learner.observe("file_open", "pdf", "evince")
    prefs = {p.value: p.confidence for p in learner.get_preferences("file_open", "pdf")}
    assert prefs["okular"] > prefs["evince"]


def test_confidence_capped_at_one(learner):
    for _ in range(100):
        learner.observe("a", "b", "c")
    assert learner.get_preferences("a", "b")[0].confidence <= 1.0


def test_single_observation_damped_below_full(learner):
    learner.observe("a", "b", "c")
    # Unanimous but only one data point -> evidence factor < 1.
    assert learner.get_preferences("a", "b")[0].confidence < 1.0


def test_preferences_sorted_best_first(learner):
    learner.observe("a", "b", "rare")
    for _ in range(5):
        learner.observe("a", "b", "common")
    prefs = learner.get_preferences("a", "b")
    assert prefs[0].value == "common"


# -- prediction ------------------------------------------------------------


def test_predict_returns_none_below_threshold(learner):
    learner.observe("a", "b", "c")  # one obs -> low confidence
    assert learner.predict("a", "b") is None


def test_predict_returns_value_when_confident(learner):
    for _ in range(8):
        learner.observe("file_open", "pdf", "okular")
    pred = learner.predict("file_open", "pdf")
    assert pred is not None and pred.value == "okular"


def test_predict_value_convenience(learner):
    for _ in range(8):
        learner.observe("app", "music", "spotify")
    assert learner.predict_value("app", "music") == "spotify"


def test_predict_value_default_when_unknown(learner):
    assert learner.predict_value("x", "y", default="fallback") == "fallback"


def test_predict_threshold_override(learner):
    learner.observe("a", "b", "c")
    assert learner.predict("a", "b", threshold=0.0) is not None


def test_predict_unknown_returns_none(learner):
    assert learner.predict("nope", "nada") is None


# -- introspection ---------------------------------------------------------


def test_categories_and_keys(learner):
    learner.observe("file_open", "pdf", "okular")
    learner.observe("file_open", "png", "gwenview")
    learner.observe("app_launch", "music", "spotify")
    assert set(learner.categories()) == {"file_open", "app_launch"}
    assert set(learner.keys("file_open")) == {"pdf", "png"}


def test_all_preferences_min_confidence_filter(learner):
    learner.observe("a", "b", "weak")           # low confidence
    for _ in range(8):
        learner.observe("c", "d", "strong")     # high confidence
    strong = learner.all_preferences(min_confidence=0.6)
    assert all(p.confidence >= 0.6 for p in strong)
    assert any(p.value == "strong" for p in strong)
    assert not any(p.value == "weak" for p in strong)


def test_explain_human_readable(learner):
    for _ in range(3):
        learner.observe("file_open", "pdf", "okular")
    text = learner.explain("file_open", "pdf")
    assert "okular" in text and "file_open/pdf" in text


def test_explain_unknown(learner):
    assert "No preference" in learner.explain("x", "y")


# -- forgetting ------------------------------------------------------------


def test_forget_value(learner):
    learner.observe("a", "b", "c")
    learner.observe("a", "b", "d")
    removed = learner.forget("a", "b", "c")
    assert removed == 1
    values = {p.value for p in learner.get_preferences("a", "b")}
    assert values == {"d"}


def test_forget_key(learner):
    learner.observe("a", "b", "c")
    learner.observe("a", "e", "f")
    learner.forget("a", "b")
    assert learner.get_preferences("a", "b") == []
    assert learner.get_preferences("a", "e")


def test_forget_category(learner):
    learner.observe("a", "b", "c")
    learner.observe("a", "e", "f")
    removed = learner.forget("a")
    assert removed == 2
    assert learner.categories() == []


def test_clear_all(learner):
    learner.observe("a", "b", "c")
    learner.observe("x", "y", "z")
    learner.clear_all()
    assert learner.all_preferences() == []


# -- persistence -----------------------------------------------------------


def test_persistence_across_reopen(tmp_path):
    db = tmp_path / "prefs.db"
    pl = PreferenceLearner(db)
    for _ in range(8):
        pl.observe("app", "music", "spotify")
    pl.close()

    pl2 = PreferenceLearner(db)
    assert pl2.predict_value("app", "music") == "spotify"
    pl2.close()


def test_creates_parent_dir(tmp_path):
    db = tmp_path / "nested" / "prefs.db"
    pl = PreferenceLearner(db)
    pl.observe("a", "b", "c")
    assert db.exists()
    pl.close()


def test_context_manager(tmp_path):
    db = tmp_path / "prefs.db"
    with PreferenceLearner(db) as pl:
        pl.observe("a", "b", "c")
    with PreferenceLearner(db) as pl2:
        assert pl2.get_preferences("a", "b")


def test_last_seen_updates(learner):
    learner.observe("a", "b", "c", timestamp=100.0)
    learner.observe("a", "b", "c", timestamp=200.0)
    pref = learner.get_preferences("a", "b")[0]
    assert pref.first_seen == 100.0
    assert pref.last_seen == 200.0


def test_learned_preference_to_dict():
    p = LearnedPreference("a", "b", "c", 2, 2.0, 0.5, 1.0, 2.0)
    d = p.to_dict()
    assert d["value"] == "c" and d["confidence"] == 0.5

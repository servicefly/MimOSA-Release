"""Tests for the Milestone-2 training-related config fields on VoiceSettings."""

from __future__ import annotations

from mimosa.utils.config import (
    DEFAULT_TRAINING_PREFERENCE,
    VALID_TRAINING_PREFERENCES,
    VoiceSettings,
)


def test_defaults():
    v = VoiceSettings()
    assert v.training_preference == DEFAULT_TRAINING_PREFERENCE == "mimosa"
    assert v.custom_wake_word_name == ""
    assert v.custom_model_path == ""
    assert v.has_custom_model() is False
    assert v.wants_training_later() is False


def test_valid_preferences_set():
    assert set(VALID_TRAINING_PREFERENCES) == {"mimosa", "now", "later"}


def test_wants_training_later():
    v = VoiceSettings(training_preference="later")
    assert v.wants_training_later() is True
    v2 = VoiceSettings(training_preference="now")
    assert v2.wants_training_later() is False


def test_has_custom_model_requires_existing_file(tmp_path):
    # A path that doesn't exist must not count as a usable model.
    v = VoiceSettings(custom_model_path=str(tmp_path / "missing.onnx"))
    assert v.has_custom_model() is False

    model = tmp_path / "model.onnx"
    model.write_bytes(b"FAKE")
    v2 = VoiceSettings(custom_model_path=str(model))
    assert v2.has_custom_model() is True

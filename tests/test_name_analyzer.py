"""Tests for the custom wake-word name analyzer (Milestone 2)."""

from __future__ import annotations

import pytest

from mimosa.training.name_analyzer import (
    CAP_CPU,
    CAP_GPU,
    DIFFICULTY_EASY,
    DIFFICULTY_HARD,
    DIFFICULTY_MODERATE,
    DIFFICULTY_VERY_HARD,
    NameAnalysis,
    analyze_wake_word,
)


def test_returns_name_analysis_instance():
    result = analyze_wake_word("Jarvis")
    assert isinstance(result, NameAnalysis)


def test_known_good_name_is_easy_and_trainable():
    result = analyze_wake_word("Jarvis")
    assert result.difficulty == DIFFICULTY_EASY
    assert result.is_trainable is True
    assert result.success_probability >= 0.9
    assert result.recommendation  # warm, non-empty guidance


def test_dataclass_supports_mapping_style_access():
    # The dialog reads fields via dict-style access for convenience.
    result = analyze_wake_word("Jarvis")
    assert result["difficulty"] == result.difficulty
    assert result["success_probability"] == result.success_probability


def test_empty_name_is_not_trainable_and_warns():
    result = analyze_wake_word("")
    assert result.is_trainable is False
    assert result.warnings  # should explain that letters are required


def test_whitespace_only_name_is_not_trainable():
    result = analyze_wake_word("   ")
    assert result.is_trainable is False


def test_very_short_name_is_difficult():
    result = analyze_wake_word("a")
    assert result.difficulty in (DIFFICULTY_HARD, DIFFICULTY_VERY_HARD)


def test_success_probability_within_bounds():
    for name in ("Jarvis", "a", "", "Computer", "Friday", "x"):
        result = analyze_wake_word(name)
        assert 0.0 <= result.success_probability <= 1.0


def test_difficulty_is_one_of_known_levels():
    levels = {
        DIFFICULTY_EASY,
        DIFFICULTY_MODERATE,
        DIFFICULTY_HARD,
        DIFFICULTY_VERY_HARD,
    }
    for name in ("Jarvis", "a", "", "Computer", "Friday"):
        assert analyze_wake_word(name).difficulty in levels


def test_estimated_times_present():
    result = analyze_wake_word("Jarvis")
    assert result.estimated_training_time_gpu
    assert result.estimated_training_time_cpu


def test_hardware_capability_does_not_break_analysis():
    gpu = analyze_wake_word("Jarvis", hardware_capability=CAP_GPU)
    cpu = analyze_wake_word("Jarvis", hardware_capability=CAP_CPU)
    assert gpu.is_trainable is True
    assert cpu.is_trainable is True


def test_never_raises_on_odd_input():
    # Defensive: the analyzer must never crash the wizard.
    for name in (None, 123, "🙂", "a" * 500):  # type: ignore[arg-type]
        try:
            analyze_wake_word(name)  # type: ignore[arg-type]
        except Exception as exc:  # pragma: no cover - failure path
            pytest.fail(f"analyze_wake_word raised for {name!r}: {exc}")

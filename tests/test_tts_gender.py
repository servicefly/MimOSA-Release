"""Tests for gender-aware voice selection (Milestone 2, requirement #9)."""

from __future__ import annotations

from mimosa.voice import tts


def test_female_voices_are_from_female_pool():
    voices = tts.voices_for_gender("female")
    assert voices
    assert voices[0] in tts.FEMALE_VOICES


def test_male_voices_are_from_male_pool():
    voices = tts.voices_for_gender("male")
    assert voices
    assert voices[0] in tts.MALE_VOICES


def test_gender_matching_is_case_insensitive():
    assert tts.voices_for_gender("FEMALE")[0] in tts.FEMALE_VOICES
    assert tts.voices_for_gender("  Male ")[0] in tts.MALE_VOICES


def test_none_and_unknown_fall_back_to_neutral_first():
    none_voices = tts.voices_for_gender(None)
    unknown_voices = tts.voices_for_gender("alien")
    assert none_voices[0] in tts.NEUTRAL_VOICES
    assert unknown_voices[0] in tts.NEUTRAL_VOICES


def test_voices_for_gender_returns_multiple_for_variety():
    # Training benefits from multiple voices per gender.
    assert len(tts.voices_for_gender("female")) >= 2
    assert len(tts.voices_for_gender("male")) >= 2


def test_voice_for_gender_returns_single_preferred():
    assert tts.voice_for_gender("female") == tts.voices_for_gender("female")[0]
    assert tts.voice_for_gender("male") == tts.voices_for_gender("male")[0]
    assert tts.voice_for_gender(None) == tts.DEFAULT_PIPER_VOICE


def test_create_tts_honours_gender():
    engine = tts.create_tts(gender="female")
    assert engine.voice in tts.FEMALE_VOICES


def test_create_tts_explicit_voice_wins_over_gender():
    engine = tts.create_tts(voice="en_US-ryan-medium", gender="female")
    assert engine.voice == "en_US-ryan-medium"


def test_create_tts_default_when_no_hints():
    engine = tts.create_tts()
    assert engine.voice  # falls back to engine default, never empty

"""Tests for onboarding config fields + persona profile injection (M3)."""

from __future__ import annotations

import pytest

from dataclasses import asdict

from mimosa.llm.persona import build_system_prompt
from mimosa.memory.profile_manager import UserProfile
from mimosa.utils.config import (
    DEFAULT_ONBOARDING_PREFERENCE,
    VALID_ONBOARDING_PREFERENCES,
    AppConfig,
    PersonalitySettings,
)


# -- config fields -------------------------------------------------------

def test_personality_defaults():
    p = PersonalitySettings()
    assert p.onboarding_complete is False
    assert p.onboarding_preference == DEFAULT_ONBOARDING_PREFERENCE
    assert DEFAULT_ONBOARDING_PREFERENCE in VALID_ONBOARDING_PREFERENCES


def test_personality_round_trip_through_appconfig():
    cfg = AppConfig()
    cfg.personality.onboarding_complete = True
    cfg.personality.onboarding_preference = "now"
    restored = AppConfig.from_dict(cfg.to_dict())
    assert restored.personality.onboarding_complete is True
    assert restored.personality.onboarding_preference == "now"


def test_personality_validate_normalises_bad_pref():
    p = PersonalitySettings(onboarding_preference="bogus").validate()
    assert p.onboarding_preference == DEFAULT_ONBOARDING_PREFERENCE


def test_personality_validate_accepts_all_valid():
    for pref in VALID_ONBOARDING_PREFERENCES:
        p = PersonalitySettings(onboarding_preference=pref).validate()
        assert p.onboarding_preference == pref


def test_personality_asdict_has_new_fields():
    data = asdict(PersonalitySettings())
    assert "onboarding_complete" in data
    assert "onboarding_preference" in data


# -- persona injection ---------------------------------------------------

def test_prompt_without_profile_has_no_clause():
    prompt = build_system_prompt("Answer questions.")
    assert "learned about the user" not in prompt.lower()


def test_prompt_with_profile_object():
    profile = UserProfile.from_dict(
        {"user_profile": {"name": "Alex", "interests": ["hiking"], "skills": ["react"]}}
    )
    prompt = build_system_prompt("Answer questions.", user_profile=profile)
    assert "Alex" in prompt
    assert "hiking" in prompt
    assert "react" in prompt


def test_prompt_with_profile_dict():
    prompt = build_system_prompt(
        "Answer questions.",
        user_profile={"user_profile": {"name": "Sam", "occupation": "designer"}},
    )
    assert "Sam" in prompt
    assert "designer" in prompt


def test_prompt_with_empty_profile_safe():
    # Empty profile should not raise and should not add a noisy clause.
    prompt = build_system_prompt("Answer.", user_profile=UserProfile())
    assert isinstance(prompt, str) and prompt

"""Tests for MimOSA's identity & tone module (Bug #11).

These verify the centralized persona always asserts MimOSA's correct identity,
applies the warm natural tone, and folds in optional personalisation (including
the new voice-style/gender preference). No network or heavy deps required.
"""

from __future__ import annotations

from mimosa.llm import persona
from mimosa.utils.config import PersonalitySettings


def test_identity_block_asserts_mimosa():
    text = persona.MIMOSA_IDENTITY
    assert "MimOSA" in text
    assert "Kubuntu" in text
    # Must explicitly disclaim being a generic vendor model.
    assert "NOT" in text
    assert "local" in text.lower()


def test_tone_block_is_friendly_and_not_robotic():
    text = persona.MIMOSA_TONE
    assert "This is MimOSA" in text  # it explicitly warns against this phrasing
    assert "warmly" in text.lower() or "natural" in text.lower()
    # Spoken-friendly: discourages markdown/bullets.
    assert "markdown" in text.lower()


def test_build_system_prompt_includes_identity_tone_and_task():
    prompt = persona.build_system_prompt("Answer the user's question.")
    assert "MimOSA" in prompt
    assert "Answer the user's question." in prompt
    # Tone included by default.
    assert "warmly" in prompt.lower() or "natural" in prompt.lower()


def test_build_system_prompt_can_omit_tone():
    prompt = persona.build_system_prompt("Do a thing.", include_tone=False)
    assert "Do a thing." in prompt
    assert persona.MIMOSA_TONE not in prompt


def test_personalization_assistant_name_and_user_name():
    p = PersonalitySettings(assistant_name="Echo", user_name="Sam").validate()
    prompt = persona.build_system_prompt("Help.", personality=p)
    assert "Echo" in prompt
    assert "Sam" in prompt


def test_personalization_verbosity_brief():
    p = PersonalitySettings(verbosity="brief").validate()
    prompt = persona.build_system_prompt("Help.", personality=p)
    assert "short" in prompt.lower()


def test_personalization_gender_female():
    p = PersonalitySettings(gender="female").validate()
    prompt = persona.build_system_prompt("Help.", personality=p)
    assert "female" in prompt.lower() or "feminine" in prompt.lower()


def test_personalization_gender_male():
    p = PersonalitySettings(gender="male").validate()
    prompt = persona.build_system_prompt("Help.", personality=p)
    assert "male" in prompt.lower() or "masculine" in prompt.lower()


def test_personalization_gender_neutral_adds_nothing():
    p = PersonalitySettings(gender="neutral").validate()
    prompt = persona.build_system_prompt("Help.", personality=p)
    assert "feminine" not in prompt.lower()
    assert "masculine" not in prompt.lower()


def test_build_system_prompt_without_personality():
    # None personality must not raise and still yields identity + task.
    prompt = persona.build_system_prompt("Task.", personality=None)
    assert "MimOSA" in prompt
    assert "Task." in prompt



def test_relationship_note_injected():
    note = "You two have grown close; be warm and casual like old friends."
    prompt = persona.build_system_prompt("Help.", relationship_note=note)
    assert "close" in prompt.lower()
    assert "MimOSA" in prompt


def test_relationship_note_none_is_ignored():
    prompt = persona.build_system_prompt("Help.", relationship_note=None)
    assert "MimOSA" in prompt
    assert "Help." in prompt


def test_relationship_note_empty_is_ignored():
    prompt = persona.build_system_prompt("Help.", relationship_note="   ")
    # No stray whitespace clause appended.
    assert "  " not in prompt.replace("\n", " ").strip() or "MimOSA" in prompt

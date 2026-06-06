"""Tests for personalised greetings (M8.4a wiring of PersonalitySettings)."""

from __future__ import annotations

from mimosa.skills.greeting_skill import GreetingSkill, _FALLBACK_REPLIES
from mimosa.utils.config import PersonalitySettings
from mimosa.core.intent_router import IntentRouter


def test_fallback_greets_by_name_when_known():
    p = PersonalitySettings(user_name="Sam", greet_by_name=True).validate()
    skill = GreetingSkill(llm_provider=None, personality=p)
    result = skill.handle("hello")
    assert "Sam" in result.text
    assert result.metadata["source"] == "fallback"


def test_fallback_generic_when_no_name():
    skill = GreetingSkill(llm_provider=None, personality=PersonalitySettings())
    result = skill.handle("hi")
    assert result.text in _FALLBACK_REPLIES


def test_fallback_respects_greet_by_name_off():
    p = PersonalitySettings(user_name="Sam", greet_by_name=False).validate()
    skill = GreetingSkill(llm_provider=None, personality=p)
    result = skill.handle("hello")
    assert result.text in _FALLBACK_REPLIES


def test_no_personality_uses_default_prompt():
    skill = GreetingSkill(llm_provider=None)
    # _system_prompt falls back to the canonical prompt.
    from mimosa.skills.greeting_skill import GREETING_SYSTEM_PROMPT

    assert skill._system_prompt() == GREETING_SYSTEM_PROMPT


def test_system_prompt_includes_assistant_name():
    p = PersonalitySettings(assistant_name="Ada", user_name="Lee").validate()
    skill = GreetingSkill(llm_provider=None, personality=p)
    prompt = skill._system_prompt()
    assert "Ada" in prompt
    assert "Lee" in prompt


def test_router_injects_personality_into_greeting_skill():
    p = PersonalitySettings(user_name="Robin", greet_by_name=True).validate()
    router = IntentRouter(llm_provider=None, personality=p)
    greeting = router._by_intent.get("greeting")
    assert greeting is not None
    assert greeting.personality is p

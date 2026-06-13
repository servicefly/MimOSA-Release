"""Tests that the learned profile is injected into LLM-backed skills (M3)."""

from __future__ import annotations

from mimosa.core.intent_router import IntentRouter
from mimosa.memory.profile_manager import UserProfile
from mimosa.skills.greeting_skill import GreetingSkill
from mimosa.skills.question_skill import QuestionSkill


def _profile():
    return UserProfile.from_dict(
        {"user_profile": {"name": "Alex", "interests": ["hiking"], "skills": ["react"]}}
    )


def test_question_skill_prompt_includes_profile():
    skill = QuestionSkill(user_profile=_profile())
    prompt = skill._system_prompt()
    assert "Alex" in prompt
    assert "hiking" in prompt


def test_greeting_skill_prompt_includes_profile():
    skill = GreetingSkill(user_profile=_profile())
    prompt = skill._system_prompt()
    assert "Alex" in prompt


def test_question_skill_no_profile_uses_default():
    skill = QuestionSkill()
    prompt = skill._system_prompt()
    assert isinstance(prompt, str) and prompt
    assert "learned about the user" not in prompt.lower()


def test_router_injects_profile_into_skills():
    profile = _profile()
    router = IntentRouter(user_profile=profile)
    # Default skills include Question + Greeting which accept a profile.
    question = router._by_intent.get("question")
    assert question is not None
    assert getattr(question, "user_profile", None) is profile


def test_router_set_user_profile_updates_live_skills():
    router = IntentRouter()
    profile = _profile()
    router.set_user_profile(profile)
    question = router._by_intent.get("question")
    assert getattr(question, "user_profile", None) is profile
    greeting = router._by_intent.get("greeting")
    assert getattr(greeting, "user_profile", None) is profile

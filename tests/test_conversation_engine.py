"""Tests for the step-driven onboarding conversation engine (M3). Hermetic."""

from __future__ import annotations

import pytest

from mimosa.memory.profile_manager import ProfileManager
from mimosa.memory.vector_store import MemoryVectorStore
from mimosa.onboarding.conversation_engine import (
    ConversationState,
    OnboardingConversation,
    PromptKind,
)
from mimosa.onboarding.fact_extractor import FactExtractor


@pytest.fixture()
def parts():
    vs = MemoryVectorStore(None, use_chroma=False)
    pm = ProfileManager(vector_store=vs, autosave=False)
    fe = FactExtractor(llm=None)
    yield fe, pm, vs
    vs.close()


def _convo(parts, **kw):
    fe, pm, vs = parts
    return OnboardingConversation(
        fact_extractor=fe, profile_manager=pm, vector_store=vs, **kw
    )


def test_starts_with_topic_intro(parts):
    convo = _convo(parts)
    prompt = convo.start()
    assert prompt.kind == PromptKind.TOPIC_INTRO
    assert prompt.topic_index == 1
    assert prompt.total_topics == 7
    assert convo.state == ConversationState.ASKING


def test_deep_answer_advances_without_followup(parts):
    convo = _convo(parts, max_follow_ups=1)
    convo.start()
    result = convo.submit_response(
        "My name is Alexander. I have been called that my whole life. "
        "Friends sometimes shorten it to Alex though, which I also like."
    )
    assert result["depth"] == "deep"
    assert result["encouragement"] is None
    assert result["advanced"] is True


def test_shallow_answer_triggers_followup(parts):
    convo = _convo(parts, max_follow_ups=1)
    convo.start()
    result = convo.submit_response("Alex")  # shallow
    assert result["depth"] == "shallow"
    assert result["encouragement"]
    assert result["advanced"] is False
    assert result["next_prompt"].kind == PromptKind.FOLLOW_UP


def test_followup_capped(parts):
    convo = _convo(parts, max_follow_ups=1)
    convo.start()
    convo.submit_response("Alex")  # triggers follow-up
    result = convo.submit_response("Sam")  # still shallow but cap reached
    assert result["advanced"] is True


def test_full_run_completes_and_builds_profile(parts):
    fe, pm, vs = parts
    convo = _convo(parts, max_follow_ups=0)
    convo.start()
    answers = [
        "My name is Alex",
        "I enjoy building things and meeting people",
        "I work as a software engineer at a startup",
        "python, react and docker",
        "I love hiking and cooking on weekends",
        "I could talk about coffee for hours honestly",
        "I am a night owl for sure",
        "Just the quick version please",
        "I mostly use vscode and a terminal",
        "Help me manage my email and calendar",
        "Short and to the point works best",
        "Wait until I ask before suggesting",
        "I'm learning Spanish this year",
        "My partner Jamie is important to me",
    ]
    steps = 0
    i = 0
    while not convo.is_complete and steps < 80:
        convo.submit_response(answers[i % len(answers)])
        i += 1
        steps += 1
    assert convo.is_complete
    assert convo.progress == 1.0
    assert pm.profile.name == "Alex"
    assert pm.profile.known_fact_count() > 0
    assert len(convo.transcript) >= 7


def test_skip_topic_advances(parts):
    convo = _convo(parts)
    convo.start()
    assert convo.topic_index == 1
    convo.skip_topic()
    assert convo.topic_index == 2


def test_completion_prompt_after_all_topics(parts):
    convo = _convo(parts)
    convo.start()
    while not convo.is_complete:
        convo.skip_topic()
    prompt = convo.current_prompt()
    assert prompt.kind == PromptKind.COMPLETE
    assert prompt.is_complete


def test_state_round_trip(parts):
    convo = _convo(parts)
    convo.start()
    convo.submit_response("My name is Alex")
    state = convo.to_state()

    convo2 = _convo(parts)
    convo2.load_state(state)
    assert convo2.topic_index == convo.topic_index
    assert len(convo2.transcript) == len(convo.transcript)


def test_engine_without_collaborators_never_raises():
    convo = OnboardingConversation()  # no fact extractor / profile / store
    convo.start()
    result = convo.submit_response("Alex")
    assert "next_prompt" in result

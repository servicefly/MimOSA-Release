"""Conversational onboarding subsystem for MimOSA (M3).

This package implements MimOSA's "get to know you" experience: a warm,
natural, friend-like conversation (rather than a dry questionnaire) that
gradually learns about the user and builds a structured profile used to
personalise every future interaction.

Modules
-------
* :mod:`mimosa.onboarding.question_bank` — the topics and questions that drive
  the conversation, with adaptive follow-ups.
* :mod:`mimosa.onboarding.response_analyzer` — gauges how much a user shared
  (shallow / medium / deep) and produces warm encouragement for thin answers.
* :mod:`mimosa.onboarding.fact_extractor` — turns free-text answers into
  structured profile facts (LLM-assisted, heuristic fallback).
* :mod:`mimosa.onboarding.conversation_engine` — the step-driven state machine
  that walks through topics, asks follow-ups, and records facts.
* :mod:`mimosa.onboarding.onboarding_manager` — high-level orchestration with
  pause/resume persistence and completion summaries.
"""

from __future__ import annotations

from mimosa.onboarding.question_bank import (
    QUESTION_BANK,
    Question,
    Topic,
    all_topics,
    get_topic,
    total_topics,
)
from mimosa.onboarding.response_analyzer import (
    ResponseDepth,
    analyze_response_depth,
    encouragement_for,
    is_vague,
)
from mimosa.onboarding.fact_extractor import FactExtractor, ExtractedFact
from mimosa.onboarding.conversation_engine import (
    ConversationState,
    OnboardingConversation,
    OnboardingTurn,
)
from mimosa.onboarding.onboarding_manager import OnboardingManager

__all__ = [
    "QUESTION_BANK",
    "Topic",
    "Question",
    "all_topics",
    "get_topic",
    "total_topics",
    "ResponseDepth",
    "analyze_response_depth",
    "encouragement_for",
    "is_vague",
    "FactExtractor",
    "ExtractedFact",
    "OnboardingConversation",
    "ConversationState",
    "OnboardingTurn",
    "OnboardingManager",
]

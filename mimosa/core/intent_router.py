"""Intent classification and routing for MimOSA.

The :class:`IntentRouter` is the brain that sits between speech-to-text and the
skills. Given the user's transcribed text it:

1. **Classifies** the intent into one of the supported categories
   (``time``, ``weather``, ``calculator``, ``question``, ``greeting``) or
   ``unknown``.
2. **Routes** the text to the matching
   :class:`~mimosa.skills.base_skill.BaseSkill`.
3. Returns a unified :class:`~mimosa.skills.base_skill.SkillResult`.

Hybrid, cost-aware classification
---------------------------------
LLM calls cost latency and money, so the router classifies in two tiers:

* **Tier 1 -- fast local heuristics.** Strong regex/keyword patterns catch the
  common, unambiguous cases (time, math, weather, greetings) instantly with
  high confidence and **zero LLM calls**. This directly satisfies the
  "minimize LLM calls / use local logic for time & calc" requirement.
* **Tier 2 -- LLM classification.** Only when local heuristics are not
  confident does the router ask the LLM to label the intent. The LLM returns a
  category and a confidence score.

If the resulting confidence is below ``INTENT_CONFIDENCE_THRESHOLD`` the router
falls back to the general :class:`~mimosa.skills.question_skill.QuestionSkill`
(treating it as a general query) or, when configured, an explicit "unknown"
clarification response.

Extensibility
-------------
Skills are registered in a list and matched by their declared ``intents``.
Adding a new skill = construct it and call :meth:`register_skill` (or pass it in
the constructor); no changes to routing logic required.

Privacy
-------
Only text is ever sent to the LLM. The router itself holds no audio. When the
Privacy Guard forces local-only mode, the injected provider is simply a local
one -- the router is agnostic.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Dict, List, Optional

from mimosa.llm.base_provider import LLMError, Message, Role
from mimosa.skills.base_skill import BaseSkill, SkillResult
from mimosa.skills.calculator_skill import CalculatorSkill
from mimosa.skills.greeting_skill import GreetingSkill
from mimosa.skills.question_skill import QuestionSkill
from mimosa.skills.time_skill import TimeSkill
from mimosa.skills.weather_skill import WeatherSkill

logger = logging.getLogger("mimosa.core.intent_router")

#: Canonical intent labels the router understands.
INTENT_TIME = "time"
INTENT_WEATHER = "weather"
INTENT_CALCULATOR = "calculator"
INTENT_QUESTION = "question"
INTENT_GREETING = "greeting"
INTENT_UNKNOWN = "unknown"

SUPPORTED_INTENTS = (
    INTENT_TIME,
    INTENT_WEATHER,
    INTENT_CALCULATOR,
    INTENT_QUESTION,
    INTENT_GREETING,
)

DEFAULT_CONFIDENCE_THRESHOLD = 0.7


@dataclass
class IntentClassification:
    """Result of classifying an utterance.

    Attributes:
        intent: One of :data:`SUPPORTED_INTENTS` or :data:`INTENT_UNKNOWN`.
        confidence: Confidence in ``[0, 1]``.
        source: How it was classified -- ``"heuristic"`` or ``"llm"``.
    """

    intent: str
    confidence: float
    source: str = "heuristic"


# --- Tier 1 heuristic patterns ------------------------------------------------
# Each pattern, if matched, yields a high-confidence local classification with
# NO LLM call. Order matters: more specific intents are checked first.

_TIME_PATTERNS = [
    r"\bwhat('?s| is) the time\b",
    r"\bwhat time is it\b",
    r"\bwhat('?s| is) (today'?s )?date\b",
    r"\bwhat day is it\b",
    r"\bwhat('?s| is) the day\b",
    r"\bcurrent (time|date)\b",
    r"\btell me the (time|date)\b",
]

_CALC_PATTERNS = [
    r"\b\d+(\.\d+)?\s*[\+\-\*/x×÷\^]\s*\d+",  # 3 + 4, 10 / 2, 3 x 4
    r"\b(calculate|compute|what('?s| is))\b.*\b\d+\b.*\b(plus|minus|times|multiplied by|divided by|over|to the power of|squared|cubed|mod)\b",
    r"\b\d+\s+(plus|minus|times|multiplied by|divided by|over|to the power of|mod)\s+\d+",
    r"\bsquare root of\b",
]

_WEATHER_PATTERNS = [
    r"\bweather\b",
    r"\b(how('?s| is) it|what('?s| is) it like) outside\b",
    r"\b(is it|will it) (rain|snow|sunny|cloudy|hot|cold)\b",
    r"\btemperature\b",
    r"\bforecast\b",
]

_GREETING_PATTERNS = [
    r"^\s*(hello|hi|hey|yo|howdy|greetings)\b",
    r"\bgood (morning|afternoon|evening|day)\b",
    r"\bhow are you\b",
    r"\bhow('?s| is) it going\b",
    r"\bwhat('?s| is) up\b",
    r"\bnice to meet you\b",
    r"^\s*(thanks|thank you|thank u)\b",
]


def _matches_any(text: str, patterns: List[str]) -> bool:
    return any(re.search(p, text) for p in patterns)


class IntentRouter:
    """Classify intents and dispatch them to the right skill.

    Args:
        llm_provider: Shared LLM provider for LLM-backed skills and Tier-2
            classification. May be ``None`` for fully local operation (LLM
            skills then return graceful "not available" messages, and Tier-2
            classification is skipped).
        confidence_threshold: Minimum confidence to accept a classification;
            below this the router falls back to the question skill. Defaults to
            the ``INTENT_CONFIDENCE_THRESHOLD`` env var, then
            :data:`DEFAULT_CONFIDENCE_THRESHOLD`.
        skills: Optional explicit list of skills. If omitted, the default M1.3
            skill set is constructed.
    """

    def __init__(
        self,
        llm_provider=None,
        confidence_threshold: Optional[float] = None,
        skills: Optional[List[BaseSkill]] = None,
    ) -> None:
        self.llm = llm_provider
        self.confidence_threshold = (
            confidence_threshold
            if confidence_threshold is not None
            else float(os.getenv("INTENT_CONFIDENCE_THRESHOLD", DEFAULT_CONFIDENCE_THRESHOLD))
        )

        # Build default skill set if none provided.
        if skills is None:
            skills = [
                TimeSkill(),
                CalculatorSkill(),
                WeatherSkill(llm_provider=llm_provider),
                GreetingSkill(llm_provider=llm_provider),
                QuestionSkill(llm_provider=llm_provider),
            ]
        self._skills: List[BaseSkill] = []
        self._by_intent: Dict[str, BaseSkill] = {}
        for skill in skills:
            self.register_skill(skill)

    # -- registration ------------------------------------------------------

    def register_skill(self, skill: BaseSkill) -> None:
        """Register a skill, indexing it by each intent label it declares."""
        self._skills.append(skill)
        for intent in skill.intents:
            self._by_intent[intent] = skill
        logger.debug("Registered skill %r for intents %s", skill.name, skill.intents)

    @property
    def skills(self) -> List[BaseSkill]:
        """The registered skills."""
        return list(self._skills)

    # -- classification ----------------------------------------------------

    def classify(self, text: str) -> IntentClassification:
        """Classify ``text`` into an intent, heuristics first then LLM.

        Returns:
            An :class:`IntentClassification`. Never raises -- LLM failures fall
            back to ``unknown`` with low confidence.
        """
        lowered = (text or "").strip().lower()
        if not lowered:
            return IntentClassification(INTENT_UNKNOWN, 0.0, source="heuristic")

        # Tier 1: fast, local, zero-cost heuristics (most specific first).
        if _matches_any(lowered, _TIME_PATTERNS):
            return IntentClassification(INTENT_TIME, 0.97, source="heuristic")
        if _matches_any(lowered, _CALC_PATTERNS):
            return IntentClassification(INTENT_CALCULATOR, 0.95, source="heuristic")
        if _matches_any(lowered, _WEATHER_PATTERNS):
            return IntentClassification(INTENT_WEATHER, 0.9, source="heuristic")
        if _matches_any(lowered, _GREETING_PATTERNS):
            return IntentClassification(INTENT_GREETING, 0.9, source="heuristic")

        # Tier 1b: clearly question-shaped utterances route straight to the
        # question skill. This is a deliberate cost optimization -- a question
        # needs an LLM call to *answer* anyway, so spending a second LLM call
        # just to *classify* it would be wasteful. We accept these locally with
        # solid confidence.
        if lowered.endswith("?") or re.match(
            r"^\s*(who|what|when|where|why|how|which|whose|whom|is|are|was|were|"
            r"can|could|do|does|did|will|would|should|tell me about|explain|"
            r"define)\b",
            lowered,
        ):
            return IntentClassification(INTENT_QUESTION, 0.85, source="heuristic")

        # Tier 2: ask the LLM only when heuristics are inconclusive (e.g. a
        # statement that isn't obviously a question or a command).
        if self.llm is not None:
            llm_result = self._classify_with_llm(text)
            if llm_result is not None:
                return llm_result

        return IntentClassification(INTENT_UNKNOWN, 0.3, source="heuristic")

    def _classify_with_llm(self, text: str) -> Optional[IntentClassification]:
        """Use the LLM to classify intent; returns ``None`` on failure."""
        system = (
            "You are an intent classifier for a voice assistant. Classify the "
            "user's message into exactly one of these intents: "
            f"{', '.join(SUPPORTED_INTENTS)}. "
            "Use 'question' for general knowledge or open-ended queries. "
            "Respond with ONLY a compact JSON object of the form "
            '{"intent": "<intent>", "confidence": <0..1>} and nothing else.'
        )
        try:
            response = self.llm.chat(
                [
                    Message(role=Role.SYSTEM, content=system),
                    Message(role=Role.USER, content=text),
                ],
                temperature=0.0,
                max_tokens=40,
            )
        except LLMError as exc:
            logger.warning("LLM intent classification failed: %s", exc)
            return None

        intent, confidence = self._parse_classification(response.content)
        if intent is None:
            return None
        return IntentClassification(intent, confidence, source="llm")

    @staticmethod
    def _parse_classification(content: str):
        """Extract (intent, confidence) from an LLM reply; tolerant of noise."""
        if not content:
            return None, 0.0
        match = re.search(r"\{.*\}", content, re.DOTALL)
        raw = match.group(0) if match else content
        try:
            data = json.loads(raw)
            intent = str(data.get("intent", "")).strip().lower()
            confidence = float(data.get("confidence", 0.0))
        except (ValueError, TypeError):
            # Last resort: look for a bare intent keyword in the text.
            lowered = content.lower()
            for cand in SUPPORTED_INTENTS:
                if cand in lowered:
                    return cand, 0.6
            return None, 0.0
        if intent not in SUPPORTED_INTENTS and intent != INTENT_UNKNOWN:
            intent = INTENT_QUESTION  # default unrecognized labels to question
        return intent, max(0.0, min(1.0, confidence))

    # -- routing -----------------------------------------------------------

    def route(self, text: str, context: Optional[List] = None) -> SkillResult:
        """Classify ``text`` and dispatch to the appropriate skill.

        Args:
            text: The recognized user utterance.
            context: Optional conversation history (list of
                :class:`~mimosa.llm.base_provider.Message`) for LLM skills.

        Returns:
            A :class:`SkillResult`. Always returns a result; never raises.
        """
        if not (text or "").strip():
            return SkillResult(
                text="I didn't catch that. Could you say it again?",
                success=False,
                skill="router",
                metadata={"intent": INTENT_UNKNOWN, "confidence": 0.0},
            )

        classification = self.classify(text)
        intent = classification.intent
        confidence = classification.confidence
        logger.info(
            "Intent=%s confidence=%.2f source=%s text=%r",
            intent, confidence, classification.source, text,
        )

        # Low confidence or unknown -> treat as a general question if possible.
        if intent == INTENT_UNKNOWN or confidence < self.confidence_threshold:
            logger.info(
                "Confidence %.2f below threshold %.2f (intent=%s); falling back.",
                confidence, self.confidence_threshold, intent,
            )
            intent = INTENT_QUESTION

        skill = self._by_intent.get(intent)
        if skill is None:
            # No skill registered for this intent -> fall back to question skill
            # if present, else a clear message.
            skill = self._by_intent.get(INTENT_QUESTION)
            if skill is None:
                return SkillResult(
                    text="I'm not sure how to help with that yet.",
                    success=False,
                    skill="router",
                    metadata={"intent": intent, "confidence": confidence},
                )

        result = skill.run(text, context=context)
        # Annotate with routing metadata for logging/tests.
        result.metadata.setdefault("intent", intent)
        result.metadata.setdefault("confidence", confidence)
        result.metadata.setdefault("classification_source", classification.source)
        return result

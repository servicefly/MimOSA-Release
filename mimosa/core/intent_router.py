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
from mimosa.skills.application import ApplicationSkill
from mimosa.skills.base_skill import BaseSkill, SkillResult
from mimosa.skills.calculator_skill import CalculatorSkill
from mimosa.skills.custom_skill import CustomSkill
from mimosa.skills.file_ops import FileOperationsSkill
from mimosa.skills.greeting_skill import GreetingSkill
from mimosa.skills.question_skill import QuestionSkill
from mimosa.skills.system_control import SystemControlSkill
from mimosa.skills.system_info import SystemInfoSkill
from mimosa.skills.time_skill import TimeSkill
from mimosa.skills.weather_skill import WeatherSkill

logger = logging.getLogger("mimosa.core.intent_router")

#: Canonical intent labels the router understands.
INTENT_TIME = "time"
INTENT_WEATHER = "weather"
INTENT_CALCULATOR = "calculator"
INTENT_QUESTION = "question"
INTENT_GREETING = "greeting"
INTENT_FILE = "file_ops"
INTENT_APPLICATION = "application"
INTENT_SYSTEM = "system_control"
INTENT_SYSTEM_INFO = "system_info"
INTENT_UNKNOWN = "unknown"

SUPPORTED_INTENTS = (
    INTENT_TIME,
    INTENT_WEATHER,
    INTENT_CALCULATOR,
    INTENT_FILE,
    INTENT_APPLICATION,
    INTENT_SYSTEM,
    INTENT_SYSTEM_INFO,
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

# File-operation patterns (M2.1). These catch explicit file/folder commands so
# they are handled locally by the FileOperationsSkill with zero LLM calls. They
# run before the question-shape heuristic so "where is my budget file" routes to
# files rather than to the question skill.
_FILE_PATTERNS = [
    # create/make a (new) file or folder/directory
    r"\b(create|make|new)\s+(a\s+|an\s+|the\s+)?(new\s+)?(file|folder|directory|dir)\b",
    r"\b(create|make)\b.+\b(called|named|titled)\b",
    # any file verb that explicitly mentions a file/folder noun
    r"\b(open|delete|remove|trash|erase|rename|move|relocate|find|search|locate|list|show)\b"
    r"[^?]*\b(file|folder|directory)\b",
    # a file verb acting on something with a file extension (e.g. notes.txt)
    r"\b(open|delete|remove|trash|erase|rename|move|relocate|find|search|locate)\b"
    r"[^?]*\.[A-Za-z0-9]{1,6}\b",
    # find/search/list a category of files
    r"\b(find|search( for)?|locate|look for|where('?s| is)|list|show me)\b[^?]*\b"
    r"(documents?|images?|photos?|pictures?|videos?|movies?|songs?|music|"
    r"spreadsheets?|presentations?|pdfs?|downloads?)\b",
    # move/rename X to Y
    r"\b(move|rename|relocate)\b.+\bto\b",
]


# System-control patterns (M2.2). These catch volume/brightness/Wi-Fi/battery
# commands so they are handled locally by the SystemControlSkill with zero LLM
# calls. Checked before the application patterns so "turn the volume up" is not
# mistaken for "turn ... up <app>".
_SYSTEM_PATTERNS = [
    # volume / audio
    r"\b(volume|sound|audio)\b",
    r"\b(mute|unmute|silence)\b",
    r"\b(louder|quieter|turn it (up|down))\b",
    # brightness
    r"\bbright(ness)?\b",
    r"\b(dim|brighten)\b.*\b(screen|display)\b",
    r"\b(screen|display)\b.*\bbright",
    # wifi / wireless
    r"\b(wi-?fi|wireless)\b",
    # battery / power
    r"\b(battery|charge level|how much (battery|charge|power))\b",
]

# Application launch/control patterns (M2.2). These catch app commands so they
# route locally to the ApplicationSkill. They run after file patterns (so
# "open my notes file" stays a file op) and after system patterns.
_APP_PATTERNS = [
    # launch verbs
    r"\b(open|launch|start|run|fire up|bring up)\b",
    # close/kill verbs paired with an app-ish object
    r"\b(close|quit|kill|terminate)\b",
    # is X running / open
    r"\b(is|are)\b.+\b(running|open|active)\b",
    # list/what apps/browsers/editors
    r"\b(list|show|what|which)\b.*\b(apps?|applications?|programs?|browsers?|"
    r"editors?|games?)\b",
]


# System-information patterns (M2.3). These catch read-only questions about
# the host -- the OS/distro, desktop environment, display server (Wayland/X11),
# KDE Plasma version, CPU/RAM/GPU/displays, the audio backend, and tuning
# recommendations -- so the SystemInfoSkill answers them locally with zero LLM
# calls. They are checked *before* the system-control patterns (so "what audio
# backend am I using" is treated as a query, not a volume command) and before
# the Tier-1b question-shape heuristic (so "what desktop am I using?" doesn't
# fall through to the LLM question skill).
_SYSTEM_INFO_PATTERNS = [
    # desktop environment / window manager
    r"\b(what|which)\b.*\bdesktop( environment)?\b",
    r"\bdesktop environment\b",
    r"\bwindow manager\b",
    # display server
    r"\b(display server|wayland|x11|xorg)\b",
    # KDE Plasma
    r"\b(plasma version|kde version|version of plasma|version of kde)\b",
    r"\bplasma\b.*\bversion\b",
    # distro / OS
    r"\bwhat\b.*\b(operating system|distro|distribution)\b",
    r"\b(what|which)\b.*\bversion of (kubuntu|ubuntu|linux|the os)\b",
    r"\b(which|what)\s+(linux|distro|distribution)\b",
    # audio backend (info, not control)
    r"\b(audio|sound)\s+(backend|server|system|stack)\b",
    r"\b(what|which)\b.*\b(audio|sound)\b.*\b(am i|using|do i have)\b",
    # microphone presence
    r"\b(do i have|is there)\b.*\b(microphone|mic)\b",
    r"\bmicrophone\b",
    # CPU / RAM / GPU / displays as queries
    r"\bhow (much|many)\b.*\b(ram|memory|cpu|cores?|threads?|gpus?|graphics)\b",
    r"\b(what|which)\b.*\b(cpu|processor|gpu|graphics card|graphics adapter|video card)\b",
    r"\b(what|how much)\b.*\b(ram|memory)\b",
    # specs / general system info
    r"\b(system|hardware)\s+(specs?|specifications?|info(rmation)?)\b",
    r"\b(my|this)\s+(system|hardware)\s+(specs?|specifications?)\b",
    r"\bshow\b.*\b(system|hardware)\b.*\b(specs?|info)\b",
    r"\babout (this|my) (system|computer|machine|pc|laptop)\b",
    # tuning recommendations
    r"\b(recommend|optimi[sz]e|tune|tuning)\b.*\b(settings|machine|system|hardware|config(uration)?)\b",
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
        custom_skills: Optional[List[CustomSkill]] = None,
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
                FileOperationsSkill(),
                ApplicationSkill(),
                SystemControlSkill(),
                SystemInfoSkill(),
                GreetingSkill(llm_provider=llm_provider),
                QuestionSkill(llm_provider=llm_provider),
            ]
        self._skills: List[BaseSkill] = []
        self._by_intent: Dict[str, BaseSkill] = {}
        #: User-defined custom skills (M4.1), matched before the LLM fallback.
        self._custom_skills: List[CustomSkill] = []
        for skill in skills:
            self.register_skill(skill)
        if custom_skills:
            self.set_custom_skills(custom_skills)

    # -- registration ------------------------------------------------------

    def register_skill(self, skill: BaseSkill) -> None:
        """Register a skill, indexing it by each intent label it declares."""
        self._skills.append(skill)
        for intent in skill.intents:
            self._by_intent[intent] = skill
        if isinstance(skill, CustomSkill) and skill not in self._custom_skills:
            self._custom_skills.append(skill)
        logger.debug("Registered skill %r for intents %s", skill.name, skill.intents)

    def set_custom_skills(self, custom_skills: List[CustomSkill]) -> None:
        """Replace the active set of custom skills (M4.1).

        Removes any previously-registered custom skills (from both the skill
        list and the intent index) and registers the new set. Lets the app
        live-refresh custom skills when the user edits them in Settings without
        rebuilding the whole router.
        """
        # Drop the old custom skills.
        old = set(id(s) for s in self._custom_skills)
        self._skills = [s for s in self._skills if id(s) not in old]
        for cs in self._custom_skills:
            for intent in cs.intents:
                if self._by_intent.get(intent) is cs:
                    del self._by_intent[intent]
        self._custom_skills = []
        for cs in custom_skills or []:
            self.register_skill(cs)

    @property
    def skills(self) -> List[BaseSkill]:
        """The registered skills."""
        return list(self._skills)

    @property
    def custom_skills(self) -> List[CustomSkill]:
        """The registered user-defined custom skills (M4.1)."""
        return list(self._custom_skills)

    def _match_custom_skill(self, text: str) -> Optional[CustomSkill]:
        """Return the first enabled custom skill whose triggers match ``text``."""
        for cs in self._custom_skills:
            try:
                if cs.matches(text):
                    return cs
            except Exception:  # pragma: no cover - a bad custom skill is non-fatal
                logger.exception("Custom skill %r match failed", cs.name)
        return None

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
        if _matches_any(lowered, _FILE_PATTERNS):
            return IntentClassification(INTENT_FILE, 0.92, source="heuristic")
        if _matches_any(lowered, _SYSTEM_INFO_PATTERNS):
            return IntentClassification(INTENT_SYSTEM_INFO, 0.92, source="heuristic")
        if _matches_any(lowered, _SYSTEM_PATTERNS):
            return IntentClassification(INTENT_SYSTEM, 0.93, source="heuristic")
        if _matches_any(lowered, _APP_PATTERNS):
            return IntentClassification(INTENT_APPLICATION, 0.9, source="heuristic")
        if _matches_any(lowered, _GREETING_PATTERNS):
            return IntentClassification(INTENT_GREETING, 0.9, source="heuristic")

        # Tier 1c: user-defined custom skills (M4.1). Checked after the built-in
        # heuristics (so core commands keep priority) but before the question /
        # LLM fallback, so a matching custom command is answered locally with
        # zero classification cost.
        custom = self._match_custom_skill(text)
        if custom is not None:
            return IntentClassification(custom.intents[0], 0.9, source="custom")

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

        # If a skill is mid-confirmation (e.g. a queued file delete awaiting a
        # yes/no), route this utterance straight back to it so a bare "yes"/"no"
        # resolves the prompt instead of being re-classified as a new intent.
        for skill in self._skills:
            if skill.has_pending_confirmation():
                logger.info("Routing to %r to resolve pending confirmation.", skill.name)
                result = skill.run(text, context=context)
                result.metadata.setdefault("intent", skill.intents[0] if skill.intents else skill.name)
                result.metadata.setdefault("confidence", 1.0)
                result.metadata.setdefault("classification_source", "pending_confirmation")
                return result

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

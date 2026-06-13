"""Step-driven onboarding conversation engine for MimOSA (M3).

This is the heart of the "get to know you" experience.  Rather than an async
loop (which is awkward to drive from a GTK UI and to unit-test), the engine is
a small **state machine** that the UI/manager pumps one turn at a time:

    convo = OnboardingConversation(fact_extractor=..., vector_store=...,
                                   profile_manager=...)
    prompt = convo.current_prompt()      # what MimOSA should say/ask now
    ...                                  # collect the user's answer
    result = convo.submit_response(text) # record + analyse + maybe follow up
    if convo.is_complete: ...

The engine:

* walks the seven topics from the question bank in order;
* asks the topic's primary question, then *adapts* — if an answer is
  shallow/vague it offers a warm encouragement and (optionally) a follow-up
  before moving on;
* extracts facts from every substantive answer and records them into the
  profile + vector store (all best-effort, never fatal);
* tracks progress (topic N of 7) and keeps a full transcript.

Everything heavy (LLM, embeddings, persistence) is injected and optional, so
the engine runs fully hermetically in tests.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from mimosa.onboarding.question_bank import (
    QUESTION_BANK,
    Question,
    Topic,
    total_topics,
)
from mimosa.onboarding.response_analyzer import (
    ResponseDepth,
    analyze_response_depth,
    encouragement_for,
    is_vague,
)

__all__ = [
    "OnboardingConversation",
    "ConversationState",
    "OnboardingTurn",
    "PromptKind",
]


class ConversationState(str, Enum):
    NOT_STARTED = "not_started"
    ASKING = "asking"
    COMPLETE = "complete"


class PromptKind(str, Enum):
    TOPIC_INTRO = "topic_intro"
    QUESTION = "question"
    FOLLOW_UP = "follow_up"
    ENCOURAGEMENT = "encouragement"
    COMPLETE = "complete"


@dataclass
class OnboardingTurn:
    """A single recorded exchange in the onboarding transcript."""

    topic_id: str
    question_id: str
    prompt: str
    response: str = ""
    depth: Optional[str] = None
    facts: List[Dict[str, Any]] = field(default_factory=list)
    is_follow_up: bool = False


@dataclass
class Prompt:
    """What MimOSA should say next."""

    text: str
    kind: PromptKind
    topic_id: str = ""
    question_id: str = ""
    topic_index: int = 0
    total_topics: int = 0

    @property
    def is_complete(self) -> bool:
        return self.kind == PromptKind.COMPLETE


class OnboardingConversation:
    """Step-driven state machine that runs the onboarding chat."""

    #: Max adaptive follow-ups asked per question before moving on.
    MAX_FOLLOW_UPS = 1

    def __init__(
        self,
        *,
        fact_extractor: Any = None,
        profile_manager: Any = None,
        vector_store: Any = None,
        topics=QUESTION_BANK,
        max_follow_ups: Optional[int] = None,
    ):
        self.topics = tuple(topics)
        self.fact_extractor = fact_extractor
        self.profile_manager = profile_manager
        self.vector_store = vector_store
        if max_follow_ups is not None:
            self.MAX_FOLLOW_UPS = max_follow_ups

        self.state = ConversationState.NOT_STARTED
        self._topic_idx = 0
        self._question_idx = 0
        self._follow_up_count = 0
        self._pending_intro = True
        self.transcript: List[OnboardingTurn] = []

    # -- introspection ----------------------------------------------------
    @property
    def is_complete(self) -> bool:
        return self.state == ConversationState.COMPLETE

    @property
    def topic_index(self) -> int:
        """1-based index of the current topic (0 before start)."""

        if self.state == ConversationState.NOT_STARTED:
            return 0
        return min(self._topic_idx + 1, len(self.topics))

    @property
    def total_topics(self) -> int:
        return len(self.topics)

    @property
    def progress(self) -> float:
        """Fraction complete in ``[0.0, 1.0]`` based on topics covered."""

        if not self.topics:
            return 1.0
        if self.is_complete:
            return 1.0
        return self._topic_idx / len(self.topics)

    def current_topic(self) -> Optional[Topic]:
        if 0 <= self._topic_idx < len(self.topics):
            return self.topics[self._topic_idx]
        return None

    def current_question(self) -> Optional[Question]:
        topic = self.current_topic()
        if topic and 0 <= self._question_idx < len(topic.questions):
            return topic.questions[self._question_idx]
        return None

    # -- driving the conversation ----------------------------------------
    def start(self) -> Prompt:
        """Begin (or restart) the conversation and return the first prompt."""

        if self.state == ConversationState.NOT_STARTED:
            self.state = ConversationState.ASKING
        return self.current_prompt()

    def current_prompt(self) -> Prompt:
        """Return what MimOSA should say right now (does not advance)."""

        if self.state == ConversationState.NOT_STARTED:
            self.state = ConversationState.ASKING
        if self._topic_idx >= len(self.topics):
            self.state = ConversationState.COMPLETE
            return Prompt(
                text=self._completion_message(),
                kind=PromptKind.COMPLETE,
                topic_index=len(self.topics),
                total_topics=len(self.topics),
            )

        topic = self.topics[self._topic_idx]
        question = self.current_question()
        q_text = question.text if question else ""

        if self._pending_intro:
            # Lead with the warm topic intro, then the first question.
            text = f"{topic.intro} {q_text}".strip()
            kind = PromptKind.TOPIC_INTRO
        elif self._follow_up_count > 0:
            kind = PromptKind.FOLLOW_UP
            text = self.current_follow_up_text() or q_text
        else:
            kind = PromptKind.QUESTION
            text = q_text

        return Prompt(
            text=text,
            kind=kind,
            topic_id=topic.id,
            question_id=question.id if question else "",
            topic_index=self._topic_idx + 1,
            total_topics=len(self.topics),
        )

    def submit_response(self, text: str) -> Dict[str, Any]:
        """Record *text* for the current prompt and decide what comes next.

        Returns a dict describing the outcome::

            {
              "depth": "shallow"|"medium"|"deep",
              "facts": [...],
              "encouragement": Optional[str],   # warm nudge if thin
              "next_prompt": Prompt,            # what to say next
              "advanced": bool,                 # moved to a new question/topic
            }
        """

        if self.state == ConversationState.COMPLETE:
            return {
                "depth": None,
                "facts": [],
                "encouragement": None,
                "next_prompt": self.current_prompt(),
                "advanced": False,
            }

        if self.state == ConversationState.NOT_STARTED:
            self.state = ConversationState.ASKING

        topic = self.current_topic()
        question = self.current_question()
        answer = (text or "").strip()
        depth = analyze_response_depth(answer)
        vague = is_vague(answer)

        # Extract + record facts (best-effort).
        facts = self._extract_facts(answer, topic, question)
        self._record_facts(facts, topic, question)
        self._record_conversation_turn(question, answer)

        # Append to transcript.
        prompt_text = question.text if question else ""
        self.transcript.append(
            OnboardingTurn(
                topic_id=topic.id if topic else "",
                question_id=question.id if question else "",
                prompt=prompt_text,
                response=answer,
                depth=depth.value,
                facts=facts,
                is_follow_up=self._follow_up_count > 0,
            )
        )

        # Once we've shown the intro for a topic, subsequent prompts are plain.
        self._pending_intro = False

        # Decide whether to offer a follow-up/encouragement or advance.
        encouragement: Optional[str] = None
        wants_follow_up = (
            depth == ResponseDepth.SHALLOW
            and self._follow_up_count < self.MAX_FOLLOW_UPS
            and question is not None
            and len(question.follow_ups) > 0
        )

        if wants_follow_up:
            encouragement = encouragement_for(
                topic.id if topic else None, vague=vague
            )
            # Replace the current question text with its follow-up phrasing so
            # current_prompt() surfaces the deeper ask.
            self._follow_up_count += 1
            advanced = False
        else:
            advanced = True
            self._advance()

        return {
            "depth": depth.value,
            "facts": facts,
            "encouragement": encouragement,
            "next_prompt": self.current_prompt(),
            "advanced": advanced,
        }

    def skip_topic(self) -> Prompt:
        """Skip the rest of the current topic and move to the next one."""

        self._topic_idx += 1
        self._question_idx = 0
        self._follow_up_count = 0
        self._pending_intro = True
        if self._topic_idx >= len(self.topics):
            self.state = ConversationState.COMPLETE
        return self.current_prompt()

    # -- internals --------------------------------------------------------
    def _advance(self) -> None:
        """Move to the next question, or the next topic when exhausted."""

        self._follow_up_count = 0
        topic = self.current_topic()
        if topic and self._question_idx + 1 < len(topic.questions):
            self._question_idx += 1
            return
        # Next topic.
        self._topic_idx += 1
        self._question_idx = 0
        self._pending_intro = True
        if self._topic_idx >= len(self.topics):
            self.state = ConversationState.COMPLETE

    def current_follow_up_text(self) -> Optional[str]:
        """Return the active follow-up phrasing, if any."""

        question = self.current_question()
        if not question or self._follow_up_count <= 0:
            return None
        idx = min(self._follow_up_count - 1, len(question.follow_ups) - 1)
        if idx < 0:
            return None
        return question.follow_ups[idx]

    def _extract_facts(
        self, answer: str, topic: Optional[Topic], question: Optional[Question]
    ) -> List[Dict[str, Any]]:
        if not answer or self.fact_extractor is None:
            return []
        try:
            return self.fact_extractor.extract(answer, topic, question) or []
        except Exception:
            return []

    def _record_facts(
        self,
        facts: List[Dict[str, Any]],
        topic: Optional[Topic],
        question: Optional[Question],
    ) -> None:
        if not facts:
            return
        if self.profile_manager is not None:
            try:
                self.profile_manager.update_from_facts(facts)
            except Exception:
                pass

    def _record_conversation_turn(
        self, question: Optional[Question], answer: str
    ) -> None:
        if not answer or self.vector_store is None:
            return
        try:
            topic = self.current_topic()
            self.vector_store.add_conversation_turn(
                question.text if question else "",
                answer,
                topic=topic.id if topic else None,
            )
        except Exception:
            pass

    def _completion_message(self) -> str:
        name = ""
        if self.profile_manager is not None:
            try:
                name = getattr(self.profile_manager.profile, "name", "") or ""
            except Exception:
                name = ""
        who = f", {name}" if name else ""
        return (
            f"Thanks so much for sharing{who} — I feel like I know you a lot "
            "better now! I'll keep all of this in mind to help you the way you "
            "like. You can always update any of it later."
        )

    # -- serialisation for pause/resume -----------------------------------
    def to_state(self) -> Dict[str, Any]:
        """Serialise enough to resume the conversation later."""

        return {
            "state": self.state.value,
            "topic_idx": self._topic_idx,
            "question_idx": self._question_idx,
            "follow_up_count": self._follow_up_count,
            "pending_intro": self._pending_intro,
            "transcript": [
                {
                    "topic_id": t.topic_id,
                    "question_id": t.question_id,
                    "prompt": t.prompt,
                    "response": t.response,
                    "depth": t.depth,
                    "facts": t.facts,
                    "is_follow_up": t.is_follow_up,
                }
                for t in self.transcript
            ],
        }

    def load_state(self, data: Dict[str, Any]) -> None:
        """Restore engine position from :meth:`to_state` output."""

        if not isinstance(data, dict):
            return
        try:
            self.state = ConversationState(data.get("state", "asking"))
        except Exception:
            self.state = ConversationState.ASKING
        self._topic_idx = int(data.get("topic_idx", 0) or 0)
        self._question_idx = int(data.get("question_idx", 0) or 0)
        self._follow_up_count = int(data.get("follow_up_count", 0) or 0)
        self._pending_intro = bool(data.get("pending_intro", True))
        self.transcript = []
        for raw in data.get("transcript", []) or []:
            if not isinstance(raw, dict):
                continue
            self.transcript.append(
                OnboardingTurn(
                    topic_id=raw.get("topic_id", ""),
                    question_id=raw.get("question_id", ""),
                    prompt=raw.get("prompt", ""),
                    response=raw.get("response", ""),
                    depth=raw.get("depth"),
                    facts=raw.get("facts", []) or [],
                    is_follow_up=bool(raw.get("is_follow_up", False)),
                )
            )

"""Question skill -- general-knowledge answers via the LLM.

This is the first **LLM-backed** skill: questions like "Who was Albert
Einstein?" or "What is the capital of France?" require world knowledge, so they
are routed to the Abacus.AI RouteLLM provider through MimOSA's LLM abstraction
layer (:mod:`mimosa.llm`).

Privacy note
------------
Only the **transcribed text** of the question (plus recent conversation
context) is sent to the cloud LLM -- never audio. When the Privacy Guard forces
local-only mode (Phase 4), the same code path will transparently use a local
provider instead, because it depends only on the
:class:`~mimosa.llm.base_provider.BaseLLMProvider` interface.

Responses are constrained by a system prompt to be concise and speakable, since
the answer is read aloud by TTS.
"""

from __future__ import annotations

from typing import List, Optional

from mimosa.llm.base_provider import LLMError, Message, Role
from mimosa.skills.base_skill import BaseSkill, SkillResult

#: System prompt that shapes answers to be short and voice-friendly.
QUESTION_SYSTEM_PROMPT = (
    "You are MimOSA, a helpful local voice assistant. Answer the user's "
    "question accurately and concisely in 1-3 sentences. Use plain, "
    "conversational language suitable for being read aloud. Do not use markdown, "
    "bullet points, or code blocks. If you are unsure, say so briefly."
)

#: Max conversation-history messages to include for context.
DEFAULT_CONTEXT_MESSAGES = 6


class QuestionSkill(BaseSkill):
    """Answer general-knowledge questions using the LLM provider."""

    name = "question"
    intents = ["question"]
    uses_llm = True

    def __init__(self, llm_provider=None, max_tokens: int = 256) -> None:
        super().__init__(llm_provider=llm_provider)
        self.max_tokens = max_tokens

    def handle(self, text: str, context: Optional[List] = None) -> SkillResult:
        if self.llm is None:
            return SkillResult(
                text="I can't answer that right now because no language model "
                "is configured.",
                success=False,
                skill=self.name,
            )

        messages: List[Message] = [Message(role=Role.SYSTEM, content=QUESTION_SYSTEM_PROMPT)]

        # Include recent context (already Message objects) if provided.
        if context:
            messages.extend(context[-DEFAULT_CONTEXT_MESSAGES:])

        messages.append(Message(role=Role.USER, content=text))

        try:
            response = self.llm.chat(messages, temperature=0.3, max_tokens=self.max_tokens)
        except LLMError as exc:
            self.logger.warning("LLM question failed: %s", exc)
            return SkillResult(
                text="Sorry, I'm having trouble reaching my knowledge service "
                "right now. Please try again in a moment.",
                success=False,
                skill=self.name,
                metadata={"error": str(exc)},
            )

        answer = (response.content or "").strip()
        if not answer:
            answer = "I'm not sure how to answer that."
        return SkillResult(
            text=answer,
            skill=self.name,
            metadata={"model": response.model, "tokens": response.total_tokens},
        )

    def _error_message(self) -> str:
        return "Sorry, I couldn't answer that question right now."

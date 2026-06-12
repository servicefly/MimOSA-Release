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
from mimosa.llm.persona import build_system_prompt
from mimosa.skills.base_skill import BaseSkill, SkillResult

#: Task-specific guidance layered on top of MimOSA's shared identity + tone.
QUESTION_TASK_INSTRUCTIONS = (
    "The user is asking a general-knowledge question. Answer accurately and "
    "concisely in 1-3 sentences. If you are unsure, just say so briefly and "
    "naturally."
)

#: Full system prompt: MimOSA's identity + natural tone + the question task.
QUESTION_SYSTEM_PROMPT = build_system_prompt(QUESTION_TASK_INSTRUCTIONS)

#: Max conversation-history messages to include for context.
DEFAULT_CONTEXT_MESSAGES = 6


class QuestionSkill(BaseSkill):
    """Answer general-knowledge questions using the LLM provider."""

    name = "question"
    intents = ["question"]
    uses_llm = True

    def __init__(self, llm_provider=None, max_tokens: int = 256,
                 personality=None) -> None:
        super().__init__(llm_provider=llm_provider)
        self.max_tokens = max_tokens
        #: Optional :class:`~mimosa.utils.config.PersonalitySettings` so answers
        #: adopt the user's chosen assistant name, address, and verbosity.
        self.personality = personality

    def _system_prompt(self) -> str:
        """MimOSA's identity + tone + question task, personalised when known."""
        if self.personality is None:
            return QUESTION_SYSTEM_PROMPT
        return build_system_prompt(
            QUESTION_TASK_INSTRUCTIONS, personality=self.personality
        )

    def handle(self, text: str, context: Optional[List] = None) -> SkillResult:
        if self.llm is None:
            return SkillResult(
                text="I can't answer that right now because no language model "
                "is configured.",
                success=False,
                skill=self.name,
            )

        messages: List[Message] = [Message(role=Role.SYSTEM, content=self._system_prompt())]

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

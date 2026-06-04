"""Greeting / chit-chat skill -- friendly small talk via the LLM.

Handles greetings and light conversational filler -- "Hello", "Hi there",
"Good morning", "How are you?" -- with MimOSA's personality. These are
LLM-backed so replies feel natural and varied rather than canned, but a
**local fallback** set of responses is used if no LLM is configured or the call
fails, so greetings always work even offline.

Privacy note
------------
Only transcribed text is sent to the LLM (never audio), and the Privacy Guard
can route this through a local provider in local-only mode.
"""

from __future__ import annotations

import random
from typing import List, Optional

from mimosa.llm.base_provider import LLMError, Message, Role
from mimosa.skills.base_skill import BaseSkill, SkillResult

#: Personality/system prompt for greetings and chit-chat.
GREETING_SYSTEM_PROMPT = (
    "You are MimOSA, a warm, friendly, and concise local voice assistant. "
    "The user is greeting you or making small talk. Respond in one short, "
    "natural sentence suitable for being read aloud. Be personable but brief. "
    "Do not use markdown or emoji."
)

#: Local fallbacks used when the LLM is unavailable -- greetings must never fail.
_FALLBACK_REPLIES = (
    "Hello! How can I help you today?",
    "Hi there! What can I do for you?",
    "Hey! I'm here and ready to help.",
    "Good to hear from you. What would you like to do?",
)


class GreetingSkill(BaseSkill):
    """Respond to greetings and small talk with personality (LLM + fallback)."""

    name = "greeting"
    intents = ["greeting", "chitchat"]
    uses_llm = True

    def __init__(self, llm_provider=None, max_tokens: int = 64) -> None:
        super().__init__(llm_provider=llm_provider)
        self.max_tokens = max_tokens

    def handle(self, text: str, context: Optional[List] = None) -> SkillResult:
        # No LLM configured -> use a friendly local fallback.
        if self.llm is None:
            return SkillResult(
                text=random.choice(_FALLBACK_REPLIES),
                skill=self.name,
                metadata={"source": "fallback"},
            )

        messages: List[Message] = [Message(role=Role.SYSTEM, content=GREETING_SYSTEM_PROMPT)]
        if context:
            messages.extend(context[-4:])
        messages.append(Message(role=Role.USER, content=text))

        try:
            response = self.llm.chat(messages, temperature=0.8, max_tokens=self.max_tokens)
        except LLMError as exc:
            self.logger.info("LLM greeting failed, using fallback: %s", exc)
            return SkillResult(
                text=random.choice(_FALLBACK_REPLIES),
                skill=self.name,
                metadata={"source": "fallback", "error": str(exc)},
            )

        reply = (response.content or "").strip() or random.choice(_FALLBACK_REPLIES)
        return SkillResult(
            text=reply,
            skill=self.name,
            metadata={"source": "llm", "model": response.model},
        )

    def _error_message(self) -> str:
        return random.choice(_FALLBACK_REPLIES)

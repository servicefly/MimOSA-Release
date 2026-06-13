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
from mimosa.llm.persona import build_system_prompt
from mimosa.skills.base_skill import BaseSkill, SkillResult

#: Task-specific guidance for greetings/chit-chat, layered on MimOSA's shared
#: identity + natural tone (see :mod:`mimosa.llm.persona`).
GREETING_TASK_INSTRUCTIONS = (
    "The user is greeting you or making small talk. Reply in one short, natural "
    "sentence, like a friend saying hi back. Be warm and personable but brief."
)

#: Full default greeting prompt (identity + tone + task), used when no
#: personality settings are available.
GREETING_SYSTEM_PROMPT = build_system_prompt(GREETING_TASK_INSTRUCTIONS)

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

    def __init__(self, llm_provider=None, max_tokens: int = 64,
                 personality=None, user_profile=None) -> None:
        super().__init__(llm_provider=llm_provider)
        self.max_tokens = max_tokens
        #: Optional :class:`~mimosa.utils.config.PersonalitySettings` from the
        #: "Get to Know MimOSA" setup step. When present, greetings address the
        #: user by name and adopt the chosen assistant name.
        self.personality = personality
        #: Optional learned :class:`~mimosa.memory.profile_manager.UserProfile`
        #: (M3) injected so greetings reflect what MimOSA knows about the user.
        self.user_profile = user_profile

    def _fallback_reply(self) -> str:
        """A friendly local greeting, personalised when we know the user."""
        p = self.personality
        if p is not None and getattr(p, "greet_by_name", False) and getattr(p, "user_name", ""):
            return f"Hello {p.user_name}! How can I help you today?"
        return random.choice(_FALLBACK_REPLIES)

    def _system_prompt(self) -> str:
        if self.personality is None and self.user_profile is None:
            return GREETING_SYSTEM_PROMPT
        return build_system_prompt(
            GREETING_TASK_INSTRUCTIONS,
            personality=self.personality,
            user_profile=self.user_profile,
        )

    def handle(self, text: str, context: Optional[List] = None) -> SkillResult:
        # No LLM configured -> use a friendly local fallback.
        if self.llm is None:
            return SkillResult(
                text=self._fallback_reply(),
                skill=self.name,
                metadata={"source": "fallback"},
            )

        messages: List[Message] = [Message(role=Role.SYSTEM, content=self._system_prompt())]
        if context:
            messages.extend(context[-4:])
        messages.append(Message(role=Role.USER, content=text))

        try:
            response = self.llm.chat(messages, temperature=0.8, max_tokens=self.max_tokens)
        except LLMError as exc:
            self.logger.info("LLM greeting failed, using fallback: %s", exc)
            return SkillResult(
                text=self._fallback_reply(),
                skill=self.name,
                metadata={"source": "fallback", "error": str(exc)},
            )

        reply = (response.content or "").strip() or self._fallback_reply()
        return SkillResult(
            text=reply,
            skill=self.name,
            metadata={"source": "llm", "model": response.model},
        )

    def _error_message(self) -> str:
        return random.choice(_FALLBACK_REPLIES)

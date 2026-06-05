"""Optional text-chat window logic (M4.3).

Some users prefer (or occasionally need) to *type* to MimOSA instead of
speaking -- in a quiet room, for accessibility, or to issue a precise command.
The chat window provides that path while reusing the exact same brain as the
voice loop: utterances are dispatched through the shared
:class:`~mimosa.core.intent_router.IntentRouter` and recorded in a
:class:`~mimosa.core.conversation_manager.ConversationManager`, so spoken and
typed turns share one coherent history.

This module is pure logic (:class:`ChatController`); the GTK window
(:mod:`mimosa.ui.chat_window`) is a thin view that renders :class:`ChatMessage`
objects and forwards input to :meth:`ChatController.send`.  The router and
conversation manager are injected, so tests run fully offline with fakes.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable, List, Optional

logger = logging.getLogger(__name__)

ROLE_USER = "user"
ROLE_ASSISTANT = "assistant"
ROLE_SYSTEM = "system"

#: Maximum messages retained in the visible log (oldest are trimmed).
DEFAULT_MAX_MESSAGES = 200


@dataclass(frozen=True)
class ChatMessage:
    """A single line in the chat transcript."""

    role: str
    text: str
    timestamp: float
    intent: Optional[str] = None
    success: bool = True


class ChatController:
    """Pure-logic controller backing the optional chat window.

    Args:
        router: Object exposing ``route(text, context=...) -> SkillResult``
            (typically an :class:`IntentRouter`).  When ``None`` the controller
            still records the user's message but replies with a friendly
            "not available" notice, so the UI degrades gracefully.
        conversation: Shared :class:`ConversationManager`.  When ``None`` a
            private one is created lazily.
        clock: Injectable ``() -> float`` time source (defaults to
            ``time.time``) for deterministic tests.
        max_messages: Visible-log cap.
    """

    def __init__(
        self,
        router=None,
        conversation=None,
        *,
        clock: Optional[Callable[[], float]] = None,
        max_messages: int = DEFAULT_MAX_MESSAGES,
    ) -> None:
        self._router = router
        self._conversation = conversation
        self._clock = clock
        self._max_messages = max(1, int(max_messages))
        self._messages: List[ChatMessage] = []

    # -- time helper --
    def _now(self) -> float:
        if self._clock is not None:
            return float(self._clock())
        import time

        return time.time()

    # -- conversation manager (lazy) --
    @property
    def conversation(self):
        if self._conversation is None:
            from mimosa.core.conversation_manager import ConversationManager

            self._conversation = ConversationManager()
        return self._conversation

    @property
    def messages(self) -> List[ChatMessage]:
        """A copy of the current visible transcript."""
        return list(self._messages)

    @property
    def message_count(self) -> int:
        return len(self._messages)

    @property
    def has_router(self) -> bool:
        return self._router is not None

    def set_router(self, router) -> None:
        """Attach (or replace) the routing brain after construction."""
        self._router = router

    # -- mutation --
    def _append(self, message: ChatMessage) -> ChatMessage:
        self._messages.append(message)
        if len(self._messages) > self._max_messages:
            # Trim oldest, keeping the most recent ``max_messages`` entries.
            self._messages = self._messages[-self._max_messages:]
        return message

    def add_system_message(self, text: str) -> ChatMessage:
        """Record a system/notice line (e.g. greeting, errors)."""
        return self._append(
            ChatMessage(role=ROLE_SYSTEM, text=text, timestamp=self._now())
        )

    def send(self, text: str) -> Optional[ChatMessage]:
        """Process a typed user message and return the assistant's reply.

        Empty/whitespace input is ignored (returns ``None``).  Routing errors
        are caught and surfaced as an unsuccessful assistant message rather than
        propagated, so the window never crashes on a bad turn.
        """
        clean = (text or "").strip()
        if not clean:
            return None

        now = self._now()
        self._append(
            ChatMessage(role=ROLE_USER, text=clean, timestamp=now)
        )

        if self._router is None:
            return self._append(
                ChatMessage(
                    role=ROLE_ASSISTANT,
                    text="Chat isn't connected to the assistant yet.",
                    timestamp=self._now(),
                    success=False,
                )
            )

        context = None
        try:
            context = self.conversation.get_context_messages()
        except Exception:  # pragma: no cover - defensive
            logger.exception("Failed to build conversation context; continuing.")

        try:
            result = self._router.route(clean, context=context)
            reply_text = getattr(result, "text", "") or ""
            success = bool(getattr(result, "success", True))
            intent = None
            metadata = getattr(result, "metadata", None)
            if isinstance(metadata, dict):
                intent = metadata.get("intent")
        except Exception:
            logger.exception("Router raised while handling chat message.")
            reply_text = "Sorry, something went wrong handling that."
            success = False
            intent = None

        # Keep the shared history coherent with the voice loop.
        try:
            self.conversation.add_turn(clean, reply_text, intent=intent)
        except Exception:  # pragma: no cover - defensive
            logger.exception("Failed to record conversation turn.")

        return self._append(
            ChatMessage(
                role=ROLE_ASSISTANT,
                text=reply_text,
                timestamp=self._now(),
                intent=intent,
                success=success,
            )
        )

    # -- maintenance / export --
    def clear(self) -> None:
        """Clear the visible transcript (does not touch conversation memory)."""
        self._messages.clear()

    def reset(self) -> None:
        """Clear the transcript *and* the underlying conversation history."""
        self._messages.clear()
        try:
            self.conversation.clear()
        except Exception:  # pragma: no cover - defensive
            logger.exception("Failed to clear conversation history.")

    def to_transcript(self) -> str:
        """Render the visible log as a plain-text transcript."""
        lines = []
        for m in self._messages:
            prefix = {ROLE_USER: "You", ROLE_ASSISTANT: "MimOSA", ROLE_SYSTEM: "—"}.get(
                m.role, m.role
            )
            lines.append(f"{prefix}: {m.text}")
        return "\n".join(lines)

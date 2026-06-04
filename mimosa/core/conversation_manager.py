"""Conversation state and context management for MimOSA.

The :class:`ConversationManager` holds the short-term, in-memory history of a
dialogue so that skills (especially LLM-backed ones) can answer with awareness
of what was just said -- e.g. follow-ups like "and what about tomorrow?".

Scope (M1.3)
------------
This is deliberately a *short-term* context buffer:

* It keeps the last ``max_history`` turns (a turn = one user message + one
  assistant reply), bounded so prompts stay small and cheap.
* It exposes the history as a list of
  :class:`~mimosa.llm.base_provider.Message` objects ready to splice into an
  LLM request.
* It tracks lightweight per-session metadata (id, created time, turn count).

What it is **not** (yet)
------------------------
Long-term, persistent memory (vector store, summarization, recall across
sessions) arrives in Phase 3. This class is intentionally structured so that
layer can be added behind the same interface: a future ``MemoryStore`` can be
injected to persist/retrieve turns without changing callers. The
:meth:`to_memory_records` hook already emits a persistence-friendly shape.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from mimosa.llm.base_provider import Message, Role

#: Default number of *turns* (user+assistant pairs) to retain.
DEFAULT_MAX_HISTORY = 10


@dataclass
class Turn:
    """A single exchange: the user's utterance and MimOSA's reply.

    Attributes:
        user_text: What the user said (transcribed).
        assistant_text: What MimOSA replied.
        intent: The classified intent for this turn, if known.
        timestamp: Unix epoch seconds when the turn was recorded.
    """

    user_text: str
    assistant_text: str = ""
    intent: Optional[str] = None
    timestamp: float = field(default_factory=time.time)


class ConversationManager:
    """In-memory, bounded conversation history with session metadata.

    Args:
        max_history: Maximum number of turns to retain. Older turns are evicted
            FIFO. Reads from ``MAX_CONVERSATION_HISTORY`` env via the caller if
            desired; defaults to :data:`DEFAULT_MAX_HISTORY`.
        session_id: Optional explicit session id; a UUID4 is generated if omitted.
    """

    def __init__(self, max_history: int = DEFAULT_MAX_HISTORY, session_id: Optional[str] = None) -> None:
        self.max_history = max(1, int(max_history))
        self.session_id = session_id or uuid.uuid4().hex
        self.created_at = time.time()
        self._turns: List[Turn] = []

    # -- recording ---------------------------------------------------------

    def add_turn(self, user_text: str, assistant_text: str = "", intent: Optional[str] = None) -> Turn:
        """Record a completed turn and enforce the history bound.

        Returns:
            The :class:`Turn` that was appended.
        """
        turn = Turn(user_text=user_text, assistant_text=assistant_text, intent=intent)
        self._turns.append(turn)
        # Evict oldest turns beyond the cap.
        if len(self._turns) > self.max_history:
            self._turns = self._turns[-self.max_history:]
        return turn

    def update_last_response(self, assistant_text: str, intent: Optional[str] = None) -> None:
        """Fill in the assistant reply (and optionally intent) for the latest turn.

        Useful when the user text is recorded first (on capture) and the reply
        is known later (after the skill runs).
        """
        if not self._turns:
            self.add_turn(user_text="", assistant_text=assistant_text, intent=intent)
            return
        self._turns[-1].assistant_text = assistant_text
        if intent is not None:
            self._turns[-1].intent = intent

    # -- access ------------------------------------------------------------

    @property
    def turns(self) -> List[Turn]:
        """A copy of the retained turns (oldest first)."""
        return list(self._turns)

    @property
    def turn_count(self) -> int:
        """Number of turns currently retained."""
        return len(self._turns)

    def get_context_messages(self, max_messages: Optional[int] = None) -> List[Message]:
        """Return history as LLM :class:`Message` objects (oldest first).

        Each turn becomes a USER message followed by an ASSISTANT message
        (assistant omitted if empty). This is ready to prepend before the
        current user message in an LLM request.

        Args:
            max_messages: Optional cap on the number of returned messages
                (most recent kept). ``None`` returns all within the history
                bound.
        """
        messages: List[Message] = []
        for turn in self._turns:
            if turn.user_text:
                messages.append(Message(role=Role.USER, content=turn.user_text))
            if turn.assistant_text:
                messages.append(Message(role=Role.ASSISTANT, content=turn.assistant_text))
        if max_messages is not None and max_messages >= 0:
            messages = messages[-max_messages:]
        return messages

    def last_intent(self) -> Optional[str]:
        """The intent of the most recent turn, if any."""
        return self._turns[-1].intent if self._turns else None

    # -- lifecycle ---------------------------------------------------------

    def clear(self) -> None:
        """Drop all history (e.g. on a new session) but keep the session id."""
        self._turns.clear()

    def reset_session(self) -> str:
        """Start a fresh session: new id, cleared history. Returns new id."""
        self.session_id = uuid.uuid4().hex
        self.created_at = time.time()
        self._turns.clear()
        return self.session_id

    # -- future memory integration hook -----------------------------------

    def to_memory_records(self) -> List[Dict]:
        """Serialize turns into a persistence-friendly shape.

        This is the seam for Phase 3 long-term memory: a future ``MemoryStore``
        can consume these records to persist/index the conversation without any
        change to callers of :class:`ConversationManager`.
        """
        return [
            {
                "session_id": self.session_id,
                "user_text": t.user_text,
                "assistant_text": t.assistant_text,
                "intent": t.intent,
                "timestamp": t.timestamp,
            }
            for t in self._turns
        ]

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return (
            f"ConversationManager(session={self.session_id[:8]}..., "
            f"turns={self.turn_count}/{self.max_history})"
        )

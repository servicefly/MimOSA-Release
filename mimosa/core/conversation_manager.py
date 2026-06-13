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

import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Dict, List, Optional

from mimosa.llm.base_provider import Message, Role

if TYPE_CHECKING:  # pragma: no cover - typing only
    from mimosa.memory.conversation_store import ConversationStore

logger = logging.getLogger(__name__)

#: Default number of *turns* (user+assistant pairs) to retain.
DEFAULT_MAX_HISTORY = 10

# Emotion detection (M4) ----------------------------------------------------
EMOTION_NEUTRAL = "neutral"
EMOTION_FRUSTRATED = "frustrated"
EMOTION_EXCITED = "excited"
EMOTION_STRESSED = "stressed"

#: Lightweight keyword cues per emotion. Deliberately conservative -- this is a
#: *hint* used to soften MimOSA's tone, never a diagnosis. Lowercase substrings.
_EMOTION_CUES = {
    EMOTION_FRUSTRATED: (
        "frustrated", "annoyed", "ugh", "this is stupid", "not working",
        "doesn't work", "won't work", "broken", "hate this", "useless",
        "come on", "seriously", "fed up", "again?!",
    ),
    EMOTION_STRESSED: (
        "stressed", "overwhelmed", "so much to do", "no time", "deadline",
        "running late", "panic", "anxious", "swamped", "can't keep up",
        "too much", "burnt out", "burned out",
    ),
    EMOTION_EXCITED: (
        "awesome", "amazing", "excited", "can't wait", "love it", "yay",
        "fantastic", "brilliant", "so cool", "this is great", "woohoo",
        "let's go", "pumped", "thrilled",
    ),
}

#: Pronouns that usually refer back to something said earlier.
_REFERENCE_PRONOUNS = frozenset(
    {"it", "that", "this", "them", "those", "these", "they", "one"}
)

_WORD_RE = re.compile(r"[a-zA-Z][a-zA-Z'\-]*")


def detect_emotion(text: str) -> str:
    """Best-effort emotion hint for *text*.

    Returns one of ``"frustrated"``, ``"stressed"``, ``"excited"`` or
    ``"neutral"``. Uses simple, transparent keyword/punctuation cues so it's
    fully deterministic and hermetic. Never raises.
    """
    try:
        low = str(text or "").lower().strip()
        if not low:
            return EMOTION_NEUTRAL
        scores: Dict[str, int] = {k: 0 for k in _EMOTION_CUES}
        for emotion, cues in _EMOTION_CUES.items():
            for cue in cues:
                if cue in low:
                    scores[emotion] += 1
        # Punctuation signals: many !!! → excitement or frustration emphasis.
        exclaims = low.count("!")
        if exclaims >= 2:
            # Bias toward whichever (frust/excited) already has a cue, else
            # treat strong exclamation as excitement.
            if scores[EMOTION_FRUSTRATED] == 0 and scores[EMOTION_EXCITED] == 0:
                scores[EMOTION_EXCITED] += 1
        best = max(scores, key=lambda k: scores[k])
        return best if scores[best] > 0 else EMOTION_NEUTRAL
    except Exception:  # pragma: no cover - defensive
        return EMOTION_NEUTRAL


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
    #: Whether this turn was flagged sensitive (Privacy Guard, M5.4). Private
    #: turns stay in the live buffer but may be withheld from cloud context and
    #: from disk depending on policy.
    is_private: bool = False
    #: Internal: set once the turn has been written to a durable store so it is
    #: never persisted twice. Not part of the serialized record.
    _persisted: bool = field(default=False, repr=False, compare=False)


class ConversationManager:
    """In-memory, bounded conversation history with session metadata.

    Args:
        max_history: Maximum number of turns to retain. Older turns are evicted
            FIFO. Reads from ``MAX_CONVERSATION_HISTORY`` env via the caller if
            desired; defaults to :data:`DEFAULT_MAX_HISTORY`.
        session_id: Optional explicit session id; a UUID4 is generated if omitted.
    """

    def __init__(
        self,
        max_history: int = DEFAULT_MAX_HISTORY,
        session_id: Optional[str] = None,
        store: "Optional[ConversationStore]" = None,
        *,
        persist_private: bool = True,
    ) -> None:
        self.max_history = max(1, int(max_history))
        self.session_id = session_id or uuid.uuid4().hex
        self.created_at = time.time()
        self._turns: List[Turn] = []
        #: Optional durable backing store (M5.1). When set, completed turns are
        #: mirrored to SQLite so context survives restarts. ``None`` preserves
        #: the original in-memory-only behaviour (fully backward compatible).
        self.store = store
        #: Whether to also persist turns flagged private. The in-memory buffer
        #: always holds them for the live session; this only governs disk.
        self.persist_private = bool(persist_private)
        if self.store is not None:
            try:
                self.store.ensure_session(self.session_id)
            except Exception:  # pragma: no cover - storage faults are non-fatal
                logger.exception("Could not initialise session in store; continuing")

    # -- recording ---------------------------------------------------------

    def add_turn(
        self,
        user_text: str,
        assistant_text: str = "",
        intent: Optional[str] = None,
        *,
        is_private: bool = False,
    ) -> Turn:
        """Record a completed turn and enforce the history bound.

        When a durable :attr:`store` is attached the turn is also persisted
        (unless it is private and ``persist_private`` is ``False``).

        Returns:
            The :class:`Turn` that was appended.
        """
        # Flush any previous still-pending turn (e.g. a user utterance whose
        # reply never arrived) so it isn't lost when a new turn starts.
        if self._turns and not self._turns[-1]._persisted:
            self._persist_turn(self._turns[-1])
        turn = Turn(
            user_text=user_text,
            assistant_text=assistant_text,
            intent=intent,
            is_private=is_private,
        )
        self._turns.append(turn)
        # Evict oldest turns beyond the cap.
        if len(self._turns) > self.max_history:
            self._turns = self._turns[-self.max_history:]
        # Persist immediately only when the turn is already complete (has a
        # reply). A user-only turn stays pending until the reply is filled in
        # via update_last_response, avoiding a duplicate row.
        if turn.assistant_text:
            self._persist_turn(turn)
        return turn

    def update_last_response(
        self,
        assistant_text: str,
        intent: Optional[str] = None,
        *,
        is_private: Optional[bool] = None,
    ) -> None:
        """Fill in the assistant reply (and optionally intent) for the latest turn.

        Useful when the user text is recorded first (on capture) and the reply
        is known later (after the skill runs). If a store is attached the
        updated turn is (re)persisted once complete.
        """
        if not self._turns:
            self.add_turn(
                user_text="",
                assistant_text=assistant_text,
                intent=intent,
                is_private=bool(is_private),
            )
            return
        last = self._turns[-1]
        # This turn was created earlier (user side) but not yet persisted; fill
        # it in and persist the complete turn now to avoid duplicate rows.
        last.assistant_text = assistant_text
        if intent is not None:
            last.intent = intent
        if is_private is not None:
            last.is_private = bool(is_private)
        if not last._persisted:
            self._persist_turn(last)

    def _persist_turn(self, turn: Turn) -> None:
        """Mirror a turn to the durable store, if one is attached."""
        if self.store is None or turn._persisted:
            return
        if turn.is_private and not self.persist_private:
            return
        try:
            self.store.add_turn(
                self.session_id,
                turn.user_text,
                turn.assistant_text,
                intent=turn.intent,
                is_private=turn.is_private,
                timestamp=turn.timestamp,
            )
            turn._persisted = True
        except Exception:  # pragma: no cover - storage faults are non-fatal
            logger.exception("Failed to persist turn to store; continuing")

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

    # -- conversational intelligence (M4) ---------------------------------

    def last_user_text(self) -> str:
        """The most recent non-empty user utterance, or ``""``."""
        for turn in reversed(self._turns):
            if turn.user_text:
                return turn.user_text
        return ""

    def recent_user_texts(self, n: int = 5) -> List[str]:
        """Up to *n* most recent user utterances (oldest first)."""
        texts = [t.user_text for t in self._turns if t.user_text]
        return texts[-max(0, int(n)):]

    @staticmethod
    def has_reference(text: str) -> bool:
        """Whether *text* contains a back-reference pronoun ("it", "that"…)."""
        try:
            words = {w.lower() for w in _WORD_RE.findall(str(text or ""))}
            return bool(words & _REFERENCE_PRONOUNS)
        except Exception:  # pragma: no cover - defensive
            return False

    def resolve_references(self, text: str) -> str:
        """Best-effort pronoun resolution using recent context.

        If *text* leans on a back-reference ("can you close it?") and we have a
        prior turn, we append a parenthetical hint naming the most likely
        referent (a salient noun from the previous user message). This keeps the
        original wording intact while giving downstream skills/LLM the context
        to disambiguate. When nothing useful is found, *text* is returned
        unchanged. Never raises.
        """
        try:
            t = str(text or "").strip()
            if not t or not self.has_reference(t):
                return t
            referent = self._salient_referent()
            if not referent:
                return t
            if referent.lower() in t.lower():
                return t
            return f"{t} (referring to: {referent})"
        except Exception:  # pragma: no cover - defensive
            return str(text or "")

    def _salient_referent(self) -> str:
        """Pick a likely referent from the previous user turn(s).

        Heuristic: the last "content" word (skipping common stopwords) of the
        most recent user message that *isn't* itself just a pronoun question.
        """
        stop = _REFERENCE_PRONOUNS | {
            "the", "a", "an", "can", "you", "please", "could", "would", "do",
            "i", "me", "my", "we", "to", "for", "and", "or", "is", "are", "of",
            "on", "in", "at", "with", "what", "how", "when", "where", "why",
            "open", "close", "show", "tell", "give", "get", "make", "let",
        }
        # Skip the current (most recent) turn -- we want the prior one.
        prior = [t.user_text for t in self._turns if t.user_text]
        if len(prior) < 2:
            return ""
        for utter in reversed(prior[:-1]):
            words = _WORD_RE.findall(utter)
            for w in reversed(words):
                if w.lower() not in stop and len(w) > 2:
                    return w
        return ""

    # -- durable store integration (M5.1) ---------------------------------

    def flush(self) -> None:
        """Persist any still-pending turns to the durable store (if attached).

        Call on shutdown (or before discarding the manager) so a final
        user-only turn whose reply never arrived is not lost.
        """
        for turn in self._turns:
            if not turn._persisted:
                self._persist_turn(turn)

    def load_from_store(self, max_turns: Optional[int] = None) -> int:
        """Rehydrate the in-memory buffer from the durable store.

        Reconstructs turns for :attr:`session_id` from persisted messages so a
        restarted MimOSA resumes with prior context. Pairs consecutive
        user→assistant messages into turns. Returns the number of turns loaded.
        ``max_turns`` defaults to :attr:`max_history`.
        """
        if self.store is None:
            return 0
        limit = self.max_history if max_turns is None else max(1, int(max_turns))
        try:
            stored = self.store.get_messages(self.session_id)
        except Exception:  # pragma: no cover - storage faults are non-fatal
            logger.exception("Could not load session from store; continuing")
            return 0

        turns: List[Turn] = []
        pending: Optional[Turn] = None
        for msg in stored:
            if msg.role == Role.USER.value:
                if pending is not None:
                    turns.append(pending)
                pending = Turn(
                    user_text=msg.content,
                    intent=msg.intent,
                    timestamp=msg.timestamp,
                    is_private=msg.is_private,
                    _persisted=True,
                )
            elif msg.role == Role.ASSISTANT.value:
                if pending is None:
                    pending = Turn(user_text="", timestamp=msg.timestamp, _persisted=True)
                pending.assistant_text = msg.content
                if msg.intent:
                    pending.intent = msg.intent
                pending.is_private = pending.is_private or msg.is_private
                turns.append(pending)
                pending = None
        if pending is not None:
            turns.append(pending)

        self._turns = turns[-limit:]
        return len(self._turns)

    # -- lifecycle ---------------------------------------------------------

    def clear(self) -> None:
        """Drop all in-memory history but keep the session id and store.

        Pending (unpersisted) turns are flushed first so disabling the buffer
        never silently loses durable history.
        """
        self.flush()
        self._turns.clear()

    def reset_session(self) -> str:
        """Start a fresh session: new id, cleared history. Returns new id.

        Any pending turns from the old session are flushed to the store first,
        and the new session is registered with the store if one is attached.
        """
        self.flush()
        self.session_id = uuid.uuid4().hex
        self.created_at = time.time()
        self._turns.clear()
        if self.store is not None:
            try:
                self.store.ensure_session(self.session_id)
            except Exception:  # pragma: no cover - storage faults are non-fatal
                logger.exception("Could not register new session in store; continuing")
        return self.session_id

    # -- future memory integration hook -----------------------------------

    def to_memory_records(self) -> List[Dict]:
        """Serialize turns into a persistence-friendly shape.

        This is the seam used by the M5.1 long-term store: a
        :class:`~mimosa.memory.conversation_store.ConversationStore` consumes
        these records to persist/index the conversation without any change to
        callers of :class:`ConversationManager`.
        """
        return [
            {
                "session_id": self.session_id,
                "user_text": t.user_text,
                "assistant_text": t.assistant_text,
                "intent": t.intent,
                "timestamp": t.timestamp,
                "is_private": t.is_private,
            }
            for t in self._turns
        ]

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return (
            f"ConversationManager(session={self.session_id[:8]}..., "
            f"turns={self.turn_count}/{self.max_history})"
        )

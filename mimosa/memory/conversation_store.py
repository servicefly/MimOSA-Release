"""Persistent conversation history for MimOSA (M5.1 — Conversation Persistence).

This module adds the long-term, on-disk half of MimOSA's memory architecture.
Where :class:`mimosa.core.conversation_manager.ConversationManager` keeps a
small, bounded, *in-session* buffer, :class:`ConversationStore` durably records
every turn in a local SQLite database (``conversations.db``) so context
survives restarts and can be searched/recalled across sessions.

Design goals
------------
* **Local-first & private.** A single SQLite file under the user's data dir
  (``~/.local/share/mimosa/conversations.db`` by default). Nothing is ever
  transmitted off the device; there is no telemetry. Each message carries an
  ``is_private`` flag so the Privacy Guard (M5.4) can keep sensitive turns out
  of any cloud-bound context.
* **Headless & dependency-free.** Uses only the standard-library :mod:`sqlite3`
  — no GTK, audio, or ML imports — so it loads and unit-tests cleanly on a
  headless machine. An in-memory database (``":memory:"``) is supported for
  tests.
* **Robust I/O.** Schema is created idempotently on connect; reads degrade to
  empty results rather than raising on a missing/locked row.
* **Thread-safe.** All access is guarded by a re-entrant lock and the
  connection is opened with ``check_same_thread=False`` so the GTK main loop
  and background voice threads can share one store.

Schema (spec §6)
----------------
``sessions``  — one row per conversation session (id, timestamps, title,
running turn count).
``messages``  — one row per message (role, content, intent, ``is_private``,
timestamp), foreign-keyed to a session. A *turn* (user utterance + assistant
reply) is stored as up to two message rows, which keeps the table generic
enough for richer role sequences (system/tool) later.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Union

from mimosa.llm.base_provider import Message, Role
from mimosa.memory.paths import conversations_db_path

logger = logging.getLogger(__name__)

#: Schema version stored in the SQLite ``user_version`` pragma; bump when the
#: table layout changes so :meth:`ConversationStore._migrate` can upgrade.
SCHEMA_VERSION = 1


@dataclass
class StoredMessage:
    """A single persisted message row.

    Attributes:
        id: Auto-increment row id.
        session_id: The owning session's id.
        role: ``"user"``, ``"assistant"``, ``"system"`` or ``"tool"``.
        content: The message text.
        intent: Classified intent for the turn, if known.
        is_private: Whether this message was flagged private (kept local-only).
        timestamp: Unix epoch seconds when recorded.
    """

    id: int
    session_id: str
    role: str
    content: str
    intent: Optional[str]
    is_private: bool
    timestamp: float

    def to_message(self) -> Message:
        """Convert to an LLM :class:`~mimosa.llm.base_provider.Message`."""
        try:
            role = Role(self.role)
        except ValueError:  # pragma: no cover - defensive against bad rows
            role = Role.USER
        return Message(role=role, content=self.content)

    def to_dict(self) -> Dict:
        return {
            "id": self.id,
            "session_id": self.session_id,
            "role": self.role,
            "content": self.content,
            "intent": self.intent,
            "is_private": self.is_private,
            "timestamp": self.timestamp,
        }


@dataclass
class StoredSession:
    """Metadata for one conversation session."""

    session_id: str
    created_at: float
    updated_at: float
    title: Optional[str]
    turn_count: int

    def to_dict(self) -> Dict:
        return {
            "session_id": self.session_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "title": self.title,
            "turn_count": self.turn_count,
        }


class ConversationStore:
    """SQLite-backed durable conversation history.

    Args:
        db_path: Path to the SQLite file. ``None`` uses the default under the
            user data dir; ``":memory:"`` keeps everything in RAM (tests).
    """

    def __init__(self, db_path: Optional[Union[str, Path]] = None) -> None:
        if db_path is None:
            db_path = conversations_db_path()
        self._is_memory = str(db_path) == ":memory:"
        self.db_path = str(db_path)
        self._lock = threading.RLock()

        if not self._is_memory:
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)

        # check_same_thread=False so the connection can be shared across the GTK
        # main loop and background worker threads; our own lock serialises use.
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._init_schema()

    # -- schema ------------------------------------------------------------

    def _init_schema(self) -> None:
        with self._lock:
            cur = self._conn.cursor()
            cur.executescript(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id  TEXT PRIMARY KEY,
                    created_at  REAL NOT NULL,
                    updated_at  REAL NOT NULL,
                    title       TEXT,
                    turn_count  INTEGER NOT NULL DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS messages (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id  TEXT NOT NULL,
                    role        TEXT NOT NULL,
                    content     TEXT NOT NULL,
                    intent      TEXT,
                    is_private  INTEGER NOT NULL DEFAULT 0,
                    timestamp   REAL NOT NULL,
                    FOREIGN KEY (session_id) REFERENCES sessions(session_id)
                        ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_messages_session
                    ON messages(session_id);
                CREATE INDEX IF NOT EXISTS idx_messages_timestamp
                    ON messages(timestamp);
                """
            )
            self._conn.commit()
            self._migrate(cur)

    def _migrate(self, cur: sqlite3.Cursor) -> None:
        """Apply forward migrations based on the ``user_version`` pragma."""
        current = cur.execute("PRAGMA user_version").fetchone()[0]
        if current < SCHEMA_VERSION:
            # No destructive migrations yet; just stamp the version. Future
            # schema changes append ``if current < N:`` blocks here.
            cur.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            self._conn.commit()

    # -- sessions ----------------------------------------------------------

    def ensure_session(self, session_id: str, title: Optional[str] = None) -> str:
        """Create the session row if absent; return its id.

        Idempotent: an existing session is left untouched (its title is only
        set when provided and currently empty).
        """
        now = time.time()
        with self._lock:
            row = self._conn.execute(
                "SELECT session_id, title FROM sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            if row is None:
                self._conn.execute(
                    "INSERT INTO sessions (session_id, created_at, updated_at, "
                    "title, turn_count) VALUES (?, ?, ?, ?, 0)",
                    (session_id, now, now, title),
                )
            elif title and not row["title"]:
                self._conn.execute(
                    "UPDATE sessions SET title = ? WHERE session_id = ?",
                    (title, session_id),
                )
            self._conn.commit()
        return session_id

    def set_session_title(self, session_id: str, title: str) -> None:
        """Set (overwrite) a session's human-readable title."""
        with self._lock:
            self.ensure_session(session_id)
            self._conn.execute(
                "UPDATE sessions SET title = ? WHERE session_id = ?",
                (title, session_id),
            )
            self._conn.commit()

    def get_session(self, session_id: str) -> Optional[StoredSession]:
        """Return a session's metadata, or ``None`` if unknown."""
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
        return self._row_to_session(row) if row else None

    def list_sessions(self, limit: int = 50) -> List[StoredSession]:
        """Return recent sessions, most-recently-updated first."""
        limit = max(1, int(limit))
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM sessions ORDER BY updated_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._row_to_session(r) for r in rows]

    # -- writing -----------------------------------------------------------

    def add_message(
        self,
        session_id: str,
        role: Union[str, Role],
        content: str,
        *,
        intent: Optional[str] = None,
        is_private: bool = False,
        timestamp: Optional[float] = None,
    ) -> int:
        """Persist a single message row; returns its row id.

        Empty/whitespace-only content is skipped (returns ``-1``) so callers can
        blindly forward optional assistant replies.
        """
        if not (content or "").strip():
            return -1
        role_str = role.value if isinstance(role, Role) else str(role)
        ts = time.time() if timestamp is None else float(timestamp)
        with self._lock:
            self.ensure_session(session_id)
            cur = self._conn.execute(
                "INSERT INTO messages (session_id, role, content, intent, "
                "is_private, timestamp) VALUES (?, ?, ?, ?, ?, ?)",
                (session_id, role_str, content, intent, 1 if is_private else 0, ts),
            )
            self._conn.execute(
                "UPDATE sessions SET updated_at = ? WHERE session_id = ?",
                (ts, session_id),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def add_turn(
        self,
        session_id: str,
        user_text: str,
        assistant_text: str = "",
        *,
        intent: Optional[str] = None,
        is_private: bool = False,
        timestamp: Optional[float] = None,
    ) -> List[int]:
        """Persist a full turn (user message + optional assistant reply).

        Increments the session's ``turn_count`` by one. Returns the list of
        inserted message row ids (1 or 2 entries).
        """
        ts = time.time() if timestamp is None else float(timestamp)
        ids: List[int] = []
        with self._lock:
            self.ensure_session(session_id)
            uid = self.add_message(
                session_id, Role.USER, user_text,
                intent=intent, is_private=is_private, timestamp=ts,
            )
            if uid != -1:
                ids.append(uid)
            aid = self.add_message(
                session_id, Role.ASSISTANT, assistant_text,
                intent=intent, is_private=is_private, timestamp=ts,
            )
            if aid != -1:
                ids.append(aid)
            # A turn is counted whenever at least one side was recorded.
            if ids:
                self._conn.execute(
                    "UPDATE sessions SET turn_count = turn_count + 1, "
                    "updated_at = ? WHERE session_id = ?",
                    (ts, session_id),
                )
                self._conn.commit()
        return ids

    # -- reading -----------------------------------------------------------

    def get_messages(
        self,
        session_id: str,
        *,
        include_private: bool = True,
        limit: Optional[int] = None,
    ) -> List[StoredMessage]:
        """Return a session's messages, oldest first.

        Args:
            session_id: Which session to read.
            include_private: When ``False``, private messages are filtered out
                (used when building cloud-bound context).
            limit: Optional cap keeping the most recent ``limit`` messages.
        """
        query = "SELECT * FROM messages WHERE session_id = ?"
        params: List = [session_id]
        if not include_private:
            query += " AND is_private = 0"
        query += " ORDER BY id ASC"
        with self._lock:
            rows = self._conn.execute(query, params).fetchall()
        msgs = [self._row_to_message(r) for r in rows]
        if limit is not None and limit >= 0:
            msgs = msgs[-limit:]
        return msgs

    def get_recent_messages(
        self,
        *,
        session_id: Optional[str] = None,
        limit: int = 20,
        include_private: bool = True,
    ) -> List[StoredMessage]:
        """Return the most recent messages (optionally within one session).

        Results are returned oldest-first (chronological) even though they are
        selected by recency, so they can be spliced straight into a prompt.
        """
        limit = max(1, int(limit))
        query = "SELECT * FROM messages"
        clauses: List[str] = []
        params: List = []
        if session_id is not None:
            clauses.append("session_id = ?")
            params.append(session_id)
        if not include_private:
            clauses.append("is_private = 0")
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        with self._lock:
            rows = self._conn.execute(query, params).fetchall()
        rows = list(reversed(rows))
        return [self._row_to_message(r) for r in rows]

    def get_context_messages(
        self,
        session_id: str,
        *,
        max_messages: int = 20,
        include_private: bool = True,
    ) -> List[Message]:
        """Return recent history as LLM :class:`Message` objects (oldest first).

        This mirrors
        :meth:`mimosa.core.conversation_manager.ConversationManager.get_context_messages`
        but sourced from durable storage, so context survives restarts. When
        building cloud-bound prompts, pass ``include_private=False`` to keep
        sensitive turns on-device.
        """
        stored = self.get_recent_messages(
            session_id=session_id, limit=max_messages, include_private=include_private
        )
        return [m.to_message() for m in stored]

    def search(
        self,
        query: str,
        *,
        limit: int = 20,
        include_private: bool = False,
    ) -> List[StoredMessage]:
        """Substring search across message content, most recent first.

        Private messages are excluded by default so a casual "what did we talk
        about?" recall never surfaces sensitive content unless explicitly asked.
        """
        text = (query or "").strip()
        if not text:
            return []
        limit = max(1, int(limit))
        sql = "SELECT * FROM messages WHERE content LIKE ? ESCAPE '\\'"
        if not include_private:
            sql += " AND is_private = 0"
        sql += " ORDER BY id DESC LIMIT ?"
        like = "%" + text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        with self._lock:
            rows = self._conn.execute(sql, (like, limit)).fetchall()
        return [self._row_to_message(r) for r in rows]

    # -- maintenance -------------------------------------------------------

    def count_messages(self, session_id: Optional[str] = None) -> int:
        """Total number of stored messages (optionally within one session)."""
        with self._lock:
            if session_id is None:
                row = self._conn.execute(
                    "SELECT COUNT(*) AS n FROM messages"
                ).fetchone()
            else:
                row = self._conn.execute(
                    "SELECT COUNT(*) AS n FROM messages WHERE session_id = ?",
                    (session_id,),
                ).fetchone()
        return int(row["n"])

    def delete_session(self, session_id: str) -> int:
        """Delete a session and its messages. Returns rows removed (messages)."""
        with self._lock:
            n = self.count_messages(session_id)
            self._conn.execute(
                "DELETE FROM messages WHERE session_id = ?", (session_id,)
            )
            self._conn.execute(
                "DELETE FROM sessions WHERE session_id = ?", (session_id,)
            )
            self._conn.commit()
        return n

    def purge_older_than(self, days: float) -> int:
        """Delete messages older than ``days`` and prune now-empty sessions.

        Implements the data-retention policy from
        :class:`mimosa.utils.config.PrivacySettings` (``data_retention_days``).
        ``days <= 0`` is a no-op (keep until the user clears it). Returns the
        number of messages deleted.
        """
        if days is None or days <= 0:
            return 0
        cutoff = time.time() - float(days) * 86400.0
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM messages WHERE timestamp < ?", (cutoff,)
            )
            deleted = cur.rowcount or 0
            # Recompute turn counts and drop sessions left with no messages.
            self._conn.execute(
                """
                UPDATE sessions SET turn_count = (
                    SELECT COUNT(*) FROM messages
                    WHERE messages.session_id = sessions.session_id
                )
                """
            )
            self._conn.execute(
                """
                DELETE FROM sessions WHERE session_id NOT IN (
                    SELECT DISTINCT session_id FROM messages
                )
                """
            )
            self._conn.commit()
        return int(deleted)

    def vacuum(self) -> bool:
        """Reclaim free space by running SQLite ``VACUUM``.

        Called periodically (see :class:`mimosa.core.runtime.AppServices`) after
        retention purges so the on-disk database does not keep growing. No-op
        for in-memory databases. Returns ``True`` on success, ``False`` if the
        operation failed (defensive: maintenance should never crash the app).
        """
        if self._is_memory:
            return False
        with self._lock:
            try:
                self._conn.execute("VACUUM")
                self._conn.commit()
                return True
            except Exception:  # pragma: no cover - defensive
                return False

    def clear_all(self) -> None:
        """Remove every session and message (factory reset of history)."""
        with self._lock:
            self._conn.execute("DELETE FROM messages")
            self._conn.execute("DELETE FROM sessions")
            self._conn.commit()

    def close(self) -> None:
        """Close the underlying connection. Safe to call more than once."""
        with self._lock:
            try:
                self._conn.close()
            except Exception:  # pragma: no cover - defensive
                pass

    # -- context-manager sugar --------------------------------------------

    def __enter__(self) -> "ConversationStore":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- helpers -----------------------------------------------------------

    @staticmethod
    def _row_to_message(row: sqlite3.Row) -> StoredMessage:
        return StoredMessage(
            id=row["id"],
            session_id=row["session_id"],
            role=row["role"],
            content=row["content"],
            intent=row["intent"],
            is_private=bool(row["is_private"]),
            timestamp=row["timestamp"],
        )

    @staticmethod
    def _row_to_session(row: sqlite3.Row) -> StoredSession:
        return StoredSession(
            session_id=row["session_id"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            title=row["title"],
            turn_count=row["turn_count"],
        )

    def __repr__(self) -> str:  # pragma: no cover - trivial
        loc = ":memory:" if self._is_memory else self.db_path
        return f"ConversationStore(path={loc!r})"

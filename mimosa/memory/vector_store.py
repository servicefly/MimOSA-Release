"""Vector store for MimOSA's onboarding & long-term memory (M6).

This module gives the onboarding/profile subsystem a single, friendly entry
point -- :class:`MemoryVectorStore` -- that manages several logical *collections*
of memories, each stored as semantic (vector) embeddings so MimOSA can recall
them by *meaning* later:

* ``user_profile``        -- structured facts about the user (occupation, skills…)
* ``conversation_history``-- snippets of past conversations
* ``learned_preferences`` -- preferences MimOSA infers over time
* ``episodic_memories``   -- specific events / moments worth remembering

Design goals
------------
* **Local-first** -- everything lives on-device under
  ``~/.local/share/mimosa/memory/chroma/`` (see
  :func:`mimosa.memory.paths.memory_chroma_dir`). Nothing is sent anywhere.
* **Graceful degradation** -- the heavy "good" backend is **Chroma**, but it is
  optional. We build on :class:`mimosa.memory.semantic_memory.SemanticMemory`,
  which already falls back to a deterministic, dependency-free in-process store
  when Chroma / sentence-transformers are absent. So this class works
  everywhere, and tests stay hermetic.
* **Never raises on the happy paths** -- storage/recall failures are logged and
  swallowed; callers always get a sensible value.

Each collection is a separate :class:`SemanticMemory` instance pointed at its
own persistent sub-store, so embeddings from different collections never mix.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, List, Optional, Union

from mimosa.memory.paths import memory_chroma_dir
from mimosa.memory.semantic_memory import (
    HAS_CHROMA,
    SemanticMemory,
    SemanticResult,
)

logger = logging.getLogger(__name__)

#: The canonical collection names. Exposed so callers/tests don't hard-code
#: strings and typos fail loudly.
COLLECTION_USER_PROFILE = "user_profile"
COLLECTION_CONVERSATION = "conversation_history"
COLLECTION_PREFERENCES = "learned_preferences"
COLLECTION_EPISODIC = "episodic_memories"

DEFAULT_COLLECTIONS = (
    COLLECTION_USER_PROFILE,
    COLLECTION_CONVERSATION,
    COLLECTION_PREFERENCES,
    COLLECTION_EPISODIC,
)

__all__ = [
    "MemoryVectorStore",
    "HAS_CHROMA",
    "COLLECTION_USER_PROFILE",
    "COLLECTION_CONVERSATION",
    "COLLECTION_PREFERENCES",
    "COLLECTION_EPISODIC",
    "DEFAULT_COLLECTIONS",
]


class MemoryVectorStore:
    """Manage MimOSA's onboarding/long-term memory collections.

    Args:
        persist_dir: Directory for durable storage. Defaults to
            :func:`mimosa.memory.paths.memory_chroma_dir`. Pass ``None``
            explicitly to keep everything in memory (handy for tests).
        embedder: Optional shared embedder callable (``str -> List[float]``)
            forwarded to every collection. ``None`` lets each
            :class:`SemanticMemory` pick its own (model if available, else the
            deterministic hashing fallback).
        use_chroma: Force-disable Chroma by passing ``False`` (tests use this to
            exercise the fallback path even when Chroma is installed).
        collections: Iterable of collection names to create. Defaults to
            :data:`DEFAULT_COLLECTIONS`.
    """

    def __init__(
        self,
        persist_dir: Optional[Union[str, Path]] = "__default__",
        *,
        embedder=None,
        use_chroma: Optional[bool] = None,
        collections=DEFAULT_COLLECTIONS,
    ) -> None:
        if persist_dir == "__default__":
            try:
                persist_dir = memory_chroma_dir()
            except Exception:  # pragma: no cover - defensive
                persist_dir = None
        self.persist_dir = Path(persist_dir).expanduser() if persist_dir else None
        self._embedder = embedder
        self._use_chroma = use_chroma
        self._collections: Dict[str, SemanticMemory] = {}
        for name in collections:
            self._collections[name] = self._make_collection(name)
        self.backend = next(
            (c.backend for c in self._collections.values()), "fallback"
        )

    # -- construction ------------------------------------------------------

    def _make_collection(self, name: str) -> SemanticMemory:
        """Build one :class:`SemanticMemory` for ``name`` (never raises)."""
        sub_dir = None
        if self.persist_dir is not None:
            sub_dir = self.persist_dir / name
        try:
            return SemanticMemory(
                persist_dir=sub_dir,
                embedder=self._embedder,
                collection_name=name,
                use_chroma=self._use_chroma,
            )
        except Exception:  # pragma: no cover - defensive
            logger.exception("Could not init memory collection %r; using memory", name)
            return SemanticMemory(
                persist_dir=None,
                embedder=self._embedder,
                collection_name=name,
                use_chroma=False,
            )

    # -- access ------------------------------------------------------------

    def collection(self, name: str) -> SemanticMemory:
        """Return the :class:`SemanticMemory` for ``name``.

        Unknown names are created lazily so the store stays forgiving.
        """
        mem = self._collections.get(name)
        if mem is None:
            mem = self._make_collection(name)
            self._collections[name] = mem
        return mem

    @property
    def collection_names(self) -> List[str]:
        return list(self._collections.keys())

    # -- writing -----------------------------------------------------------

    def add(
        self,
        collection: str,
        text: str,
        *,
        metadata: Optional[Dict] = None,
        doc_id: Optional[str] = None,
    ) -> Optional[str]:
        """Store ``text`` in ``collection``. Returns the doc id (or ``None``).

        Never raises -- a storage failure is logged and ``None`` is returned.
        """
        try:
            return self.collection(collection).add(
                text, metadata=metadata, doc_id=doc_id
            )
        except Exception:  # pragma: no cover - defensive
            logger.exception("Memory add failed for collection %r", collection)
            return None

    def add_fact(self, key: str, value, *, source: str = "") -> Optional[str]:
        """Convenience: store a single profile *fact* as a readable snippet.

        The snippet ("key: value") is what gets embedded, while the structured
        ``key``/``value`` are kept in metadata for exact lookup/consolidation.
        A stable ``doc_id`` derived from the key means re-storing the same fact
        updates rather than duplicates it.
        """
        text_value = ", ".join(map(str, value)) if isinstance(value, (list, tuple)) else str(value)
        snippet = f"{key}: {text_value}".strip()
        meta = {"kind": "fact", "key": str(key), "value": text_value}
        if source:
            meta["source"] = source
        doc_id = f"fact::{str(key).strip().lower().replace(' ', '_')}"
        return self.add(COLLECTION_USER_PROFILE, snippet, metadata=meta, doc_id=doc_id)

    def add_conversation_turn(
        self, user_text: str, assistant_text: str = "", *, topic: str = ""
    ) -> Optional[str]:
        """Store a conversation turn in the conversation-history collection."""
        try:
            meta = {"kind": "turn"}
            if topic:
                meta["topic"] = topic
            return self.collection(COLLECTION_CONVERSATION).add_turn(
                user_text, assistant_text, intent=topic or None
            )
        except Exception:  # pragma: no cover - defensive
            logger.exception("Memory add_conversation_turn failed")
            return None

    def add_preference(self, text: str, *, confidence: float = 0.0) -> Optional[str]:
        """Store an inferred preference snippet."""
        meta = {"kind": "preference"}
        if confidence:
            meta["confidence"] = float(confidence)
        return self.add(COLLECTION_PREFERENCES, text, metadata=meta)

    def add_episode(self, text: str, *, when: str = "") -> Optional[str]:
        """Store an episodic memory (a specific event/moment)."""
        meta = {"kind": "episode"}
        if when:
            meta["when"] = when
        return self.add(COLLECTION_EPISODIC, text, metadata=meta)

    # -- reading -----------------------------------------------------------

    def query(
        self,
        collection: str,
        text: str,
        *,
        n_results: int = 5,
        min_score: float = 0.0,
    ) -> List[SemanticResult]:
        """Return the most semantically-similar items in ``collection``."""
        try:
            return self.collection(collection).query(
                text, n_results=n_results, min_score=min_score
            )
        except Exception:  # pragma: no cover - defensive
            logger.exception("Memory query failed for collection %r", collection)
            return []

    def search_all(
        self, text: str, *, n_results: int = 3, min_score: float = 0.0
    ) -> Dict[str, List[SemanticResult]]:
        """Query every collection and return a ``{collection: results}`` map."""
        out: Dict[str, List[SemanticResult]] = {}
        for name in self._collections:
            out[name] = self.query(
                name, text, n_results=n_results, min_score=min_score
            )
        return out

    # -- maintenance -------------------------------------------------------

    def count(self, collection: Optional[str] = None) -> int:
        """Total items in ``collection`` (or across all collections)."""
        try:
            if collection is not None:
                return self.collection(collection).count()
            return sum(c.count() for c in self._collections.values())
        except Exception:  # pragma: no cover - defensive
            return 0

    def reset(self, collection: Optional[str] = None) -> None:
        """Clear one collection, or *all* of them (the privacy "nuclear" option).

        Never raises -- best-effort wipe.
        """
        try:
            if collection is not None:
                self.collection(collection).reset()
            else:
                for c in self._collections.values():
                    try:
                        c.reset()
                    except Exception:  # pragma: no cover - keep wiping the rest
                        logger.warning("Reset failed for a collection")
        except Exception:  # pragma: no cover - defensive
            logger.exception("Memory reset failed")

    def close(self) -> None:  # pragma: no cover - trivial
        for c in self._collections.values():
            try:
                c.close()
            except Exception:
                pass

    def __enter__(self) -> "MemoryVectorStore":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return (
            f"MemoryVectorStore(backend={self.backend!r}, "
            f"collections={self.collection_names}, count={self.count()})"
        )

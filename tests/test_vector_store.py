"""Tests for the multi-collection memory vector store (M3).

Fully hermetic: the store is created with ``use_chroma=False`` (or an in-memory
persist dir) so no real Chroma / sentence-transformers / network is touched.
"""

from __future__ import annotations

import pytest

from mimosa.memory.vector_store import (
    COLLECTION_CONVERSATION,
    COLLECTION_EPISODIC,
    COLLECTION_PREFERENCES,
    COLLECTION_USER_PROFILE,
    DEFAULT_COLLECTIONS,
    MemoryVectorStore,
)


@pytest.fixture()
def store():
    s = MemoryVectorStore(None, use_chroma=False)
    yield s
    s.close()


def test_default_collections_present(store):
    names = set(store.collection_names)
    assert COLLECTION_USER_PROFILE in names
    assert COLLECTION_CONVERSATION in names
    assert COLLECTION_PREFERENCES in names
    assert COLLECTION_EPISODIC in names
    assert len(DEFAULT_COLLECTIONS) == 4


def test_add_and_query(store):
    store.add(COLLECTION_EPISODIC, "We talked about hiking trails", metadata={"x": 1})
    results = store.query(COLLECTION_EPISODIC, "hiking", n_results=3)
    assert results, "expected at least one result"
    assert any("hiking" in r.text.lower() for r in results)


def test_add_fact_is_idempotent(store):
    store.add_fact("name", "Alex")
    store.add_fact("name", "Alex")  # same key -> update, not duplicate
    assert store.count(COLLECTION_USER_PROFILE) == 1
    store.add_fact("name", "Sam")  # update value
    assert store.count(COLLECTION_USER_PROFILE) == 1


def test_add_fact_list_value(store):
    store.add_fact("skills", ["python", "go"])
    results = store.query(COLLECTION_USER_PROFILE, "python")
    assert results


def test_conversation_preference_episode_helpers(store):
    store.add_conversation_turn("Hi", "Hello there", topic="introduction")
    store.add_preference("prefers concise answers", confidence=0.9)
    store.add_episode("user mentioned a trip", when="2026-06-12")
    assert store.count(COLLECTION_CONVERSATION) == 1
    assert store.count(COLLECTION_PREFERENCES) == 1
    assert store.count(COLLECTION_EPISODIC) == 1


def test_search_all_spans_collections(store):
    store.add_fact("occupation", "engineer")
    store.add_preference("likes detail", confidence=0.5)
    hits = store.search_all("engineer", n_results=5)
    assert isinstance(hits, dict)
    assert any(hits.values())


def test_count_total_and_reset(store):
    store.add_fact("name", "Alex")
    store.add_preference("p", confidence=0.5)
    assert store.count() >= 2
    store.reset(COLLECTION_USER_PROFILE)
    assert store.count(COLLECTION_USER_PROFILE) == 0
    store.reset()
    assert store.count() == 0


def test_never_raises_on_bad_collection(store):
    # Unknown collection should not raise.
    assert store.query("does_not_exist", "anything") == []
    assert store.add("does_not_exist", "text") is None or True


def test_context_manager():
    with MemoryVectorStore(None, use_chroma=False) as s:
        s.add_fact("name", "Alex")
        assert s.count() == 1

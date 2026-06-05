"""Tests for the SQLite conversation store (M5.1 — Conversation Persistence)."""

from __future__ import annotations

import time

import pytest

from mimosa.llm.base_provider import Message, Role
from mimosa.memory.conversation_store import (
    ConversationStore,
    StoredMessage,
    StoredSession,
)


@pytest.fixture
def store():
    s = ConversationStore(":memory:")
    yield s
    s.close()


# -- sessions --------------------------------------------------------------


def test_ensure_session_idempotent(store):
    store.ensure_session("s1")
    store.ensure_session("s1")
    sessions = store.list_sessions()
    assert len([x for x in sessions if x.session_id == "s1"]) == 1


def test_ensure_session_sets_title_only_when_empty(store):
    store.ensure_session("s1", title="First")
    store.ensure_session("s1", title="Second")
    assert store.get_session("s1").title == "First"


def test_set_session_title_overwrites(store):
    store.ensure_session("s1", title="First")
    store.set_session_title("s1", "Renamed")
    assert store.get_session("s1").title == "Renamed"


def test_get_unknown_session_returns_none(store):
    assert store.get_session("nope") is None


def test_list_sessions_orders_by_recency(store):
    store.add_turn("s1", "a", "b", timestamp=100.0)
    store.add_turn("s2", "c", "d", timestamp=200.0)
    ids = [s.session_id for s in store.list_sessions()]
    assert ids[0] == "s2"


def test_list_sessions_respects_limit(store):
    for i in range(5):
        store.add_turn(f"s{i}", "u", "a")
    assert len(store.list_sessions(limit=2)) == 2


# -- messages / turns ------------------------------------------------------


def test_add_turn_creates_two_messages(store):
    store.add_turn("s1", "hello", "hi")
    assert store.count_messages("s1") == 2


def test_add_turn_increments_turn_count(store):
    store.add_turn("s1", "one", "1")
    store.add_turn("s1", "two", "2")
    assert store.get_session("s1").turn_count == 2


def test_add_turn_skips_empty_assistant(store):
    store.add_turn("s1", "hello", "")
    msgs = store.get_messages("s1")
    assert len(msgs) == 1
    assert msgs[0].role == "user"


def test_add_turn_empty_user_keeps_assistant(store):
    store.add_turn("s1", "", "just a reply")
    msgs = store.get_messages("s1")
    assert len(msgs) == 1
    assert msgs[0].role == "assistant"


def test_add_message_skips_blank(store):
    rid = store.add_message("s1", Role.USER, "   ")
    assert rid == -1
    assert store.count_messages("s1") == 0


def test_add_message_accepts_role_enum_and_str(store):
    store.add_message("s1", Role.USER, "via enum")
    store.add_message("s1", "assistant", "via str")
    roles = [m.role for m in store.get_messages("s1")]
    assert roles == ["user", "assistant"]


def test_messages_ordered_oldest_first(store):
    store.add_message("s1", Role.USER, "first", timestamp=1.0)
    store.add_message("s1", Role.ASSISTANT, "second", timestamp=2.0)
    contents = [m.content for m in store.get_messages("s1")]
    assert contents == ["first", "second"]


def test_intent_persisted(store):
    store.add_turn("s1", "what time", "noon", intent="time")
    assert all(m.intent == "time" for m in store.get_messages("s1"))


# -- privacy flag ----------------------------------------------------------


def test_private_messages_filtered_when_requested(store):
    store.add_turn("s1", "public q", "public a")
    store.add_turn("s1", "secret", "shh", is_private=True)
    public_only = store.get_messages("s1", include_private=False)
    assert all(not m.is_private for m in public_only)
    assert len(public_only) == 2


def test_private_included_by_default(store):
    store.add_turn("s1", "secret", "shh", is_private=True)
    assert len(store.get_messages("s1")) == 2


def test_is_private_roundtrips_as_bool(store):
    store.add_message("s1", Role.USER, "x", is_private=True)
    m = store.get_messages("s1")[0]
    assert m.is_private is True and isinstance(m.is_private, bool)


# -- recent / context ------------------------------------------------------


def test_get_recent_messages_limit_and_order(store):
    for i in range(10):
        store.add_message("s1", Role.USER, f"m{i}")
    recent = store.get_recent_messages(session_id="s1", limit=3)
    assert [m.content for m in recent] == ["m7", "m8", "m9"]


def test_get_recent_messages_across_sessions(store):
    store.add_message("s1", Role.USER, "a")
    store.add_message("s2", Role.USER, "b")
    recent = store.get_recent_messages(limit=5)
    assert {m.content for m in recent} == {"a", "b"}


def test_get_context_messages_returns_llm_messages(store):
    store.add_turn("s1", "hello", "hi")
    ctx = store.get_context_messages("s1")
    assert all(isinstance(m, Message) for m in ctx)
    assert ctx[0].role == Role.USER
    assert ctx[1].role == Role.ASSISTANT


def test_get_context_excludes_private_when_asked(store):
    store.add_turn("s1", "pub", "pub")
    store.add_turn("s1", "priv", "priv", is_private=True)
    ctx = store.get_context_messages("s1", include_private=False)
    assert len(ctx) == 2


# -- search ----------------------------------------------------------------


def test_search_finds_substring(store):
    store.add_turn("s1", "let us talk about python", "sure")
    hits = store.search("python")
    assert any("python" in m.content for m in hits)


def test_search_excludes_private_by_default(store):
    store.add_turn("s1", "my secret password stuff", "ok", is_private=True)
    assert store.search("secret") == []
    assert len(store.search("secret", include_private=True)) >= 1


def test_search_empty_query_returns_empty(store):
    store.add_turn("s1", "something", "ok")
    assert store.search("   ") == []


def test_search_escapes_like_wildcards(store):
    store.add_turn("s1", "100% complete", "great")
    store.add_turn("s1", "nothing here", "ok")
    # A literal "%" must not act as a wildcard matching everything.
    hits = store.search("100%")
    assert len(hits) == 1


# -- maintenance -----------------------------------------------------------


def test_delete_session_removes_messages(store):
    store.add_turn("s1", "a", "b")
    removed = store.delete_session("s1")
    assert removed == 2
    assert store.get_session("s1") is None
    assert store.count_messages("s1") == 0


def test_purge_older_than_deletes_old(store):
    old = time.time() - 10 * 86400
    store.add_turn("s1", "old", "msg", timestamp=old)
    store.add_turn("s2", "new", "msg")
    deleted = store.purge_older_than(days=5)
    assert deleted == 2
    assert store.get_session("s1") is None
    assert store.get_session("s2") is not None


def test_purge_zero_days_is_noop(store):
    store.add_turn("s1", "x", "y", timestamp=time.time() - 99 * 86400)
    assert store.purge_older_than(0) == 0
    assert store.count_messages("s1") == 2


def test_clear_all(store):
    store.add_turn("s1", "a", "b")
    store.add_turn("s2", "c", "d")
    store.clear_all()
    assert store.count_messages() == 0
    assert store.list_sessions() == []


def test_count_messages_total_vs_session(store):
    store.add_turn("s1", "a", "b")
    store.add_turn("s2", "c", "d")
    assert store.count_messages() == 4
    assert store.count_messages("s1") == 2


# -- persistence across connections ---------------------------------------


def test_data_survives_reopen(tmp_path):
    db = tmp_path / "conv.db"
    s1 = ConversationStore(db)
    s1.add_turn("s1", "remember me", "ok")
    s1.close()

    s2 = ConversationStore(db)
    assert s2.count_messages("s1") == 2
    assert s2.search("remember", include_private=True)
    s2.close()


def test_creates_parent_dir(tmp_path):
    db = tmp_path / "nested" / "dir" / "conv.db"
    s = ConversationStore(db)
    s.add_turn("s1", "a", "b")
    assert db.exists()
    s.close()


def test_context_manager_closes(tmp_path):
    db = tmp_path / "conv.db"
    with ConversationStore(db) as s:
        s.add_turn("s1", "a", "b")
    # Reopen confirms the data flushed/committed.
    with ConversationStore(db) as s2:
        assert s2.count_messages("s1") == 2


# -- dataclasses -----------------------------------------------------------


def test_stored_message_to_message():
    sm = StoredMessage(1, "s1", "assistant", "hi", None, False, 1.0)
    msg = sm.to_message()
    assert msg.role == Role.ASSISTANT and msg.content == "hi"


def test_stored_message_bad_role_defaults_user():
    sm = StoredMessage(1, "s1", "weird", "x", None, False, 1.0)
    assert sm.to_message().role == Role.USER


def test_to_dict_shapes():
    sm = StoredMessage(1, "s1", "user", "hi", "greet", True, 1.0)
    assert sm.to_dict()["is_private"] is True
    ss = StoredSession("s1", 1.0, 2.0, "t", 3)
    assert ss.to_dict()["turn_count"] == 3

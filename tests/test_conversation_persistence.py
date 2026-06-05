"""Tests for ConversationManager ↔ ConversationStore integration (M5.1)."""

from __future__ import annotations

import pytest

from mimosa.core.conversation_manager import ConversationManager, Turn
from mimosa.memory.conversation_store import ConversationStore


@pytest.fixture
def store():
    s = ConversationStore(":memory:")
    yield s
    s.close()


# -- backward compatibility (no store) ------------------------------------


def test_manager_without_store_unchanged():
    cm = ConversationManager(max_history=3)
    cm.add_turn("hi", "hello")
    assert cm.turn_count == 1
    assert cm.store is None


def test_add_turn_returns_turn_and_buffers():
    cm = ConversationManager()
    t = cm.add_turn("hi", "hello", intent="greeting")
    assert isinstance(t, Turn)
    assert cm.turns[-1].assistant_text == "hello"


def test_history_bound_still_enforced():
    cm = ConversationManager(max_history=2)
    cm.add_turn("a", "1")
    cm.add_turn("b", "2")
    cm.add_turn("c", "3")
    assert cm.turn_count == 2
    assert cm.turns[0].user_text == "b"


# -- persistence -----------------------------------------------------------


def test_complete_turn_persisted(store):
    cm = ConversationManager(session_id="s1", store=store)
    cm.add_turn("hello", "hi")
    assert store.count_messages("s1") == 2


def test_session_registered_on_construct(store):
    ConversationManager(session_id="s1", store=store)
    assert store.get_session("s1") is not None


def test_user_then_reply_persists_once(store):
    cm = ConversationManager(session_id="s1", store=store)
    cm.add_turn("what time is it")          # user only -> pending
    assert store.count_messages("s1") == 0  # not yet persisted
    cm.update_last_response("It is noon.", intent="time")
    msgs = store.get_messages("s1")
    assert [m.role for m in msgs] == ["user", "assistant"]
    assert store.get_session("s1").turn_count == 1


def test_pending_turn_flushed_on_next_add(store):
    cm = ConversationManager(session_id="s1", store=store)
    cm.add_turn("user only utterance")  # pending
    cm.add_turn("second", "reply")      # should flush the first
    # first turn (user only) + second turn (user+assistant) = 3 messages
    assert store.count_messages("s1") == 3


def test_flush_persists_pending(store):
    cm = ConversationManager(session_id="s1", store=store)
    cm.add_turn("dangling user turn")
    assert store.count_messages("s1") == 0
    cm.flush()
    assert store.count_messages("s1") == 1


def test_no_duplicate_on_double_flush(store):
    cm = ConversationManager(session_id="s1", store=store)
    cm.add_turn("hi", "hello")
    cm.flush()
    cm.flush()
    assert store.count_messages("s1") == 2


def test_update_last_response_without_prior_turn(store):
    cm = ConversationManager(session_id="s1", store=store)
    cm.update_last_response("orphan reply")
    assert cm.turn_count == 1
    assert store.count_messages("s1") == 1


# -- private turns ---------------------------------------------------------


def test_private_turn_persisted_by_default(store):
    cm = ConversationManager(session_id="s1", store=store)
    cm.add_turn("secret", "ok", is_private=True)
    assert store.count_messages("s1") == 2
    assert all(m.is_private for m in store.get_messages("s1"))


def test_private_turn_withheld_when_disabled(store):
    cm = ConversationManager(session_id="s1", store=store, persist_private=False)
    cm.add_turn("secret", "ok", is_private=True)
    # Stays in the live buffer but not on disk.
    assert cm.turn_count == 1
    assert store.count_messages("s1") == 0


def test_private_flag_in_buffer():
    cm = ConversationManager()
    cm.add_turn("secret", "ok", is_private=True)
    assert cm.turns[-1].is_private is True


# -- rehydration -----------------------------------------------------------


def test_load_from_store_rebuilds_turns(store):
    cm = ConversationManager(session_id="s1", store=store)
    cm.add_turn("hello", "hi")
    cm.add_turn("bye", "goodbye")

    cm2 = ConversationManager(session_id="s1", store=store)
    loaded = cm2.load_from_store()
    assert loaded == 2
    assert cm2.turns[0].user_text == "hello"
    assert cm2.turns[1].assistant_text == "goodbye"


def test_load_from_store_respects_max(store):
    cm = ConversationManager(session_id="s1", store=store)
    for i in range(5):
        cm.add_turn(f"u{i}", f"a{i}")
    cm2 = ConversationManager(session_id="s1", store=store, max_history=2)
    assert cm2.load_from_store() == 2
    assert cm2.turns[-1].user_text == "u4"


def test_load_from_store_no_store_returns_zero():
    cm = ConversationManager(session_id="s1")
    assert cm.load_from_store() == 0


def test_loaded_turns_marked_persisted(store):
    cm = ConversationManager(session_id="s1", store=store)
    cm.add_turn("hi", "hello")
    cm2 = ConversationManager(session_id="s1", store=store)
    cm2.load_from_store()
    # Re-flushing loaded turns must not duplicate rows.
    cm2.flush()
    assert store.count_messages("s1") == 2


def test_context_messages_after_reload(store):
    cm = ConversationManager(session_id="s1", store=store)
    cm.add_turn("what time", "noon")
    cm2 = ConversationManager(session_id="s1", store=store)
    cm2.load_from_store()
    ctx = cm2.get_context_messages()
    assert [m.content for m in ctx] == ["what time", "noon"]


# -- lifecycle -------------------------------------------------------------


def test_reset_session_new_id_and_registers(store):
    cm = ConversationManager(session_id="s1", store=store)
    cm.add_turn("a", "b")
    new_id = cm.reset_session()
    assert new_id != "s1"
    assert cm.turn_count == 0
    assert store.get_session(new_id) is not None
    # Old session's data preserved on disk.
    assert store.count_messages("s1") == 2


def test_clear_flushes_then_empties(store):
    cm = ConversationManager(session_id="s1", store=store)
    cm.add_turn("dangling")  # pending
    cm.clear()
    assert cm.turn_count == 0
    assert store.count_messages("s1") == 1  # flushed before clearing


def test_to_memory_records_includes_privacy_flag():
    cm = ConversationManager(session_id="s1")
    cm.add_turn("secret", "ok", is_private=True)
    rec = cm.to_memory_records()[0]
    assert rec["is_private"] is True
    assert rec["session_id"] == "s1"


def test_store_failure_is_non_fatal(monkeypatch, store):
    cm = ConversationManager(session_id="s1", store=store)

    def boom(*a, **k):
        raise RuntimeError("disk full")

    monkeypatch.setattr(store, "add_turn", boom)
    # Should not raise even though persistence fails.
    cm.add_turn("hello", "hi")
    assert cm.turn_count == 1

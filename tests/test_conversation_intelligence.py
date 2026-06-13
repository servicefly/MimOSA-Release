"""Tests for conversation intelligence: emotion + reference resolution (M4)."""

from __future__ import annotations

from mimosa.core.conversation_manager import (
    ConversationManager,
    detect_emotion,
    EMOTION_NEUTRAL,
    EMOTION_FRUSTRATED,
    EMOTION_EXCITED,
    EMOTION_STRESSED,
)


def test_detect_emotion_neutral():
    assert detect_emotion("What time is it?") == EMOTION_NEUTRAL


def test_detect_emotion_frustrated():
    assert detect_emotion("Ugh, this is not working and I'm so frustrated") == EMOTION_FRUSTRATED


def test_detect_emotion_excited():
    assert detect_emotion("This is amazing, I love it!") == EMOTION_EXCITED


def test_detect_emotion_stressed():
    assert detect_emotion("I'm so overwhelmed and stressed about this deadline") == EMOTION_STRESSED


def test_detect_emotion_empty():
    assert detect_emotion("") == EMOTION_NEUTRAL
    assert detect_emotion(None) == EMOTION_NEUTRAL  # type: ignore[arg-type]


def test_last_user_text():
    cm = ConversationManager()
    cm.add_turn("first", "reply")
    cm.add_turn("second", "reply")
    assert cm.last_user_text() == "second"


def test_recent_user_texts():
    cm = ConversationManager()
    for i in range(5):
        cm.add_turn(f"msg{i}", "reply")
    recent = cm.recent_user_texts(3)
    assert recent == ["msg2", "msg3", "msg4"]


def test_has_reference_true():
    assert ConversationManager.has_reference("can you close it?") is True


def test_has_reference_false():
    assert ConversationManager.has_reference("open firefox") is False


def test_resolve_references_appends_hint():
    cm = ConversationManager()
    cm.add_turn("open firefox", "Sure, opening Firefox")
    cm.add_turn("can you close it?", "")
    resolved = cm.resolve_references("can you close it?")
    assert "referring to" in resolved.lower()
    assert "firefox" in resolved.lower()


def test_resolve_references_no_reference_unchanged():
    cm = ConversationManager()
    cm.add_turn("open firefox", "ok")
    assert cm.resolve_references("open vscode") == "open vscode"


def test_resolve_references_no_history():
    cm = ConversationManager()
    # Only the current turn -> no prior referent.
    cm.add_turn("close it", "")
    assert cm.resolve_references("close it") == "close it"

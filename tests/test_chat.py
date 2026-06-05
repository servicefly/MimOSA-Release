"""Tests for the optional chat-window logic (M4.3).

Fully offline / hermetic: a fake router stands in for the real
:class:`IntentRouter`, so no skills, LLM, audio or display are involved.
"""

from __future__ import annotations

import pytest

from mimosa.core.conversation_manager import ConversationManager
from mimosa.ui.chat_logic import (
    DEFAULT_MAX_MESSAGES,
    ROLE_ASSISTANT,
    ROLE_SYSTEM,
    ROLE_USER,
    ChatController,
    ChatMessage,
)


class FakeResult:
    def __init__(self, text, success=True, intent=None):
        self.text = text
        self.success = success
        self.metadata = {"intent": intent} if intent else {}


class FakeRouter:
    """Records calls and returns a scripted result."""

    def __init__(self, reply="ok", success=True, intent="time", raises=False):
        self.reply = reply
        self.success = success
        self.intent = intent
        self.raises = raises
        self.calls = []

    def route(self, text, context=None):
        self.calls.append((text, context))
        if self.raises:
            raise RuntimeError("router boom")
        return FakeResult(self.reply, self.success, self.intent)


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        self.t += 1.0
        return self.t


# -- basic send flow ---------------------------------------------------------


class TestSend:
    def test_send_records_user_and_assistant(self):
        router = FakeRouter(reply="The time is 3pm.", intent="time")
        ctrl = ChatController(router=router, conversation=ConversationManager())
        reply = ctrl.send("what time is it")
        assert isinstance(reply, ChatMessage)
        assert reply.role == ROLE_ASSISTANT
        assert reply.text == "The time is 3pm."
        assert reply.intent == "time"
        roles = [m.role for m in ctrl.messages]
        assert roles == [ROLE_USER, ROLE_ASSISTANT]

    def test_send_passes_context_to_router(self):
        conv = ConversationManager()
        conv.add_turn("hello", "hi there", intent="greeting")
        router = FakeRouter()
        ctrl = ChatController(router=router, conversation=conv)
        ctrl.send("again")
        text, context = router.calls[0]
        assert text == "again"
        assert context is not None  # context messages were supplied

    def test_send_updates_shared_conversation(self):
        conv = ConversationManager()
        router = FakeRouter(reply="hi", intent="greeting")
        ctrl = ChatController(router=router, conversation=conv)
        ctrl.send("hello")
        assert conv.turn_count == 1
        assert conv.turns[0].user_text == "hello"
        assert conv.turns[0].assistant_text == "hi"

    @pytest.mark.parametrize("blank", ["", "   ", "\n\t", None])
    def test_blank_input_ignored(self, blank):
        router = FakeRouter()
        ctrl = ChatController(router=router)
        assert ctrl.send(blank) is None
        assert ctrl.message_count == 0
        assert router.calls == []

    def test_input_is_stripped(self):
        router = FakeRouter()
        ctrl = ChatController(router=router)
        ctrl.send("  hello  ")
        assert ctrl.messages[0].text == "hello"
        assert router.calls[0][0] == "hello"


# -- error handling ----------------------------------------------------------


class TestErrors:
    def test_no_router_graceful_reply(self):
        ctrl = ChatController(router=None)
        reply = ctrl.send("hi")
        assert reply.role == ROLE_ASSISTANT
        assert reply.success is False
        assert "isn't connected" in reply.text

    def test_router_exception_surfaced_as_failure(self):
        router = FakeRouter(raises=True)
        ctrl = ChatController(router=router, conversation=ConversationManager())
        reply = ctrl.send("explode")
        assert reply.success is False
        assert "something went wrong" in reply.text.lower()
        # user message still recorded
        assert ctrl.messages[0].role == ROLE_USER

    def test_unsuccessful_result_marks_failure(self):
        router = FakeRouter(reply="couldn't do that", success=False)
        ctrl = ChatController(router=router, conversation=ConversationManager())
        reply = ctrl.send("do x")
        assert reply.success is False
        assert reply.text == "couldn't do that"


# -- log management ----------------------------------------------------------


class TestLogManagement:
    def test_system_message(self):
        ctrl = ChatController()
        msg = ctrl.add_system_message("Welcome!")
        assert msg.role == ROLE_SYSTEM
        assert ctrl.messages[-1].text == "Welcome!"

    def test_clear_keeps_conversation(self):
        conv = ConversationManager()
        ctrl = ChatController(router=FakeRouter(), conversation=conv)
        ctrl.send("hello")
        ctrl.clear()
        assert ctrl.message_count == 0
        assert conv.turn_count == 1  # conversation memory untouched

    def test_reset_clears_both(self):
        conv = ConversationManager()
        ctrl = ChatController(router=FakeRouter(), conversation=conv)
        ctrl.send("hello")
        ctrl.reset()
        assert ctrl.message_count == 0
        assert conv.turn_count == 0

    def test_max_messages_trim(self):
        ctrl = ChatController(router=FakeRouter(reply="r"), max_messages=4)
        for i in range(5):
            ctrl.send(f"msg{i}")  # each send adds 2 messages
        assert ctrl.message_count == 4
        # newest retained
        assert ctrl.messages[-1].role == ROLE_ASSISTANT

    def test_max_messages_floor(self):
        ctrl = ChatController(max_messages=0)
        ctrl.add_system_message("a")
        ctrl.add_system_message("b")
        assert ctrl.message_count == 1  # clamped to at least 1

    def test_to_transcript(self):
        router = FakeRouter(reply="Hi!", intent="greeting")
        ctrl = ChatController(router=router, conversation=ConversationManager())
        ctrl.add_system_message("Session started")
        ctrl.send("hello")
        transcript = ctrl.to_transcript()
        assert "—: Session started" in transcript
        assert "You: hello" in transcript
        assert "MimOSA: Hi!" in transcript


# -- misc --------------------------------------------------------------------


class TestMisc:
    def test_lazy_conversation_created(self):
        ctrl = ChatController(router=FakeRouter())
        assert ctrl.conversation is not None
        assert isinstance(ctrl.conversation, ConversationManager)

    def test_set_router_after_construction(self):
        ctrl = ChatController(router=None)
        assert ctrl.has_router is False
        ctrl.set_router(FakeRouter(reply="now connected"))
        assert ctrl.has_router is True
        reply = ctrl.send("hi")
        assert reply.text == "now connected"

    def test_default_max_messages_constant(self):
        ctrl = ChatController()
        assert ctrl._max_messages == DEFAULT_MAX_MESSAGES

    def test_timestamps_use_injected_clock(self):
        ctrl = ChatController(router=FakeRouter(), clock=Clock(t=10.0))
        ctrl.send("hi")
        # user ts then assistant ts, both from clock (monotonic increasing)
        assert ctrl.messages[0].timestamp < ctrl.messages[1].timestamp


# -- GTK shell (headless degradation) ----------------------------------------


class TestChatShellHeadless:
    def test_open_chat_window_returns_none_headless(self):
        from mimosa.ui import chat_window

        if chat_window.HAS_GTK:
            pytest.skip("GTK present; headless degradation not applicable")
        assert chat_window.ChatWindow is None
        assert chat_window.open_chat_window() is None

"""Tests for mimosa.ui.state_bridge -- voice->UI state marshaling.

Hermetic: a fake GLib runs idle_add callbacks synchronously so transitions can
be asserted without a GTK main loop. A fake voice loop verifies subscription.
"""

import pytest

from mimosa.ui.state_bridge import StateBridge, UIState
from mimosa.voice.voice_loop import VoiceState


class FakeGLib:
    """Runs idle_add callbacks immediately (synchronously)."""

    def __init__(self):
        self.calls = []

    def idle_add(self, fn, *args):
        self.calls.append((fn, args))
        return fn(*args)


class FakeVoiceLoop:
    """Minimal stand-in exposing the state-listener API."""

    def __init__(self):
        self.listeners = []

    def add_state_listener(self, cb):
        self.listeners.append(cb)

    def remove_state_listener(self, cb):
        self.listeners.remove(cb)

    def emit(self, state):
        for cb in list(self.listeners):
            cb(state)


class TestUIStateMapping:
    @pytest.mark.parametrize(
        "voice,expected",
        [
            (VoiceState.IDLE, UIState.IDLE),
            (VoiceState.LISTENING, UIState.LISTENING),
            (VoiceState.PROCESSING, UIState.PROCESSING),
            (VoiceState.SPEAKING, UIState.SPEAKING),
            (VoiceState.STOPPED, UIState.DISABLED),
        ],
    )
    def test_from_voice_state_enum(self, voice, expected):
        assert UIState.from_voice_state(voice) == expected

    def test_from_raw_string(self):
        assert UIState.from_voice_state("listening") == UIState.LISTENING
        assert UIState.from_voice_state("SPEAKING") == UIState.SPEAKING

    def test_unknown_maps_to_idle(self):
        assert UIState.from_voice_state("wat") == UIState.IDLE
        assert UIState.from_voice_state(None) == UIState.IDLE


class TestNotify:
    def test_notify_dispatches_via_glib(self):
        glib = FakeGLib()
        seen = []
        b = StateBridge(on_state_change=seen.append, glib=glib)
        b.notify(VoiceState.LISTENING)
        assert seen == [UIState.LISTENING]
        assert b.current_state == UIState.LISTENING
        assert len(glib.calls) == 1

    def test_notify_without_glib_dispatches_synchronously(self):
        seen = []
        b = StateBridge(on_state_change=seen.append, glib=None)
        # Force the no-GLib path regardless of host.
        b._glib = None
        b.notify(VoiceState.PROCESSING)
        assert seen == [UIState.PROCESSING]

    def test_uses_glib_flag(self):
        assert StateBridge(glib=FakeGLib()).uses_glib is True
        b = StateBridge()
        b._glib = None
        assert b.uses_glib is False

    def test_callback_exception_is_swallowed(self):
        def boom(_state):
            raise RuntimeError("ui blew up")

        b = StateBridge(on_state_change=boom, glib=FakeGLib())
        # Must not raise -- the voice loop must never be affected.
        b.notify(VoiceState.SPEAKING)
        assert b.current_state == UIState.SPEAKING

    def test_no_callback_is_safe(self):
        b = StateBridge(on_state_change=None, glib=FakeGLib())
        b.notify(VoiceState.IDLE)  # no error
        assert b.current_state == UIState.IDLE

    def test_idle_add_failure_falls_back_to_sync(self):
        class BadGLib:
            def idle_add(self, fn, *a):
                raise RuntimeError("no main loop")

        seen = []
        b = StateBridge(on_state_change=seen.append, glib=BadGLib())
        b.notify(VoiceState.LISTENING)
        assert seen == [UIState.LISTENING]


class TestSubscription:
    def test_subscribe_registers_listener(self):
        loop = FakeVoiceLoop()
        seen = []
        b = StateBridge(on_state_change=seen.append, glib=FakeGLib())
        b.subscribe(loop)
        assert b.notify in loop.listeners
        loop.emit(VoiceState.LISTENING)
        assert seen == [UIState.LISTENING]

    def test_unsubscribe_removes_listener(self):
        loop = FakeVoiceLoop()
        b = StateBridge(glib=FakeGLib())
        b.subscribe(loop)
        b.unsubscribe()
        assert b.notify not in loop.listeners

    def test_subscribe_none_is_noop(self):
        b = StateBridge(glib=FakeGLib())
        b.subscribe(None)  # no error
        b.unsubscribe()  # no error

    def test_subscribe_to_real_voice_loop(self):
        """End-to-end: a real VoiceLoop state change reaches the UI callback."""
        from mimosa.voice.voice_loop import VoiceLoop

        loop = VoiceLoop()
        seen = []
        b = StateBridge(on_state_change=seen.append, glib=FakeGLib())
        b.subscribe(loop)
        loop._set_state(VoiceState.LISTENING)
        loop._set_state(VoiceState.PROCESSING)
        assert seen == [UIState.LISTENING, UIState.PROCESSING]

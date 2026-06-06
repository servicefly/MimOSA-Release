"""Tests for mimosa.ui.app and VoiceLoop state-listener hooks.

The GUI path needs a live display, so these tests focus on the headless
dispatch, CLI parsing, and the voice-loop integration seam. GTK is never
imported here.
"""

import pytest

from mimosa.ui import app as appmod
from mimosa.ui.app import MimOSAApplication, build_arg_parser, main
from mimosa.voice.voice_loop import VoiceLoop, VoiceState


class FakeVoiceLoop:
    def __init__(self):
        self.ran = False
        self.stopped = False
        self.listeners = []

    def run(self):
        self.ran = True

    def stop(self):
        self.stopped = True

    def add_state_listener(self, cb):
        self.listeners.append(cb)

    def remove_state_listener(self, cb):
        if cb in self.listeners:
            self.listeners.remove(cb)


class TestVoiceLoopListeners:
    def test_add_and_notify(self):
        loop = VoiceLoop()
        seen = []
        loop.add_state_listener(seen.append)
        loop._set_state(VoiceState.LISTENING)
        assert seen == [VoiceState.LISTENING]

    def test_no_duplicate_listeners(self):
        loop = VoiceLoop()
        cb = lambda s: None
        loop.add_state_listener(cb)
        loop.add_state_listener(cb)
        assert loop._state_listeners.count(cb) == 1

    def test_remove_listener(self):
        loop = VoiceLoop()
        seen = []
        loop.add_state_listener(seen.append)
        loop.remove_state_listener(seen.append) if False else None
        # remove the actual registered callable
        cb = loop._state_listeners[0]
        loop.remove_state_listener(cb)
        loop._set_state(VoiceState.SPEAKING)
        assert seen == []

    def test_remove_absent_is_safe(self):
        loop = VoiceLoop()
        loop.remove_state_listener(lambda s: None)  # no error

    def test_listener_exception_does_not_break_loop(self):
        loop = VoiceLoop()
        good = []

        def boom(_):
            raise RuntimeError("listener fail")

        loop.add_state_listener(boom)
        loop.add_state_listener(good.append)
        # Must not raise; the good listener still fires.
        loop._set_state(VoiceState.PROCESSING)
        assert good == [VoiceState.PROCESSING]

    def test_no_notify_when_state_unchanged(self):
        loop = VoiceLoop()
        seen = []
        loop.add_state_listener(seen.append)
        loop._set_state(VoiceState.IDLE)  # already IDLE
        assert seen == []


class TestArgParser:
    def test_no_gui_flag(self):
        args = build_arg_parser().parse_args(["--no-gui"])
        assert args.no_gui is True

    def test_check_flag(self):
        args = build_arg_parser().parse_args(["--check"])
        assert args.check is True

    def test_defaults(self):
        args = build_arg_parser().parse_args([])
        assert args.no_gui is False
        assert args.check is False
        assert args.verbose is False


class TestDispatch:
    def test_force_headless_runs_headless(self):
        loop = FakeVoiceLoop()
        app = MimOSAApplication(voice_loop=loop, force_headless=True)
        rc = app.run()
        assert rc == 0
        assert loop.ran is True

    def test_auto_headless_when_no_gui(self, monkeypatch):
        monkeypatch.setattr(appmod, "is_gui_available", lambda: False)
        loop = FakeVoiceLoop()
        app = MimOSAApplication(voice_loop=loop)
        rc = app.run()
        assert rc == 0
        assert loop.ran is True

    def test_run_headless_calls_shutdown(self, monkeypatch):
        loop = FakeVoiceLoop()
        app = MimOSAApplication(voice_loop=loop, force_headless=True)
        app.run()
        assert loop.stopped is True

    def test_shutdown_idempotent(self):
        loop = FakeVoiceLoop()
        app = MimOSAApplication(voice_loop=loop)
        app.shutdown()
        app.shutdown()  # no error
        assert loop.stopped is True


class TestMain:
    def test_check_returns_zero(self, capsys):
        rc = main(["--check"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "MimOSA environment" in out

    def test_main_no_gui_dispatches_headless(self, monkeypatch):
        ran = {"v": False}

        class Loop:
            def run(self):
                ran["v"] = True

            def stop(self):
                pass

        # Patch VoiceLoop so MimOSAApplication builds our fake.
        monkeypatch.setattr(
            "mimosa.voice.voice_loop.VoiceLoop", lambda *a, **k: Loop()
        )
        rc = main(["--no-gui"])
        assert rc == 0
        assert ran["v"] is True

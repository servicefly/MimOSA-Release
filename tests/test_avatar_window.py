"""GTK4 avatar-window tests.

These require both GTK 4 and a display server, so the whole module is skipped
when :func:`is_gui_available` is false (e.g. headless CI without Xvfb). When a
display is present, the window is built inside a real ``Gtk.Application``
activation and exercised, with a timer-driven quit so the test can never hang.

Run under Xvfb in CI, e.g.::

    xvfb-run -a python -m pytest tests/test_avatar_window.py
"""

import pytest

from mimosa.ui.environment import is_gui_available

pytestmark = pytest.mark.skipif(
    not is_gui_available(), reason="requires GTK 4 and a display server"
)

from mimosa.ui.state_bridge import UIState  # noqa: E402
from mimosa.ui.ui_config import UIConfig  # noqa: E402


def _run_app(activate_fn, timeout_ms=600):
    """Run a short-lived Gtk.Application, invoking ``activate_fn(app, window?)``."""
    import gi

    gi.require_version("Gtk", "4.0")
    from gi.repository import GLib, Gtk

    result = {"error": None}

    def on_activate(app):
        try:
            activate_fn(app)
        except Exception as exc:  # capture for assertion on the main thread
            result["error"] = exc
        finally:
            GLib.timeout_add(timeout_ms, lambda: (app.quit(), False)[1])

    app = Gtk.Application(application_id="ai.mimosa.Test")
    app.connect("activate", on_activate)
    app.run(None)
    return result


class TestAvatarWindow:
    def test_window_builds_and_animates(self):
        from mimosa.ui.avatar_window import AvatarWindow, HAS_GTK

        assert HAS_GTK is True
        captured = {}

        def activate(app):
            win = AvatarWindow(application=app, config=UIConfig(size=160))
            win.present()
            captured["win"] = win
            # animation timer should be installed
            assert win._anim_source is not None

        res = _run_app(activate)
        assert res["error"] is None

    def test_set_state_and_audio_level(self):
        from mimosa.ui.avatar_window import AvatarWindow

        def activate(app):
            win = AvatarWindow(application=app, config=UIConfig(size=160))
            win.present()
            for st in (UIState.LISTENING, UIState.PROCESSING, UIState.SPEAKING, UIState.IDLE):
                win.set_state(st)
            win.set_audio_level(0.5)
            assert win.renderer.state == UIState.IDLE

        res = _run_app(activate)
        assert res["error"] is None

    def test_quit_action_invokes_callback(self):
        from mimosa.ui.avatar_window import AvatarWindow

        flag = {"quit": False}

        def activate(app):
            win = AvatarWindow(
                application=app,
                config=UIConfig(size=160),
                on_quit=lambda: flag.update(quit=True),
            )
            win.present()
            win._action_quit()

        res = _run_app(activate)
        assert res["error"] is None
        assert flag["quit"] is True

    def test_stop_animation_is_safe(self):
        from mimosa.ui.avatar_window import AvatarWindow

        def activate(app):
            win = AvatarWindow(application=app, config=UIConfig(size=160))
            win.present()
            win.stop_animation()
            win.stop_animation()  # idempotent
            assert win._anim_source is None

        res = _run_app(activate)
        assert res["error"] is None

"""GTK4 settings-dialog tests (M3.3).

Like :mod:`tests.test_avatar_window`, the whole module is skipped unless GTK 4
and a display server are present. The dialog is built inside a real
``Gtk.Application`` activation with a timer-driven quit so the test can never
hang. These verify the *view* wiring; the heavy logic is covered headlessly by
:mod:`tests.test_settings_logic`.

Run under Xvfb in CI::

    xvfb-run -a python -m pytest tests/test_settings_dialog.py
"""

import pytest

from mimosa.ui.environment import is_gui_available

pytestmark = pytest.mark.skipif(
    not is_gui_available(), reason="requires GTK 4 and a display server"
)

from mimosa.utils.config import AppConfigManager  # noqa: E402


def _run_app(activate_fn, timeout_ms=600):
    import gi

    gi.require_version("Gtk", "4.0")
    from gi.repository import GLib, Gtk

    result = {"error": None}

    def on_activate(app):
        try:
            activate_fn(app)
        except Exception as exc:  # capture for main-thread assertion
            result["error"] = exc
        finally:
            GLib.timeout_add(timeout_ms, lambda: (app.quit(), False)[1])

    app = Gtk.Application(application_id="ai.mimosa.SettingsTest")
    app.connect("activate", on_activate)
    app.run(None)
    return result


def _manager(tmp_path, monkeypatch):
    monkeypatch.setenv("MIMOSA_CONFIG", str(tmp_path / "settings.json"))
    monkeypatch.setenv("MIMOSA_UI_CONFIG", str(tmp_path / "ui.json"))
    m = AppConfigManager()
    m.load()
    return m


class TestSettingsDialog:
    def test_dialog_builds_all_pages(self, tmp_path, monkeypatch):
        from mimosa.ui.settings_dialog import HAS_GTK, SettingsDialog

        assert HAS_GTK is True
        m = _manager(tmp_path, monkeypatch)
        captured = {}

        def activate(app):
            dlg = SettingsDialog(manager=m, system_summary="Test System")
            dlg.set_application(app)
            dlg.present()
            captured["readers"] = len(dlg._readers)
            captured["pages"] = [
                dlg.stack.get_child_by_name(p.page_id) is not None
                for p in dlg.controller.pages
            ]

        res = _run_app(activate)
        assert res["error"] is None
        assert all(captured["pages"])
        assert captured["readers"] > 0

    def test_apply_persists_via_dialog(self, tmp_path, monkeypatch):
        from mimosa.ui.settings_dialog import SettingsDialog

        m = _manager(tmp_path, monkeypatch)

        def activate(app):
            dlg = SettingsDialog(manager=m)
            dlg.set_application(app)
            dlg.present()
            # Drive the real provider dropdown widget, then hit Apply.
            from mimosa.utils.config import LLM_PROVIDERS
            combo = dlg._widgets[("privacy", "llm_provider")]
            combo.set_selected(LLM_PROVIDERS.index("none"))
            dlg._on_apply(None)

        res = _run_app(activate)
        assert res["error"] is None
        assert m.get().privacy.llm_provider == "none"
        assert (tmp_path / "settings.json").exists()

    def test_open_settings_dialog_helper(self, tmp_path, monkeypatch):
        from mimosa.ui.settings_dialog import open_settings_dialog

        m = _manager(tmp_path, monkeypatch)
        captured = {}

        def activate(app):
            dlg = open_settings_dialog(m, system_summary="X")
            if dlg is not None:
                dlg.set_application(app)
            captured["dlg"] = dlg

        res = _run_app(activate)
        assert res["error"] is None
        assert captured["dlg"] is not None

    def test_clear_history_button_uses_hook(self, tmp_path, monkeypatch):
        from mimosa.ui.settings_dialog import open_settings_dialog

        m = _manager(tmp_path, monkeypatch)
        calls = {"n": 0}

        def activate(app):
            dlg = open_settings_dialog(
                m, on_clear_history=lambda: calls.__setitem__("n", 3) or 3)
            dlg.set_application(app)
            dlg.present()
            dlg._on_clear_history(None)

        res = _run_app(activate)
        assert res["error"] is None
        assert calls["n"] == 3

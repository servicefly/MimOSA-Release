"""Tests for the system-tray companion logic (M4.3).

Fully offline / hermetic: exercises the pure :class:`TrayController`; no
GTK/AppIndicator, display or audio devices are touched.
"""

from __future__ import annotations

import pytest

from mimosa.ui.state_bridge import UIState
from mimosa.ui.tray_logic import (
    ITEM_OPEN_CHAT,
    ITEM_QUIT,
    ITEM_SETTINGS,
    ITEM_STATUS,
    ITEM_TOGGLE_AVATAR,
    ITEM_TOGGLE_MUTE,
    KIND_LABEL,
    KIND_SEPARATOR,
    KIND_TOGGLE,
    TrayCallbacks,
    TrayController,
    TrayMenuItem,
)


class Recorder:
    """Counts callback invocations."""

    def __init__(self):
        self.calls = []

    def make(self, name):
        def _cb():
            self.calls.append(name)
        return _cb


@pytest.fixture
def recorder():
    return Recorder()


@pytest.fixture
def wired(recorder):
    cbs = TrayCallbacks(
        on_show_avatar=recorder.make("show"),
        on_hide_avatar=recorder.make("hide"),
        on_open_chat=recorder.make("chat"),
        on_mute=recorder.make("mute"),
        on_unmute=recorder.make("unmute"),
        on_open_settings=recorder.make("settings"),
        on_quit=recorder.make("quit"),
    )
    return TrayController(cbs)


# -- state & presentation ----------------------------------------------------


class TestPresentation:
    def test_defaults(self):
        ctrl = TrayController()
        assert ctrl.avatar_visible is True
        assert ctrl.muted is False
        assert ctrl.state is UIState.IDLE
        assert ctrl.status_label() == "Idle"

    @pytest.mark.parametrize(
        "state,label",
        [
            (UIState.LISTENING, "Listening…"),
            (UIState.PROCESSING, "Thinking…"),
            (UIState.SPEAKING, "Speaking…"),
            (UIState.DISABLED, "Stopped"),
        ],
    )
    def test_status_label_per_state(self, state, label):
        ctrl = TrayController()
        ctrl.set_state(state)
        assert ctrl.status_label() == label

    def test_set_state_accepts_voice_string(self):
        ctrl = TrayController()
        ctrl.set_state("speaking")
        assert ctrl.state is UIState.SPEAKING

    def test_icon_name_changes_with_state(self):
        ctrl = TrayController()
        assert ctrl.icon_name() == "mimosa-idle"
        ctrl.set_state(UIState.LISTENING)
        assert ctrl.icon_name() == "mimosa-listening"

    def test_tooltip_reflects_mute(self):
        ctrl = TrayController()
        assert "muted" not in ctrl.tooltip()
        ctrl.toggle_mute()
        assert "(muted)" in ctrl.tooltip()


# -- menu --------------------------------------------------------------------


class TestMenu:
    def test_menu_structure(self):
        ctrl = TrayController()
        items = ctrl.menu_items()
        assert all(isinstance(i, TrayMenuItem) for i in items)
        ids = [i.item_id for i in items]
        for expected in (
            ITEM_STATUS, ITEM_TOGGLE_AVATAR, ITEM_OPEN_CHAT,
            ITEM_TOGGLE_MUTE, ITEM_SETTINGS, ITEM_QUIT,
        ):
            assert expected in ids

    def test_status_item_is_disabled_label(self):
        ctrl = TrayController()
        status = next(i for i in ctrl.menu_items() if i.item_id == ITEM_STATUS)
        assert status.kind == KIND_LABEL
        assert status.enabled is False

    def test_has_separators(self):
        ctrl = TrayController()
        seps = [i for i in ctrl.menu_items() if i.kind == KIND_SEPARATOR]
        assert len(seps) >= 2

    def test_toggle_labels_reflect_state(self):
        ctrl = TrayController()
        avatar = next(i for i in ctrl.menu_items() if i.item_id == ITEM_TOGGLE_AVATAR)
        assert avatar.label == "Hide avatar"
        assert avatar.checked is True
        ctrl.toggle_avatar()
        avatar = next(i for i in ctrl.menu_items() if i.item_id == ITEM_TOGGLE_AVATAR)
        assert avatar.label == "Show avatar"
        assert avatar.checked is False

    def test_mute_label_reflects_state(self):
        ctrl = TrayController()
        mute = next(i for i in ctrl.menu_items() if i.item_id == ITEM_TOGGLE_MUTE)
        assert mute.kind == KIND_TOGGLE
        assert mute.label == "Mute microphone"
        ctrl.toggle_mute()
        mute = next(i for i in ctrl.menu_items() if i.item_id == ITEM_TOGGLE_MUTE)
        assert mute.label == "Unmute microphone"
        assert mute.checked is True


# -- activation --------------------------------------------------------------


class TestActivation:
    def test_toggle_avatar_invokes_callbacks(self, wired, recorder):
        assert wired.activate(ITEM_TOGGLE_AVATAR) is True
        assert wired.avatar_visible is False
        assert recorder.calls == ["hide"]
        wired.activate(ITEM_TOGGLE_AVATAR)
        assert wired.avatar_visible is True
        assert recorder.calls == ["hide", "show"]

    def test_toggle_mute_invokes_callbacks(self, wired, recorder):
        wired.activate(ITEM_TOGGLE_MUTE)
        assert wired.muted is True
        assert recorder.calls == ["mute"]
        wired.activate(ITEM_TOGGLE_MUTE)
        assert wired.muted is False
        assert recorder.calls == ["mute", "unmute"]

    def test_open_chat_settings_quit(self, wired, recorder):
        wired.activate(ITEM_OPEN_CHAT)
        wired.activate(ITEM_SETTINGS)
        wired.activate(ITEM_QUIT)
        assert recorder.calls == ["chat", "settings", "quit"]

    def test_unknown_item_returns_false(self, wired):
        assert wired.activate("bogus") is False

    def test_status_item_not_activatable(self, wired):
        assert wired.activate(ITEM_STATUS) is False

    def test_activation_without_callbacks_still_toggles(self):
        ctrl = TrayController()  # no callbacks wired
        assert ctrl.activate(ITEM_TOGGLE_AVATAR) is True
        assert ctrl.avatar_visible is False

    def test_callback_exception_is_swallowed(self):
        def boom():
            raise RuntimeError("nope")

        ctrl = TrayController(TrayCallbacks(on_quit=boom))
        # Should not raise.
        assert ctrl.activate(ITEM_QUIT) is True

    def test_set_helpers_sync_external_state(self):
        ctrl = TrayController()
        ctrl.set_avatar_visible(False)
        assert ctrl.avatar_visible is False
        ctrl.set_muted(True)
        assert ctrl.muted is True

    def test_initial_overrides(self):
        ctrl = TrayController(avatar_visible=False, muted=True)
        assert ctrl.avatar_visible is False
        assert ctrl.muted is True


# -- GTK shell (headless degradation) ----------------------------------------


class TestTrayShellHeadless:
    def test_create_system_tray_returns_none_headless(self):
        from mimosa.ui import tray

        if tray.HAS_GTK:
            pytest.skip("GTK present; headless degradation not applicable")
        assert tray.SystemTray is None
        assert tray.create_system_tray() is None
        assert tray.create_system_tray(TrayController()) is None

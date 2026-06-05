"""System-tray companion logic (M4.3).

The tray icon gives MimOSA a lightweight, always-available presence in the
desktop panel: a status indicator plus a menu to show/hide the avatar, open the
optional chat window, mute the microphone, reach Settings and quit.

As elsewhere in the UI package, *all* behaviour lives in a pure, GTK-free
controller (:class:`TrayController`) so it can be unit-tested headlessly.  The
matching GTK/AppIndicator shell (:mod:`mimosa.ui.tray`) is a thin adapter that
renders :meth:`TrayController.menu_items` and forwards activations to
:meth:`TrayController.activate`.

The controller owns no resources and performs no I/O -- callers wire desktop
actions in via injectable callbacks.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from mimosa.ui.state_bridge import UIState

logger = logging.getLogger(__name__)

Callback = Callable[[], None]

# Menu item identifiers (stable keys the GTK shell can map to widgets).
ITEM_STATUS = "status"
ITEM_TOGGLE_AVATAR = "toggle_avatar"
ITEM_OPEN_CHAT = "open_chat"
ITEM_TOGGLE_MUTE = "toggle_mute"
ITEM_SETTINGS = "settings"
ITEM_QUIT = "quit"

KIND_ACTION = "action"
KIND_TOGGLE = "toggle"
KIND_LABEL = "label"
KIND_SEPARATOR = "separator"

#: Human-readable status text per UI state, shown in the menu header & tooltip.
_STATUS_LABELS: Dict[UIState, str] = {
    UIState.IDLE: "Idle",
    UIState.LISTENING: "Listening…",
    UIState.PROCESSING: "Thinking…",
    UIState.SPEAKING: "Speaking…",
    UIState.DISABLED: "Stopped",
}

#: Icon key per UI state (resolved to a real themed icon by the GTK shell).
_ICON_NAMES: Dict[UIState, str] = {
    UIState.IDLE: "mimosa-idle",
    UIState.LISTENING: "mimosa-listening",
    UIState.PROCESSING: "mimosa-processing",
    UIState.SPEAKING: "mimosa-speaking",
    UIState.DISABLED: "mimosa-disabled",
}


@dataclass(frozen=True)
class TrayMenuItem:
    """A declarative tray-menu entry rendered by the GTK shell."""

    item_id: str
    label: str
    kind: str = KIND_ACTION
    enabled: bool = True
    checked: Optional[bool] = None  # only meaningful for KIND_TOGGLE


@dataclass
class TrayCallbacks:
    """Optional desktop actions wired in by the application.

    Every callback is optional; missing ones simply make the corresponding
    menu entry a no-op (still toggles internal state where relevant).
    """

    on_show_avatar: Optional[Callback] = None
    on_hide_avatar: Optional[Callback] = None
    on_open_chat: Optional[Callback] = None
    on_mute: Optional[Callback] = None
    on_unmute: Optional[Callback] = None
    on_open_settings: Optional[Callback] = None
    on_quit: Optional[Callback] = None


class TrayController:
    """Pure-logic model behind the system-tray companion."""

    def __init__(
        self,
        callbacks: Optional[TrayCallbacks] = None,
        *,
        avatar_visible: bool = True,
        muted: bool = False,
    ) -> None:
        self._callbacks = callbacks or TrayCallbacks()
        self._avatar_visible = bool(avatar_visible)
        self._muted = bool(muted)
        self._state = UIState.IDLE

    # -- observable state --
    @property
    def avatar_visible(self) -> bool:
        return self._avatar_visible

    @property
    def muted(self) -> bool:
        return self._muted

    @property
    def state(self) -> UIState:
        return self._state

    def set_state(self, state) -> UIState:
        """Update the current UI state (drives status label / icon)."""
        if not isinstance(state, UIState):
            state = UIState.from_voice_state(state)
        self._state = state
        return self._state

    # -- presentation helpers --
    def status_label(self) -> str:
        return _STATUS_LABELS.get(self._state, "Idle")

    def icon_name(self) -> str:
        return _ICON_NAMES.get(self._state, "mimosa-idle")

    def tooltip(self) -> str:
        parts = [f"MimOSA — {self.status_label()}"]
        if self._muted:
            parts.append("(muted)")
        return " ".join(parts)

    def menu_items(self) -> List[TrayMenuItem]:
        """Return the current menu as an ordered list of declarative items."""
        return [
            TrayMenuItem(ITEM_STATUS, self.status_label(), KIND_LABEL, enabled=False),
            TrayMenuItem("sep1", "", KIND_SEPARATOR),
            TrayMenuItem(
                ITEM_TOGGLE_AVATAR,
                "Hide avatar" if self._avatar_visible else "Show avatar",
                KIND_TOGGLE,
                checked=self._avatar_visible,
            ),
            TrayMenuItem(ITEM_OPEN_CHAT, "Open chat…", KIND_ACTION),
            TrayMenuItem(
                ITEM_TOGGLE_MUTE,
                "Unmute microphone" if self._muted else "Mute microphone",
                KIND_TOGGLE,
                checked=self._muted,
            ),
            TrayMenuItem("sep2", "", KIND_SEPARATOR),
            TrayMenuItem(ITEM_SETTINGS, "Settings…", KIND_ACTION),
            TrayMenuItem("sep3", "", KIND_SEPARATOR),
            TrayMenuItem(ITEM_QUIT, "Quit MimOSA", KIND_ACTION),
        ]

    # -- activation --
    def activate(self, item_id: str) -> bool:
        """Handle a menu activation; returns ``True`` if recognised.

        Toggles internal state and invokes the matching callback (if wired).
        Never raises on callback errors -- they are logged and swallowed so a
        misbehaving handler can't take down the tray.
        """
        handler = {
            ITEM_TOGGLE_AVATAR: self.toggle_avatar,
            ITEM_OPEN_CHAT: self.open_chat,
            ITEM_TOGGLE_MUTE: self.toggle_mute,
            ITEM_SETTINGS: self.open_settings,
            ITEM_QUIT: self.quit,
        }.get(item_id)
        if handler is None:
            logger.debug("Tray activation ignored for item_id=%r", item_id)
            return False
        handler()
        return True

    # -- individual actions --
    def toggle_avatar(self) -> bool:
        """Flip avatar visibility; returns the new ``avatar_visible`` value."""
        self._avatar_visible = not self._avatar_visible
        cb = (
            self._callbacks.on_show_avatar
            if self._avatar_visible
            else self._callbacks.on_hide_avatar
        )
        self._safe_call(cb)
        return self._avatar_visible

    def set_avatar_visible(self, visible: bool) -> None:
        """Set visibility explicitly (used to sync with external changes)."""
        self._avatar_visible = bool(visible)

    def toggle_mute(self) -> bool:
        """Flip mute state; returns the new ``muted`` value."""
        self._muted = not self._muted
        cb = self._callbacks.on_mute if self._muted else self._callbacks.on_unmute
        self._safe_call(cb)
        return self._muted

    def set_muted(self, muted: bool) -> None:
        self._muted = bool(muted)

    def open_chat(self) -> None:
        self._safe_call(self._callbacks.on_open_chat)

    def open_settings(self) -> None:
        self._safe_call(self._callbacks.on_open_settings)

    def quit(self) -> None:
        self._safe_call(self._callbacks.on_quit)

    # -- internal --
    @staticmethod
    def _safe_call(cb: Optional[Callback]) -> None:
        if cb is None:
            return
        try:
            cb()
        except Exception:  # pragma: no cover - defensive
            logger.exception("Tray callback raised; ignoring.")

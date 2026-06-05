"""Optional chat window shell (M4.3) -- a thin view over ChatController.

All behaviour (routing, history, transcript) lives in the GTK-free
:class:`mimosa.ui.chat_logic.ChatController`; this module only renders the
message log as a scrolling list and forwards the entry text to
:meth:`ChatController.send`.

GTK must exist at *class definition* time, so the import is guarded: on a
headless machine :data:`HAS_GTK` is ``False`` and :data:`ChatWindow` is
``None``.  Use :func:`open_chat_window`, which returns ``None`` when GTK is
unavailable.
"""

from __future__ import annotations

import logging
from typing import Optional

from mimosa.ui.chat_logic import ROLE_ASSISTANT, ROLE_USER, ChatController

logger = logging.getLogger(__name__)

try:  # GTK is required to *define* the widget class.
    import gi

    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk

    HAS_GTK = True
except Exception:  # pragma: no cover - headless import path
    HAS_GTK = False


if HAS_GTK:

    class ChatWindow(Gtk.Window):  # pragma: no cover - exercised only under GTK
        """A minimal scrolling chat view with an input entry."""

        def __init__(
            self,
            controller: Optional[ChatController] = None,
            *,
            transient_for=None,
        ) -> None:
            super().__init__(title="MimOSA — Chat")
            self.controller = controller or ChatController()
            self.set_default_size(420, 520)
            if transient_for is not None:
                self.set_transient_for(transient_for)

            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            box.set_margin_top(8)
            box.set_margin_bottom(8)
            box.set_margin_start(8)
            box.set_margin_end(8)

            self._log = Gtk.TextView()
            self._log.set_editable(False)
            self._log.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
            scroller = Gtk.ScrolledWindow()
            scroller.set_vexpand(True)
            scroller.set_child(self._log)
            box.append(scroller)

            entry_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            self._entry = Gtk.Entry()
            self._entry.set_hexpand(True)
            self._entry.set_placeholder_text("Type a message…")
            self._entry.connect("activate", self._on_send)
            send_btn = Gtk.Button(label="Send")
            send_btn.connect("clicked", self._on_send)
            entry_row.append(self._entry)
            entry_row.append(send_btn)
            box.append(entry_row)

            self.set_child(box)
            self._render()

        def _on_send(self, *_args) -> None:
            text = self._entry.get_text()
            if not text.strip():
                return
            self.controller.send(text)
            self._entry.set_text("")
            self._render()

        def _render(self) -> None:
            buf = self._log.get_buffer()
            buf.set_text("")
            for m in self.controller.messages:
                who = {ROLE_USER: "You", ROLE_ASSISTANT: "MimOSA"}.get(m.role, "—")
                end = buf.get_end_iter()
                buf.insert(end, f"{who}: {m.text}\n")

else:  # headless
    ChatWindow = None  # type: ignore


def open_chat_window(
    controller: Optional[ChatController] = None,
    *,
    transient_for=None,
) -> Optional["ChatWindow"]:
    """Open the chat window when GTK is available, else return ``None``.

    Always safe to call; returns ``None`` on headless machines so the chat
    window is treated as an optional enhancement.
    """
    if not HAS_GTK:
        logger.info("GTK unavailable; chat window disabled.")
        return None
    try:  # pragma: no cover - GTK-only path
        window = ChatWindow(controller, transient_for=transient_for)
        window.present()
        return window
    except Exception:  # pragma: no cover - defensive
        logger.exception("Failed to open chat window.")
        return None

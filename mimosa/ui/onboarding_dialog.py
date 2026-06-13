"""GTK4 conversational onboarding dialog (M3) -- a thin view shell.

This window drives MimOSA's friendly "get to know you" chat. All real logic
lives in :mod:`mimosa.onboarding` (pure/testable); this module only renders the
conversation and forwards button presses, exactly like the other MimOSA
dialogs.

Features (M3 req #6):

* shows MimOSA's question and a running transcript;
* accepts answers by **text** (entry box) and, when a ``listen`` callable is
  supplied, by **voice**;
* speaks each prompt aloud when a ``speak`` callable is supplied;
* lets the user **skip** a topic, **pause** (saving resume state) or quit;
* shows progress ("Topic 3 of 7").

As with the rest of the UI, GTK must exist at *class definition* time, so the
import is guarded: on a headless machine :data:`HAS_GTK` is ``False`` and
:data:`OnboardingDialog` is ``None``. Callers should invoke
:func:`open_onboarding_dialog` unconditionally -- it returns ``None`` on
headless systems (after invoking ``on_close`` with a graceful result).
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

try:  # GTK is required to *define* the widget class.
    import gi

    gi.require_version("Gtk", "4.0")
    from gi.repository import GLib, Gtk

    HAS_GTK = True
except Exception:  # pragma: no cover - headless import path
    HAS_GTK = False


#: Result-callback signature: ``on_close(completed: bool)``.
CloseCallback = Callable[[bool], None]


if HAS_GTK:

    class OnboardingDialog(Gtk.Window):  # pragma: no cover - exercised only under GTK
        """A chat-style window that runs the onboarding conversation."""

        def __init__(
            self,
            manager: Any,
            *,
            transient_for=None,
            on_close: Optional[CloseCallback] = None,
            speak: Optional[Callable[[str], None]] = None,
            listen: Optional[Callable[[], Optional[str]]] = None,
        ) -> None:
            super().__init__(title="Let's get to know each other")
            self._manager = manager
            self._on_close = on_close
            self._speak = speak
            self._listen = listen
            self._closed = False
            self._completed = False

            self.set_modal(True)
            if transient_for is not None:
                self.set_transient_for(transient_for)
            self.set_default_size(560, 540)

            root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
            root.set_margin_top(16)
            root.set_margin_bottom(16)
            root.set_margin_start(16)
            root.set_margin_end(16)
            self.set_child(root)

            # Progress.
            self._progress = Gtk.ProgressBar()
            self._progress.set_show_text(True)
            root.append(self._progress)

            # Transcript (scrolling).
            scroller = Gtk.ScrolledWindow()
            scroller.set_vexpand(True)
            self._transcript_box = Gtk.Box(
                orientation=Gtk.Orientation.VERTICAL, spacing=8
            )
            scroller.set_child(self._transcript_box)
            root.append(scroller)

            # Input row.
            input_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            self._entry = Gtk.Entry()
            self._entry.set_hexpand(True)
            self._entry.set_placeholder_text("Type your answer…")
            self._entry.connect("activate", self._on_send_clicked)
            input_row.append(self._entry)

            send = Gtk.Button(label="Send")
            send.connect("clicked", self._on_send_clicked)
            input_row.append(send)

            if self._listen is not None:
                mic = Gtk.Button(label="🎤")
                mic.connect("clicked", self._on_listen_clicked)
                input_row.append(mic)
            root.append(input_row)

            # Action row.
            action_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            skip = Gtk.Button(label="Skip topic")
            skip.connect("clicked", self._on_skip_clicked)
            action_row.append(skip)

            pause = Gtk.Button(label="Pause & finish later")
            pause.connect("clicked", self._on_pause_clicked)
            action_row.append(pause)
            root.append(action_row)

            self._present_prompt(self._manager.begin())

        # -- rendering ----------------------------------------------------
        def _add_bubble(self, text: str, *, mine: bool) -> None:
            label = Gtk.Label(label=text)
            label.set_wrap(True)
            label.set_xalign(1.0 if mine else 0.0)
            label.set_halign(Gtk.Align.END if mine else Gtk.Align.START)
            prefix = "You: " if mine else "MimOSA: "
            label.set_text(prefix + text)
            self._transcript_box.append(label)

        def _present_prompt(self, prompt) -> None:
            text = getattr(prompt, "text", str(prompt))
            self._add_bubble(text, mine=False)
            self._update_progress()
            if self._speak is not None and text:
                try:
                    self._speak(text)
                except Exception:
                    logger.debug("onboarding speak failed", exc_info=True)
            if getattr(prompt, "is_complete", False):
                self._finish(completed=True)

        def _update_progress(self) -> None:
            try:
                frac = float(self._manager.progress)
            except Exception:
                frac = 0.0
            self._progress.set_fraction(max(0.0, min(1.0, frac)))
            convo = getattr(self._manager, "conversation", None)
            if convo is not None:
                self._progress.set_text(
                    f"Topic {convo.topic_index} of {convo.total_topics}"
                )

        # -- handlers -----------------------------------------------------
        def _on_send_clicked(self, *_a) -> None:
            text = self._entry.get_text().strip()
            if not text:
                return
            self._entry.set_text("")
            self._submit(text)

        def _on_listen_clicked(self, *_a) -> None:
            if self._listen is None:
                return
            try:
                heard = self._listen()
            except Exception:
                heard = None
                logger.debug("onboarding listen failed", exc_info=True)
            if heard:
                self._submit(heard)

        def _submit(self, text: str) -> None:
            self._add_bubble(text, mine=True)
            try:
                result = self._manager.submit(text)
            except Exception:
                logger.debug("onboarding submit failed", exc_info=True)
                return
            enc = result.get("encouragement")
            if enc:
                self._add_bubble(enc, mine=False)
                if self._speak is not None:
                    try:
                        self._speak(enc)
                    except Exception:
                        pass
            self._present_prompt(result.get("next_prompt"))

        def _on_skip_clicked(self, *_a) -> None:
            self._present_prompt(self._manager.skip_topic())

        def _on_pause_clicked(self, *_a) -> None:
            try:
                self._manager.save_state()
            except Exception:
                logger.debug("onboarding pause save failed", exc_info=True)
            self._finish(completed=False)

        def _finish(self, *, completed: bool) -> None:
            if self._closed:
                return
            self._closed = True
            self._completed = completed
            if self._on_close is not None:
                try:
                    self._on_close(completed)
                except Exception:
                    logger.debug("onboarding on_close failed", exc_info=True)
            self.close()

else:  # headless
    OnboardingDialog = None  # type: ignore


def open_onboarding_dialog(
    manager: Any,
    *,
    transient_for=None,
    on_close: Optional[CloseCallback] = None,
    speak: Optional[Callable[[str], None]] = None,
    listen: Optional[Callable[[], Optional[str]]] = None,
):
    """Show the onboarding dialog, or no-op gracefully when GTK is unavailable.

    Returns the dialog, or ``None`` on headless systems (after invoking
    ``on_close(False)`` so callers can proceed without blocking).
    """

    if not HAS_GTK or OnboardingDialog is None:
        logger.info(
            "Onboarding dialog requested but GTK is unavailable; skipping."
        )
        if on_close is not None:
            try:
                on_close(False)
            except Exception:
                logger.debug("onboarding on_close hook failed", exc_info=True)
        return None

    dialog = OnboardingDialog(
        manager,
        transient_for=transient_for,
        on_close=on_close,
        speak=speak,
        listen=listen,
    )
    dialog.present()
    return dialog

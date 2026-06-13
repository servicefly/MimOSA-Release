"""GTK4 "Test Your Wake Word" dialog (M2) -- a thin view shell.

After a custom wake-word model is trained, this window lets the user confirm it
actually hears them: it shows "Say '[name]'", listens through the microphone
with the freshly-trained model, and reports a clear ✅ (heard you!) or ❌ (didn't
catch that) with **Try again** and **Skip** options.

The heavy lifting -- microphone capture and wake-word detection -- reuses the
existing :mod:`mimosa.voice` building blocks
(:class:`~mimosa.voice.audio_manager.AudioManager` and
:func:`~mimosa.voice.wake_word.create_wake_word_detector`). All of it runs on a
worker thread so the GUI never blocks.

As with the rest of the UI, GTK must exist at *class definition* time, so the
import is guarded: on a headless machine :data:`HAS_GTK` is ``False`` and
:data:`TestWakeWordDialog` is ``None``. Callers should invoke
:func:`open_test_wakeword_dialog` unconditionally -- it returns ``None`` on
headless systems (after invoking ``on_close`` so the workflow can continue).
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Optional

logger = logging.getLogger(__name__)

#: How long (seconds) to listen for the wake word before declaring a miss.
DEFAULT_LISTEN_TIMEOUT = 8.0

#: Result-callback signature: ``on_close(heard: bool)``.
CloseCallback = Callable[[bool], None]

try:  # GTK is required to *define* the widget class.
    import gi

    gi.require_version("Gtk", "4.0")
    from gi.repository import GLib, Gtk

    HAS_GTK = True
except Exception:  # pragma: no cover - headless import path
    HAS_GTK = False


if HAS_GTK:

    class TestWakeWordDialog(Gtk.Window):  # pragma: no cover - GTK-only
        """Prompt the user to say their wake word and report whether it fired."""

        def __init__(
            self,
            name: str,
            *,
            model_path: str = "",
            transient_for=None,
            on_close: Optional[CloseCallback] = None,
            timeout: float = DEFAULT_LISTEN_TIMEOUT,
            detector_factory=None,
            audio_factory=None,
        ) -> None:
            super().__init__(title="Test Your Wake Word")
            self._name = (name or "").strip() or "your wake word"
            self._model_path = model_path or ""
            self._on_close = on_close
            self._timeout = float(timeout)
            self._detector_factory = detector_factory
            self._audio_factory = audio_factory

            self._stop = threading.Event()
            self._heard = False
            self._closed = False
            self._listening = False

            self.set_modal(True)
            if transient_for is not None:
                self.set_transient_for(transient_for)
            self.set_default_size(440, 300)

            root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
            root.set_margin_top(20)
            root.set_margin_bottom(20)
            root.set_margin_start(20)
            root.set_margin_end(20)
            self.set_child(root)

            self._prompt = Gtk.Label(xalign=0, wrap=True)
            self._prompt.add_css_class("title-2")
            self._prompt.set_text(f"Say \u201c{self._name}\u201d")
            root.append(self._prompt)

            self._hint = Gtk.Label(xalign=0, wrap=True)
            self._hint.set_text(
                "When you're ready, speak your wake word clearly. I'm listening…"
            )
            root.append(self._hint)

            # Listening indicator (a pulsing bar) + status line.
            self._indicator = Gtk.ProgressBar(show_text=False)
            root.append(self._indicator)

            self._status = Gtk.Label(xalign=0, wrap=True)
            self._status.add_css_class("dim-label")
            root.append(self._status)

            root.append(Gtk.Box(vexpand=True))  # spacer

            actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            actions.set_halign(Gtk.Align.END)
            self._skip_btn = Gtk.Button(label="Skip")
            self._skip_btn.connect("clicked", lambda *_: self._close(self._heard))
            self._retry_btn = Gtk.Button(label="Try Again")
            self._retry_btn.add_css_class("suggested-action")
            self._retry_btn.connect("clicked", lambda *_: self._start_listening())
            self._retry_btn.set_visible(False)
            actions.append(self._skip_btn)
            actions.append(self._retry_btn)
            root.append(actions)

            self.connect("close-request", self._on_close_request)

            self._start_listening()

        # -- listening -----------------------------------------------------

        def _start_listening(self) -> None:
            if self._listening:
                return
            self._listening = True
            self._heard = False
            self._stop.clear()
            self._retry_btn.set_visible(False)
            self._prompt.set_text(f"Say \u201c{self._name}\u201d")
            self._status.set_text("Listening…")
            self._pulse_id = GLib.timeout_add(120, self._pulse)

            def _worker() -> None:
                heard = self._listen_once()
                GLib.idle_add(self._finish_listening, heard)

            threading.Thread(target=_worker, name="mimosa-wakeword-test",
                             daemon=True).start()

        def _pulse(self) -> bool:
            if not self._listening:
                return False
            self._indicator.pulse()
            return True

        def _listen_once(self) -> bool:
            """Blocking: listen until the wake word fires or we time out."""
            try:
                if self._audio_factory is not None:
                    audio = self._audio_factory()
                else:
                    from mimosa.voice.audio_manager import AudioManager
                    audio = AudioManager()
                if self._detector_factory is not None:
                    detector = self._detector_factory(self._name, self._model_path)
                else:
                    from mimosa.voice.wake_word import create_wake_word_detector
                    detector = create_wake_word_detector(
                        self._name, model_path=self._model_path or None
                    )
            except Exception:
                logger.debug("Could not start wake-word test", exc_info=True)
                return False

            deadline = time.monotonic() + self._timeout
            fired = {"hit": False}

            def _on_detected() -> None:
                fired["hit"] = True
                self._stop.set()

            def _should_stop() -> bool:
                return self._stop.is_set() or time.monotonic() >= deadline

            try:
                detector.listen(audio, _on_detected, should_stop=_should_stop)
            except Exception:
                logger.debug("Wake-word test listen failed", exc_info=True)
                return False
            finally:
                try:
                    detector.delete()
                except Exception:
                    pass
                try:
                    audio.close()
                except Exception:
                    pass
            return fired["hit"]

        def _finish_listening(self, heard: bool) -> bool:
            self._listening = False
            self._heard = heard
            self._indicator.set_fraction(1.0 if heard else 0.0)
            if heard:
                self._prompt.set_text("✅ I heard you!")
                self._status.set_text(
                    f"\u201c{self._name}\u201d is working beautifully. You're all set."
                )
                self._retry_btn.set_visible(True)
                self._retry_btn.set_label("Test Again")
                self._skip_btn.set_label("Done")
            else:
                self._prompt.set_text("❌ Didn't catch that")
                self._status.set_text(
                    "I didn't hear your wake word. Make sure your mic is on and "
                    "try again — or skip for now and keep \u201cHey MimOSA\u201d."
                )
                self._retry_btn.set_visible(True)
                self._retry_btn.set_label("Try Again")
                self._skip_btn.set_label("Skip")
            return False  # one-shot idle

        # -- lifecycle -----------------------------------------------------

        def _on_close_request(self, *_args) -> bool:
            self._stop.set()
            self._notify_close(self._heard)
            return False  # allow close

        def _close(self, heard: bool) -> None:
            self._stop.set()
            self._notify_close(heard)
            self.close()

        def _notify_close(self, heard: bool) -> None:
            if self._closed:
                return
            self._closed = True
            if self._on_close is not None:
                try:
                    self._on_close(bool(heard))
                except Exception:
                    logger.debug("test on_close hook failed", exc_info=True)

else:  # headless
    TestWakeWordDialog = None  # type: ignore


def open_test_wakeword_dialog(
    name: str,
    *,
    model_path: str = "",
    transient_for=None,
    on_close: Optional[CloseCallback] = None,
    timeout: float = DEFAULT_LISTEN_TIMEOUT,
    detector_factory=None,
    audio_factory=None,
):
    """Show the wake-word test dialog, or no-op gracefully when headless.

    Returns the dialog, or ``None`` on headless systems (after invoking
    ``on_close(False)`` so the surrounding workflow can continue).
    """
    if not HAS_GTK or TestWakeWordDialog is None:
        logger.info("Wake-word test requested but GTK is unavailable; skipping.")
        if on_close is not None:
            try:
                on_close(False)
            except Exception:
                logger.debug("test on_close hook failed", exc_info=True)
        return None

    dialog = TestWakeWordDialog(
        name, model_path=model_path, transient_for=transient_for,
        on_close=on_close, timeout=timeout, detector_factory=detector_factory,
        audio_factory=audio_factory,
    )
    dialog.present()
    return dialog

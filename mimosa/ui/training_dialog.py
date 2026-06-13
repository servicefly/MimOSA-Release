"""GTK4 custom wake-word *training* dialog (M2) -- a thin view shell.

This window drives the heavy, opt-in training pipeline that turns a chosen name
(e.g. "Jarvis") into an on-device ``.onnx`` wake-word model. All real work lives
in :mod:`mimosa.training` (pure/testable); this module only renders progress and
forwards button presses, exactly like the other MimOSA dialogs.

Flow:

1. **Dependency check.** Training needs a one-time ~2.5 GB download (PyTorch,
   TensorFlow, openWakeWord extras). If those aren't installed we first show a
   friendly confirmation with three choices:
   *Download & Train* / *Use Mimosa Instead* / *Cancel* (M2 req #3).
2. **Training.** A :class:`~mimosa.training.WakeWordTrainer` runs on a worker
   thread; progress (stage, %, time remaining, epoch/loss/accuracy) streams back
   to the UI. The user can **pause/resume** or **cancel** at any time.
3. **Outcome.** On success the model path is reported; on cancel/failure we fall
   back to the built-in "Hey MimOSA" so the assistant always keeps working.

As with the rest of the UI, GTK must exist at *class definition* time, so the
import is guarded: on a headless machine :data:`HAS_GTK` is ``False`` and
:data:`TrainingDialog` is ``None``. Callers should invoke
:func:`open_training_dialog` unconditionally -- it returns ``None`` on headless
systems.
"""

from __future__ import annotations

import logging
import threading
from typing import Callable, Optional

from mimosa.training import (
    TrainingController,
    TrainingProgress,
    TrainingResult,
    WakeWordTrainer,
    check_dependencies,
    install_dependencies,
)

logger = logging.getLogger(__name__)

try:  # GTK is required to *define* the widget class.
    import gi

    gi.require_version("Gtk", "4.0")
    from gi.repository import GLib, Gtk

    HAS_GTK = True
except Exception:  # pragma: no cover - headless import path
    HAS_GTK = False


#: Result-callback signature: ``on_close(result_or_none)``.
CloseCallback = Callable[[Optional[TrainingResult]], None]


if HAS_GTK:

    class TrainingDialog(Gtk.Window):  # pragma: no cover - exercised only under GTK
        """A paged window that confirms dependencies, then trains a wake word."""

        def __init__(
            self,
            name: str,
            *,
            gender: str = "neutral",
            transient_for=None,
            on_close: Optional[CloseCallback] = None,
            trainer: Optional[WakeWordTrainer] = None,
            deps_probe=None,
        ) -> None:
            super().__init__(title="Train Your Wake Word")
            self._name = (name or "").strip()
            self._gender = gender or "neutral"
            self._on_close = on_close
            self._trainer = trainer or WakeWordTrainer()
            self._deps_probe = deps_probe
            self._controller = TrainingController()
            self._result: Optional[TrainingResult] = None
            self._closed = False
            self._training_started = False

            self.set_modal(True)
            if transient_for is not None:
                self.set_transient_for(transient_for)
            self.set_default_size(520, 380)

            self._root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
            self._root.set_margin_top(20)
            self._root.set_margin_bottom(20)
            self._root.set_margin_start(20)
            self._root.set_margin_end(20)
            self.set_child(self._root)

            self.connect("close-request", self._on_close_request)

            # Decide the first screen: confirm download or train straight away.
            report = check_dependencies(probe=self._deps_probe)
            if report.satisfied:
                self._show_training_screen()
                self._start_training()
            else:
                self._show_dependency_screen(report)

        # -- helpers -------------------------------------------------------

        def _clear_root(self) -> None:
            child = self._root.get_first_child()
            while child is not None:
                self._root.remove(child)
                child = self._root.get_first_child()

        # -- dependency confirmation screen --------------------------------

        def _show_dependency_screen(self, report) -> None:
            self._clear_root()

            title = Gtk.Label(xalign=0, wrap=True)
            title.add_css_class("title-3")
            title.set_text("One quick download first")
            self._root.append(title)

            body = Gtk.Label(xalign=0, wrap=True)
            body.set_text(report.prompt_message())
            self._root.append(body)

            self._dep_output = Gtk.Label(xalign=0, wrap=True)
            self._dep_output.add_css_class("dim-label")
            self._root.append(self._dep_output)

            self._dep_spinner = Gtk.ProgressBar(show_text=False)
            self._dep_spinner.set_visible(False)
            self._root.append(self._dep_spinner)

            self._root.append(Gtk.Box(vexpand=True))  # spacer

            actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            actions.set_halign(Gtk.Align.END)
            cancel = Gtk.Button(label="Cancel")
            cancel.connect("clicked", lambda *_: self._finish_fallback(
                cancelled=True))
            use_mimosa = Gtk.Button(label="Use Mimosa Instead")
            use_mimosa.connect("clicked", lambda *_: self._finish_fallback(
                cancelled=False))
            self._dep_download_btn = Gtk.Button(label="Download & Train")
            self._dep_download_btn.add_css_class("suggested-action")
            self._dep_download_btn.connect(
                "clicked", lambda *_: self._on_download_deps(report))
            actions.append(cancel)
            actions.append(use_mimosa)
            actions.append(self._dep_download_btn)
            self._root.append(actions)

        def _on_download_deps(self, report) -> None:
            self._dep_download_btn.set_sensitive(False)
            self._dep_download_btn.set_label("Downloading…")
            self._dep_spinner.set_visible(True)
            self._dep_spinner.pulse()

            def _pulse() -> bool:
                if self._closed:
                    return False
                self._dep_spinner.pulse()
                return not self._training_started

            GLib.timeout_add(200, _pulse)

            def _out(line: str) -> None:
                GLib.idle_add(self._dep_output.set_text, line)

            def _worker() -> None:
                ok = install_dependencies(report, on_output=_out)
                GLib.idle_add(self._after_deps, ok)

            threading.Thread(target=_worker, name="mimosa-deps-install",
                             daemon=True).start()

        def _after_deps(self, ok: bool) -> bool:
            if ok:
                self._show_training_screen()
                self._start_training()
            else:
                self._dep_download_btn.set_sensitive(True)
                self._dep_download_btn.set_label("Try Again")
                self._dep_spinner.set_visible(False)
                self._dep_output.set_text(
                    "The download didn't finish. You can try again, or use "
                    "\u201cHey MimOSA\u201d for now and train later from Settings."
                )
            return False  # one-shot idle

        # -- training screen -----------------------------------------------

        def _show_training_screen(self) -> None:
            self._clear_root()

            self._train_title = Gtk.Label(xalign=0, wrap=True)
            self._train_title.add_css_class("title-3")
            self._train_title.set_text(f"Training \u201c{self._name}\u201d")
            self._root.append(self._train_title)

            self._stage_label = Gtk.Label(xalign=0, wrap=True)
            self._stage_label.set_text("Getting ready…")
            self._root.append(self._stage_label)

            self._progress = Gtk.ProgressBar(show_text=True)
            self._progress.set_fraction(0.0)
            self._root.append(self._progress)

            self._eta_label = Gtk.Label(xalign=0)
            self._eta_label.add_css_class("dim-label")
            self._root.append(self._eta_label)

            self._metrics_label = Gtk.Label(xalign=0)
            self._metrics_label.add_css_class("dim-label")
            self._root.append(self._metrics_label)

            self._root.append(Gtk.Box(vexpand=True))  # spacer

            actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            actions.set_halign(Gtk.Align.END)
            self._pause_btn = Gtk.Button(label="Pause")
            self._pause_btn.connect("clicked", self._on_pause_resume)
            self._cancel_btn = Gtk.Button(label="Cancel")
            self._cancel_btn.connect("clicked", self._on_cancel_training)
            actions.append(self._pause_btn)
            actions.append(self._cancel_btn)
            self._root.append(actions)

        def _start_training(self) -> None:
            if self._training_started:
                return
            self._training_started = True

            def _progress(p: TrainingProgress) -> None:
                GLib.idle_add(self._apply_progress, p)

            def _worker() -> None:
                result = self._trainer.run(
                    self._name,
                    gender=self._gender,
                    controller=self._controller,
                    on_progress=_progress,
                )
                GLib.idle_add(self._apply_result, result)

            threading.Thread(target=_worker, name="mimosa-train",
                             daemon=True).start()

        def _apply_progress(self, p: TrainingProgress) -> bool:
            self._stage_label.set_text(p.stage_label or p.message or "Working…")
            self._progress.set_fraction(max(0.0, min(1.0, p.overall_fraction)))
            self._progress.set_text(f"{int(round(p.overall_fraction * 100))}%")
            self._eta_label.set_text(
                "Paused" if p.paused else p.eta_text()
            )
            bits = []
            if p.total_epochs:
                bits.append(f"Epoch {p.epoch}/{p.total_epochs}")
            if p.loss is not None:
                bits.append(f"loss {p.loss:.3f}")
            if p.accuracy is not None:
                bits.append(f"accuracy {int(round(p.accuracy * 100))}%")
            self._metrics_label.set_text("  ·  ".join(bits))
            return False  # one-shot idle

        def _on_pause_resume(self, _button) -> None:
            if self._controller.is_paused:
                self._controller.resume()
                self._pause_btn.set_label("Pause")
            else:
                self._controller.pause()
                self._pause_btn.set_label("Resume")

        def _on_cancel_training(self, _button) -> None:
            self._controller.cancel()
            self._cancel_btn.set_sensitive(False)
            self._cancel_btn.set_label("Cancelling…")
            self._pause_btn.set_sensitive(False)
            self._stage_label.set_text(
                "Stopping… I'll switch back to \u201cHey MimOSA\u201d."
            )

        def _apply_result(self, result: TrainingResult) -> bool:
            self._result = result
            if result.ok:
                self._show_done_screen(result)
            else:
                # Cancelled or failed: both fall back to Mimosa.
                self._show_fallback_screen(result)
            return False  # one-shot idle

        # -- terminal screens ----------------------------------------------

        def _show_done_screen(self, result: TrainingResult) -> None:
            self._clear_root()
            title = Gtk.Label(xalign=0, wrap=True)
            title.add_css_class("title-3")
            title.set_text("All done! 🎉")
            self._root.append(title)
            body = Gtk.Label(xalign=0, wrap=True)
            body.set_text(
                f"Your wake word \u201c{self._name}\u201d is trained and ready. "
                "Try saying it on the next screen to make sure I hear you well."
            )
            self._root.append(body)
            self._root.append(Gtk.Box(vexpand=True))
            actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            actions.set_halign(Gtk.Align.END)
            done = Gtk.Button(label="Continue")
            done.add_css_class("suggested-action")
            done.connect("clicked", lambda *_: self._close(result))
            actions.append(done)
            self._root.append(actions)

        def _show_fallback_screen(self, result: TrainingResult) -> None:
            self._clear_root()
            title = Gtk.Label(xalign=0, wrap=True)
            title.add_css_class("title-3")
            if result.cancelled:
                title.set_text("No problem — stopped")
                msg = (
                    "Training was cancelled, so I'll keep using "
                    "\u201cHey MimOSA\u201d. You can train your custom wake word "
                    "anytime from Settings."
                )
            else:
                title.set_text("Let's stick with \u201cHey MimOSA\u201d for now")
                msg = (
                    (result.error or "Training didn't finish.") + " No worries — "
                    "\u201cHey MimOSA\u201d works perfectly, and you can try "
                    "again later from Settings."
                )
            self._root.append(title)
            body = Gtk.Label(xalign=0, wrap=True)
            body.set_text(msg)
            self._root.append(body)
            self._root.append(Gtk.Box(vexpand=True))
            actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            actions.set_halign(Gtk.Align.END)
            ok = Gtk.Button(label="OK")
            ok.add_css_class("suggested-action")
            ok.connect("clicked", lambda *_: self._close(result))
            actions.append(ok)
            self._root.append(actions)

        def _finish_fallback(self, *, cancelled: bool) -> None:
            """User declined the download: report a graceful fallback result."""
            result = TrainingResult(
                ok=False,
                wake_word=self._name,
                cancelled=cancelled,
                fell_back=True,
                error="" if cancelled else "Training tools were not installed.",
            )
            self._close(result)

        # -- lifecycle -----------------------------------------------------

        def _on_close_request(self, *_args) -> bool:
            # X / Esc: make sure any running worker is asked to stop and the
            # caller hears about it (falling back to Mimosa).
            if not self._closed:
                try:
                    self._controller.cancel()
                except Exception:
                    logger.debug("training cancel-on-close failed", exc_info=True)
                result = self._result or TrainingResult(
                    ok=False, wake_word=self._name, cancelled=True,
                    fell_back=True,
                )
                self._notify_close(result)
            return False  # allow close

        def _close(self, result: Optional[TrainingResult]) -> None:
            self._notify_close(result)
            self.close()

        def _notify_close(self, result: Optional[TrainingResult]) -> None:
            if self._closed:
                return
            self._closed = True
            if self._on_close is not None:
                try:
                    self._on_close(result)
                except Exception:
                    logger.debug("training on_close hook failed", exc_info=True)

else:  # headless
    TrainingDialog = None  # type: ignore


def open_training_dialog(
    name: str,
    *,
    gender: str = "neutral",
    transient_for=None,
    on_close: Optional[CloseCallback] = None,
    trainer: Optional[WakeWordTrainer] = None,
    deps_probe=None,
):
    """Show the training dialog, or no-op gracefully when GTK is unavailable.

    Returns the dialog, or ``None`` on headless systems (after invoking
    ``on_close`` with a graceful fallback result so callers keep "Mimosa").
    """
    if not HAS_GTK or TrainingDialog is None:
        logger.info("Training dialog requested but GTK is unavailable; "
                    "falling back to the default wake word.")
        if on_close is not None:
            try:
                on_close(TrainingResult(
                    ok=False, wake_word=(name or "").strip(), fell_back=True,
                    error="Training UI is unavailable on this system.",
                ))
            except Exception:
                logger.debug("training on_close hook failed", exc_info=True)
        return None

    dialog = TrainingDialog(
        name, gender=gender, transient_for=transient_for, on_close=on_close,
        trainer=trainer, deps_probe=deps_probe,
    )
    dialog.present()
    return dialog

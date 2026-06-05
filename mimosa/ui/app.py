"""MimOSA application entry point (M3.1).

Wires the GTK4 avatar to the voice loop, with a first-class **headless** path:

* ``is_gui_available()`` false (no ``DISPLAY``/``WAYLAND_DISPLAY`` or no GTK 4),
  or ``--no-gui`` given  ->  run the voice loop directly on the main thread,
  importing **no** GTK at all.
* GUI available  ->  start a ``Gtk.Application`` showing the avatar; run the
  voice loop on a **worker thread**; a :class:`StateBridge` marshals voice-state
  changes onto the GTK main thread to animate the avatar.

The voice loop is fully decoupled: MimOSA runs with or without the UI. All GTK
imports are deferred to :meth:`MimOSAApplication.run_gui` so importing this
module never pulls in GTK.
"""

from __future__ import annotations

import argparse
import logging
import sys
import threading
from typing import Optional

from mimosa.ui.environment import describe_environment, is_gui_available
from mimosa.ui.state_bridge import StateBridge, UIState
from mimosa.ui.ui_config import UIConfig
from mimosa.ui.window_manager import WindowManager

logger = logging.getLogger(__name__)

#: GTK application id (reverse-DNS).
APP_ID = "ai.mimosa.Assistant"


class MimOSAApplication:
    """Top-level coordinator for the avatar UI + voice loop.

    Args:
        voice_loop: A ``VoiceLoop``-like object. When ``None``, one is created
            lazily (so ``--check`` and headless runs work without forcing heavy
            voice imports until needed).
        config: UI preferences; loaded from disk when ``None``.
        force_headless: If ``True``, never start GTK (equivalent to ``--no-gui``).
    """

    def __init__(
        self,
        voice_loop=None,
        config: Optional[UIConfig] = None,
        force_headless: bool = False,
    ) -> None:
        self._voice_loop = voice_loop
        self.config = config or UIConfig.load()
        self.force_headless = force_headless
        self.bridge: Optional[StateBridge] = None
        self.window = None
        self._gtk_app = None
        self._voice_thread: Optional[threading.Thread] = None

    # -- voice loop --------------------------------------------------------

    @property
    def voice_loop(self):
        """The voice loop, constructed on first use."""
        if self._voice_loop is None:
            from mimosa.voice.voice_loop import VoiceLoop

            self._voice_loop = VoiceLoop()
        return self._voice_loop

    def _start_voice_thread(self) -> None:
        """Run the voice loop on a daemon worker thread (GUI mode)."""
        def _run():
            try:
                self.voice_loop.run()
            except Exception as exc:  # pragma: no cover - defensive
                logger.exception("Voice loop crashed: %s", exc)

        self._voice_thread = threading.Thread(target=_run, name="mimosa-voice", daemon=True)
        self._voice_thread.start()

    def shutdown(self) -> None:
        """Stop the voice loop and detach the bridge (idempotent)."""
        try:
            if self._voice_loop is not None:
                self._voice_loop.stop()
        except Exception:  # pragma: no cover
            pass
        if self.bridge is not None:
            self.bridge.unsubscribe()

    # -- headless mode -----------------------------------------------------

    def run_headless(self) -> int:
        """Run the voice loop directly with no GUI. Returns a process exit code."""
        logger.info("Starting MimOSA in headless mode (%s)", describe_environment())
        try:
            self.voice_loop.run()
        except KeyboardInterrupt:  # pragma: no cover - interactive
            logger.info("Interrupted; shutting down.")
        finally:
            self.shutdown()
        return 0

    # -- GUI mode ----------------------------------------------------------

    def run_gui(self) -> int:
        """Run the GTK avatar with the voice loop on a worker thread.

        Imports GTK here (not at module load). Returns a process exit code.
        """
        import gi

        gi.require_version("Gtk", "4.0")
        from gi.repository import Gtk

        from mimosa.ui.avatar_window import AvatarWindow

        window_manager = WindowManager(self.config)

        app = Gtk.Application(application_id=APP_ID)
        self._gtk_app = app

        def _on_activate(application):
            window = AvatarWindow(
                application=application,
                config=self.config,
                on_quit=lambda: (self.shutdown(), application.quit()),
                on_settings=self._on_settings,
                on_move=lambda x, y: window_manager.save_position(x, y),
            )
            self.window = window
            window_manager.apply_to_window(window)

            # Bridge voice states -> avatar (thread-safe via GLib.idle_add).
            self.bridge = StateBridge(on_state_change=window.set_state)
            self.bridge.subscribe(self.voice_loop)

            if not self.config.start_hidden:
                window.present()

            # Kick off the voice loop only once the window is live.
            self._start_voice_thread()

        app.connect("activate", _on_activate)

        logger.info("Starting MimOSA GUI (%s)", describe_environment())
        try:
            status = app.run(None)
        finally:
            self.shutdown()
        return int(status or 0)

    def _on_settings(self) -> None:
        """Placeholder settings hook (full settings dialog is a later milestone)."""
        logger.info("Settings requested (config at default path).")

    # -- dispatch ----------------------------------------------------------

    def run(self) -> int:
        """Choose GUI vs headless automatically and run. Returns exit code."""
        if self.force_headless or not is_gui_available():
            return self.run_headless()
        return self.run_gui()


def build_arg_parser() -> argparse.ArgumentParser:
    """CLI parser for the ``mimosa`` UI entry point."""
    p = argparse.ArgumentParser(
        prog="mimosa",
        description="MimOSA -- privacy-first voice assistant with a desktop avatar.",
    )
    p.add_argument(
        "--no-gui",
        action="store_true",
        help="Run headless (voice/CLI only); never start the GTK avatar.",
    )
    p.add_argument(
        "--check",
        action="store_true",
        help="Print GUI/voice environment readiness and exit.",
    )
    p.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable debug logging.",
    )
    return p


def main(argv=None) -> int:
    """Console entry point. Returns a process exit code."""
    args = build_arg_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if args.check:
        print("MimOSA environment:")
        print("  " + describe_environment())
        return 0

    try:
        app = MimOSAApplication(force_headless=args.no_gui)
        return app.run()
    except Exception as exc:  # pragma: no cover - top-level guard
        logger.exception("Fatal error: %s", exc)
        return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

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
        config_manager=None,
    ) -> None:
        self._voice_loop = voice_loop
        # Unified config manager (M3.3) is the source of truth for all settings.
        # The avatar UI still works directly with the ``ui`` section as a
        # :class:`UIConfig`, kept in sync via the manager.
        from mimosa.utils.config import AppConfigManager

        self.config_manager = config_manager or AppConfigManager()
        if config_manager is None:
            self.config_manager.load()
        self.config = config or self.config_manager.get().ui
        self.force_headless = force_headless
        self.bridge: Optional[StateBridge] = None
        self.window = None
        self._gtk_app = None
        self._settings_dialog = None
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
        """Open the multi-page Settings dialog (M3.3), modal to the avatar.

        All hooks (skill listing, clear-history) are wired to the live voice
        loop when available, but are fully optional so the dialog also opens in
        minimal/test contexts. Applying changes persists them via the config
        manager and re-applies UI preferences to the running avatar.
        """
        try:
            from mimosa.ui.settings_dialog import open_settings_dialog
        except Exception:  # pragma: no cover - defensive
            logger.exception("Could not import settings dialog")
            return

        # If a dialog is already open, just focus it.
        if self._settings_dialog is not None:
            try:
                self._settings_dialog.present()
                return
            except Exception:
                self._settings_dialog = None

        def _skills_provider():
            try:
                return list(self.voice_loop.router.skills)
            except Exception:  # pragma: no cover - router optional
                return []

        def _on_clear_history():
            try:
                conv = self.voice_loop.conversation
                count = conv.turn_count
                conv.clear()
                return count
            except Exception:  # pragma: no cover - conversation optional
                return 0

        def _system_summary():
            try:
                from mimosa.system.system_profiler import SystemProfiler

                return SystemProfiler().profile.summary()
            except Exception:  # pragma: no cover
                return None

        def _on_close(applied: bool) -> None:
            self._settings_dialog = None
            if applied:
                self._apply_ui_preferences()

        self._settings_dialog = open_settings_dialog(
            self.config_manager,
            transient_for=self.window,
            skills_provider=_skills_provider,
            on_clear_history=_on_clear_history,
            on_close=_on_close,
            system_summary=_system_summary(),
        )

    def _apply_ui_preferences(self) -> None:
        """Re-apply the (possibly changed) UI section to the running avatar."""
        try:
            self.config = self.config_manager.get().ui
            if self.window is not None and hasattr(self.window, "apply_config"):
                self.window.apply_config(self.config)
        except Exception:  # pragma: no cover - best-effort live preview
            logger.debug("Could not live-apply UI preferences", exc_info=True)

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

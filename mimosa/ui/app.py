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
        self._services = None
        # M4.3 companions (all optional / created lazily under GTK).
        self._tray = None
        self._chat_controller = None
        self._chat_window = None
        from mimosa.ui.expressions import ExpressionController

        self.expressions = ExpressionController()

    # -- voice loop --------------------------------------------------------

    @property
    def services(self):
        """The optional runtime services (M7 stack + error reporter), lazily built.

        Constructed from the unified config so the background task queue,
        resource monitor and error-fix learner all honour the user's
        :class:`~mimosa.utils.config.TasksSettings` toggles. Best-effort: any
        failure degrades to ``None`` collaborators rather than crashing startup.
        """
        if self._services is None:
            from mimosa.core.runtime import AppServices

            try:
                self._services = AppServices.from_config(self.config_manager.get())
            except Exception:  # pragma: no cover - defensive
                logger.debug("Could not build runtime services", exc_info=True)
                self._services = AppServices()  # inert fallback
        return self._services

    @property
    def voice_loop(self):
        """The voice loop, constructed on first use."""
        if self._voice_loop is None:
            from mimosa.voice.audio_manager import AudioManager
            from mimosa.voice.voice_loop import VoiceLoop

            # Resolve the user's chosen microphone (set in the setup wizard /
            # Settings) to a concrete PyAudio device index so the voice loop
            # listens with the right device instead of the system default.
            voice_cfg = self.config_manager.get().voice
            device_index = AudioManager.resolve_device_index(voice_cfg.input_device)

            self._voice_loop = VoiceLoop(
                error_reporter=self.services.error_reporter,
                personality=self.config_manager.get().personality,
                input_device_index=device_index,
            )
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
        if self._services is not None:
            try:
                self._services.shutdown()
            except Exception:  # pragma: no cover - defensive
                pass

    def _start_services(self) -> None:
        """Start background services and apply data-retention maintenance.

        Best-effort and non-fatal: a failure here must never prevent MimOSA from
        starting up.
        """
        try:
            # Attach the real conversation store so retention/vacuum can run.
            if self.services.conversation_store is None:
                from mimosa.memory.conversation_store import ConversationStore

                self.services.conversation_store = ConversationStore()
            self.services.start()
            self.services.run_maintenance()
        except Exception:  # pragma: no cover - defensive
            logger.debug("Runtime services could not start", exc_info=True)

    # -- headless mode -----------------------------------------------------

    def _maybe_run_setup_wizard(self, transient_for=None) -> None:
        """Run the first-run setup wizard if this is a first launch (M4.2).

        Headless: completes silently with defaults (so startup never blocks).
        GUI: opens the wizard dialog. Best-effort -- any failure is non-fatal.
        """
        try:
            if not self.config_manager.is_first_run():
                return
            from mimosa.ui.setup_wizard_dialog import open_setup_wizard

            dialog = open_setup_wizard(
                self.config_manager,
                transient_for=transient_for,
                on_close=lambda applied: self._apply_ui_preferences(),
            )
            if dialog is None:
                logger.info("First-run setup completed with defaults (headless).")
        except Exception:  # pragma: no cover - wizard is best-effort
            logger.debug("Setup wizard could not run", exc_info=True)

    def run_headless(self) -> int:
        """Run the voice loop directly with no GUI. Returns a process exit code."""
        logger.info("Starting MimOSA in headless mode (%s)", describe_environment())
        self._maybe_run_setup_wizard()
        self._start_services()
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

            # First-run setup wizard (M4.2), modal to the avatar.
            self._maybe_run_setup_wizard(transient_for=window)

            # Bridge voice states -> avatar (thread-safe via GLib.idle_add).
            # The handler also keeps the expression controller and tray icon in
            # sync with the current state (M4.3).
            def _on_state_change(state: UIState) -> None:
                window.set_state(state)
                self.expressions.set_state(state)
                if self._tray is not None:
                    self._tray.controller.set_state(state)
                    self._tray.refresh()

            self.bridge = StateBridge(on_state_change=_on_state_change)
            self.bridge.subscribe(self.voice_loop)

            # Optional system-tray companion (M4.3); no-op when unavailable.
            self._build_system_tray(application)

            if not self.config.start_hidden:
                window.present()

            # Start optional runtime services (M7 stack) + data maintenance.
            self._start_services()

            # Kick off the voice loop only once the window is live.
            self._start_voice_thread()

        app.connect("activate", _on_activate)

        logger.info("Starting MimOSA GUI (%s)", describe_environment())
        try:
            status = app.run(None)
        finally:
            self.shutdown()
        return int(status or 0)

    # -- M4.3 companions ---------------------------------------------------

    def _chat_controller_or_create(self):
        """Return the shared chat controller, wiring it to the voice brain."""
        if self._chat_controller is None:
            from mimosa.ui.chat_logic import ChatController

            router = None
            conversation = None
            try:
                router = self.voice_loop.router
                conversation = self.voice_loop.conversation
            except Exception:  # pragma: no cover - defensive
                logger.debug("Voice loop brain unavailable for chat; degrading.")
            self._chat_controller = ChatController(
                router=router, conversation=conversation
            )
        return self._chat_controller

    def _open_chat(self) -> None:
        """Open (or re-present) the optional text-chat window (M4.3)."""
        from mimosa.ui.chat_window import open_chat_window

        if self._chat_window is not None:
            try:  # pragma: no cover - GTK-only path
                self._chat_window.present()
                return
            except Exception:
                self._chat_window = None
        self._chat_window = open_chat_window(
            self._chat_controller_or_create(), transient_for=self.window
        )

    def _build_system_tray(self, application=None) -> None:
        """Create the system-tray companion if a back-end is available (M4.3)."""
        from mimosa.ui.tray import create_system_tray
        from mimosa.ui.tray_logic import TrayController, TrayCallbacks

        def _toggle_avatar() -> None:
            if self.window is None:
                return
            try:  # pragma: no cover - GTK-only path
                if self.window.get_visible():
                    self.window.set_visible(False)
                else:
                    self.window.present()
            except Exception:
                logger.debug("Avatar visibility toggle failed.")

        def _quit() -> None:
            self.shutdown()
            if application is not None:
                try:  # pragma: no cover - GTK-only path
                    application.quit()
                except Exception:
                    pass

        callbacks = TrayCallbacks(
            on_show_avatar=_toggle_avatar,
            on_hide_avatar=_toggle_avatar,
            on_open_chat=self._open_chat,
            on_open_settings=self._on_settings,
            on_quit=_quit,
        )
        self._tray = create_system_tray(TrayController(callbacks))

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
    p.add_argument(
        "--no-log-file",
        action="store_true",
        help="Log to the console only; do not write the rotating log file.",
    )
    return p


def main(argv=None) -> int:
    """Console entry point. Returns a process exit code."""
    from mimosa.utils.logging_setup import configure_logging

    args = build_arg_parser().parse_args(argv)
    configure_logging(verbose=args.verbose, to_file=not args.no_log_file)

    if args.check:
        from mimosa.utils.logging_setup import describe_log_location

        print("MimOSA environment:")
        print("  " + describe_environment())
        print("  " + describe_log_location())
        return 0

    try:
        app = MimOSAApplication(force_headless=args.no_gui)
        return app.run()
    except Exception as exc:  # pragma: no cover - top-level guard
        logger.exception("Fatal error: %s", exc)
        return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

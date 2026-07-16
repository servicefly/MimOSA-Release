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
            # Bug #10: guarantee the config file exists on disk after the very
            # first launch (so ~/.config/mimosa/settings.json is always present,
            # even if the user never opens the wizard or settings).
            try:
                if not self.config_manager.path.exists():
                    self.config_manager.save()
            except Exception:  # pragma: no cover - defensive, never fatal
                logger.debug("Initial config persist failed", exc_info=True)
            # Milestone 1 (req #7): silently probe the host's capability for
            # future on-device wake-word training and cache the verdict in
            # config. Best-effort and logging-only -- no UI, never fatal.
            self._detect_hardware_capability()
        self.config = config or self.config_manager.get().ui
        self.force_headless = force_headless
        self.bridge: Optional[StateBridge] = None
        self.window = None
        self._gtk_app = None
        self._settings_dialog = None
        self._voice_thread: Optional[threading.Thread] = None
        self._services = None
        # Item #3: ensure the "no audio device -> voice disabled" notice is
        # shown at most once per session.
        self._audio_notice_shown = False
        # M4.3 companions (all optional / created lazily under GTK).
        self._tray = None
        self._chat_controller = None
        self._chat_window = None
        from mimosa.ui.expressions import ExpressionController

        self.expressions = ExpressionController()

    # -- hardware capability (M1, req #7) ----------------------------------

    def _detect_hardware_capability(self) -> None:
        """Silently scan the host and cache the capability verdict in config.

        Runs once at startup. A future milestone will let users train their own
        wake word on-device; this records whether the machine can handle it
        (gpu/cpu/insufficient). Logging-only -- no UI is shown. Any failure is
        swallowed so it can never block launch.
        """
        try:
            from mimosa.system.capability_detector import detect_capability

            report = detect_capability()
            cfg = self.config_manager.get()
            cfg.hardware.update_from_report(report)
            # Persist quietly so the verdict survives restarts. Best-effort.
            try:
                self.config_manager.save()
            except Exception:  # pragma: no cover - defensive
                logger.debug("Could not persist hardware capability", exc_info=True)
            logger.info(
                "Hardware capability for on-device training: %s",
                cfg.hardware.capability_level,
            )
        except Exception:  # pragma: no cover - never fatal
            logger.debug("Hardware capability detection failed", exc_info=True)

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
            output_index = AudioManager.resolve_output_device_index(
                voice_cfg.output_device
            )

            self._voice_loop = VoiceLoop(
                error_reporter=self.services.error_reporter,
                personality=self.config_manager.get().personality,
                input_device_index=device_index,
                output_device_index=output_index,
                continuous_learner=self._build_continuous_learner(),
            )
        return self._voice_loop

    def _build_continuous_learner(self):
        """Build the continuous learner if the user has opted in (best-effort).

        Returns ``None`` when learning is disabled or construction fails, so
        the voice loop simply skips the learning step rather than crashing.
        """
        try:
            learning = getattr(self.config_manager.get(), "learning", None)
            if learning is not None and not getattr(
                learning, "learn_from_conversations", True
            ):
                return None
            from mimosa.learning.continuous_learner import ContinuousLearner
            from mimosa.learning.pattern_detector import PatternDetector
            from mimosa.memory.profile_manager import ProfileManager

            return ContinuousLearner(
                profile_manager=ProfileManager(),
                pattern_detector=PatternDetector(),
                enabled=True,
            )
        except Exception:  # pragma: no cover - defensive
            logger.debug("Could not build continuous learner", exc_info=True)
            return None

    def _start_voice_thread(self) -> None:
        """Run the voice loop on a daemon worker thread (GUI mode)."""
        # Item #3: check for audio hardware once up front so we can show a
        # single, non-blocking notice if voice will be unavailable (instead of
        # letting the loop spam errors). This never blocks startup.
        self._maybe_notify_no_audio()

        def _run():
            try:
                self.voice_loop.run()
            except Exception as exc:  # pragma: no cover - defensive
                logger.exception("Voice loop crashed: %s", exc)

        self._voice_thread = threading.Thread(target=_run, name="mimosa-voice", daemon=True)
        self._voice_thread.start()

    def _maybe_notify_no_audio(self) -> None:
        """Show a one-time, non-blocking notice when no audio input exists.

        Item #3: on headless / no-audio hosts the voice loop disables itself and
        logs a single warning. Here we surface that to the user exactly once via
        a desktop notification (best-effort), so they understand why voice is
        off without the console being flooded.
        """
        if self._audio_notice_shown:
            return
        try:
            from mimosa.voice.audio_manager import AudioManager

            available, reason = AudioManager.check_audio_available()
        except Exception:  # pragma: no cover - defensive
            return
        if available:
            return
        self._audio_notice_shown = True
        message = (
            f"No microphone was found ({reason}). Voice features are disabled; "
            "you can still chat with MimOSA in the window."
        )
        logger.warning("Audio input unavailable: %s. Voice features disabled.", reason)
        try:
            from mimosa.system.kde_integration import KDEIntegration

            KDEIntegration().send_notification("MimOSA: voice disabled", message)
        except Exception:  # pragma: no cover - notifications are best-effort
            logger.debug("Could not send no-audio desktop notification", exc_info=True)

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

            def _on_wizard_close(applied: bool) -> None:
                self._apply_ui_preferences()
                # Post-setup training workflow (M2): if the user asked to train
                # their custom wake word "now", kick it off; otherwise the
                # default "Hey MimOSA" (or a "train later" reminder) stands.
                self._maybe_start_training_workflow(transient_for=transient_for)

            dialog = open_setup_wizard(
                self.config_manager,
                transient_for=transient_for,
                on_close=_on_wizard_close,
            )
            if dialog is None:
                logger.info("First-run setup completed with defaults (headless).")
        except Exception:  # pragma: no cover - wizard is best-effort
            logger.debug("Setup wizard could not run", exc_info=True)

    def _maybe_start_training_workflow(self, transient_for=None) -> None:
        """Start custom wake-word training iff the user chose "train now" (M2).

        Reads the freshly-persisted config: when ``voice.training_preference``
        is ``"now"`` and a custom name is set, launch the training dialog. Any
        other state (``"later"``/``"mimosa"``) leaves the default wake word in
        place. Best-effort and never fatal.
        """
        try:
            voice = self.config_manager.get().voice
            name = (getattr(voice, "custom_wake_word_name", "") or "").strip()
            pref = getattr(voice, "training_preference", "mimosa")
            if pref == "now" and name:
                self._train_custom_wake_word(name, transient_for=transient_for)
            else:
                # No training now -> proceed straight to onboarding (M3).
                self._maybe_start_onboarding_workflow(transient_for=transient_for)
        except Exception:  # pragma: no cover - best-effort
            logger.debug("Could not start training workflow", exc_info=True)

    def _train_custom_wake_word(self, name=None, transient_for=None) -> None:
        """Open the training dialog for ``name`` and wire up its outcome (M2).

        On success the trained model is recorded in config, the running voice
        loop reloads it, and the user is invited to test it. On cancel/failure
        we keep "Hey MimOSA". Never raises.
        """
        try:
            voice = self.config_manager.get().voice
            name = (name or getattr(voice, "custom_wake_word_name", "") or "").strip()
            if not name:
                return
            gender = ""
            try:
                gender = self.config_manager.get().personality.gender or "neutral"
            except Exception:
                gender = "neutral"
            from mimosa.ui.training_dialog import open_training_dialog

            def _on_training_close(result) -> None:
                self._on_training_complete(result, transient_for=transient_for)

            open_training_dialog(
                name,
                gender=gender or "neutral",
                transient_for=transient_for or self.window,
                on_close=_on_training_close,
            )
        except Exception:  # pragma: no cover - best-effort
            logger.debug("Could not open training dialog", exc_info=True)

    def _on_training_complete(self, result, transient_for=None) -> None:
        """Persist a successful model + reload the loop, then offer a test (M2)."""
        try:
            if result is not None and getattr(result, "ok", False) \
                    and getattr(result, "model_path", ""):
                # Record the trained model and stop nagging to "train later".
                self.config_manager.update_section(
                    "voice",
                    custom_model_path=result.model_path,
                    custom_wake_word_name=result.wake_word,
                    training_preference="mimosa",
                )
                try:
                    self.voice_loop.reload_wake_word()
                except Exception:  # pragma: no cover - loop optional
                    logger.debug("Could not reload wake word", exc_info=True)
                # Invite the user to test their new wake word.
                from mimosa.ui.test_wakeword_dialog import open_test_wakeword_dialog

                open_test_wakeword_dialog(
                    result.wake_word,
                    model_path=result.model_path,
                    transient_for=transient_for or self.window,
                )
            else:
                logger.info("Custom wake-word training did not complete; "
                            "keeping the default wake word.")
        except Exception:  # pragma: no cover - best-effort
            logger.debug("Post-training handling failed", exc_info=True)
        finally:
            # Onboarding follows wake-word setup regardless of its outcome (M3).
            self._maybe_start_onboarding_workflow(transient_for=transient_for)

    # -- onboarding (M3) ---------------------------------------------------
    def _build_llm_provider(self):
        """Build the configured LLM provider, or ``None`` (graceful). Never raises."""
        try:
            from mimosa.llm.provider_factory import create_provider
            from mimosa.voice.voice_loop import VoiceLoop

            return VoiceLoop._build_configured_provider(
                self.config_manager.get(), create_provider
            )
        except Exception:  # pragma: no cover - best-effort
            logger.debug("Could not build LLM provider for onboarding",
                         exc_info=True)
            return None

    def _build_onboarding_manager(self):
        """Construct an :class:`OnboardingManager` wired to memory + LLM."""
        from mimosa.memory.profile_manager import ProfileManager
        from mimosa.memory.vector_store import MemoryVectorStore
        from mimosa.onboarding import OnboardingManager

        vector_store = None
        try:
            vector_store = MemoryVectorStore()
        except Exception:  # pragma: no cover - storage optional
            logger.debug("Could not open memory vector store", exc_info=True)
        profile_manager = ProfileManager(vector_store=vector_store)
        return OnboardingManager(
            llm=self._build_llm_provider(),
            profile_manager=profile_manager,
            vector_store=vector_store,
            config_manager=self.config_manager,
        )

    def _maybe_start_onboarding_workflow(self, transient_for=None) -> None:
        """Offer the conversational onboarding after setup, if appropriate (M3).

        Runs only when the user hasn't completed it and hasn't opted to skip.
        The default preference is "later", which is non-blocking: we don't force
        it at startup, but we do surface it once right after setup so new users
        discover it. Best-effort and never fatal.
        """
        try:
            manager = self._build_onboarding_manager()
            if not manager.should_run():
                return
            from mimosa.ui.onboarding_dialog import open_onboarding_dialog

            open_onboarding_dialog(
                manager,
                transient_for=transient_for or self.window,
                on_close=lambda completed: self._on_onboarding_complete(
                    manager, completed
                ),
            )
        except Exception:  # pragma: no cover - best-effort
            logger.debug("Could not start onboarding workflow", exc_info=True)

    def _on_onboarding_complete(self, manager, completed) -> None:
        """After onboarding closes, refresh the live persona profile (M3)."""
        try:
            if completed:
                self._refresh_user_profile(manager.profile_manager)
        except Exception:  # pragma: no cover - best-effort
            logger.debug("Post-onboarding handling failed", exc_info=True)

    def _refresh_user_profile(self, profile_manager) -> None:
        """Inject the learned profile into the running voice loop's prompt (M3)."""
        try:
            loop = self._voice_loop
            if loop is not None and hasattr(loop, "set_user_profile"):
                loop.set_user_profile(profile_manager.profile)
        except Exception:  # pragma: no cover - best-effort
            logger.debug("Could not refresh user profile", exc_info=True)

    def _redo_onboarding(self) -> None:
        """Settings hook: clear completion flag and run onboarding again (M3)."""
        try:
            self.config_manager.update_section(
                "personality", persist=True, onboarding_complete=False
            )
        except Exception:  # pragma: no cover - best-effort
            logger.debug("Could not reset onboarding flag", exc_info=True)
        self._maybe_start_onboarding_workflow(transient_for=self.window)

    def _review_profile(self) -> None:
        """Settings hook: open the profile viewer/editor (M3)."""
        try:
            from mimosa.memory.profile_manager import ProfileManager
            from mimosa.memory.vector_store import MemoryVectorStore
            from mimosa.ui.profile_viewer import open_profile_viewer

            vector_store = None
            try:
                vector_store = MemoryVectorStore()
            except Exception:  # pragma: no cover
                vector_store = None
            pm = ProfileManager(vector_store=vector_store)

            def _on_save(edited):
                try:
                    from mimosa.memory.profile_manager import UserProfile

                    pm.profile = UserProfile.from_dict(edited)
                    pm.save()
                    self._refresh_user_profile(pm)
                except Exception:  # pragma: no cover
                    logger.debug("Could not save edited profile", exc_info=True)

            open_profile_viewer(
                pm.to_dict(),
                transient_for=self.window,
                on_save=_on_save,
            )
        except Exception:  # pragma: no cover - best-effort
            logger.debug("Could not open profile viewer", exc_info=True)

    def _clear_all_memories(self) -> None:
        """Settings hook: wipe the profile and all stored memories (M3)."""
        try:
            from mimosa.memory.profile_manager import ProfileManager
            from mimosa.memory.vector_store import MemoryVectorStore

            try:
                store = MemoryVectorStore()
                store.reset()
                store.close()
            except Exception:  # pragma: no cover
                logger.debug("Could not reset vector store", exc_info=True)
            pm = ProfileManager()
            pm.clear()
            self.config_manager.update_section(
                "personality", persist=True, onboarding_complete=False
            )
        except Exception:  # pragma: no cover - best-effort
            logger.debug("Could not clear memories", exc_info=True)

    def _view_memory(self) -> None:
        """Settings hook: open the read-only memory viewer (M4)."""
        try:
            from mimosa.memory.profile_manager import ProfileManager
            from mimosa.learning.pattern_detector import PatternDetector
            from mimosa.memory.relationship_tracker import RelationshipTracker
            from mimosa.learning.proactive_questioner import ProactiveQuestioner
            from mimosa.ui.memory_viewer import open_memory_viewer

            pm = ProfileManager()
            patterns = []
            try:
                patterns = PatternDetector().detect_patterns()
            except Exception:  # pragma: no cover
                patterns = []
            relationship = None
            try:
                relationship = RelationshipTracker().summary()
            except Exception:  # pragma: no cover
                relationship = None
            questions = []
            try:
                questions = ProactiveQuestioner().asked
            except Exception:  # pragma: no cover
                questions = []

            open_memory_viewer(
                profile=pm.to_dict(),
                patterns=patterns,
                relationship=relationship,
                questions=questions,
                transient_for=self.window,
                on_clear=self._clear_all_memories,
            )
        except Exception:  # pragma: no cover - best-effort
            logger.debug("Could not open memory viewer", exc_info=True)

    def _consolidate_memory(self):
        """Settings hook: tidy up the profile (merge dupes, flag conflicts)."""
        try:
            from mimosa.memory.profile_manager import ProfileManager
            from mimosa.memory.consolidator import (
                MemoryConsolidator,
                CONSOLIDATION_DEEP,
            )

            pm = ProfileManager()
            report = MemoryConsolidator(pm).consolidate(mode=CONSOLIDATION_DEEP)
            self._refresh_user_profile(pm)
            return report
        except Exception:  # pragma: no cover - best-effort
            logger.debug("Could not consolidate memory", exc_info=True)
            return None

    def _memory_stats_text(self) -> str:
        """Settings hook: a one-line summary of what's stored."""
        try:
            from mimosa.memory.profile_manager import ProfileManager

            pm = ProfileManager()
            prof = pm.profile
            n_skills = len(getattr(prof, "skills", []) or [])
            n_interests = len(getattr(prof, "interests", []) or [])
            n_people = len(getattr(prof, "relationships", {}) or {})
            return (
                f"I'm remembering {n_skills} skill(s), {n_interests} interest(s), "
                f"and {n_people} person(s) — all stored locally."
            )
        except Exception:  # pragma: no cover - best-effort
            return ""

    def _relationship_text(self) -> str:
        """Settings hook: a friendly description of the relationship stage."""
        try:
            from mimosa.memory.relationship_tracker import RelationshipTracker
            from mimosa.ui.memory_viewer import format_relationship_section

            return format_relationship_section(RelationshipTracker().summary())
        except Exception:  # pragma: no cover - best-effort
            return "We're just getting to know each other."

    def _rerun_setup_wizard(self) -> None:
        """Reset first-run state and reopen the setup wizard (M2, Settings).

        Other settings are preserved; only the wizard flow runs again. The
        wizard's normal completion path then re-applies preferences and may
        start training. Never raises.
        """
        try:
            cfg = self.config_manager.get()
            cfg.first_run_complete = False
            self.config_manager.replace(cfg)
            self._maybe_run_setup_wizard(transient_for=self.window)
        except Exception:  # pragma: no cover - best-effort
            logger.debug("Could not re-run setup wizard", exc_info=True)

    def _warn_if_mic_unavailable(self, transient_for=None) -> None:
        """Surface a warning if the configured microphone can't be found.

        A user may have selected a specific mic that has since been unplugged or
        renamed. We detect that here (the stored ``voice.input_device`` no longer
        resolves to a live device) and show a non-blocking dialog plus a log
        warning, pointing them at Settings. Best-effort and never fatal.
        """
        try:
            from mimosa.voice.audio_manager import AudioManager

            configured = (self.config_manager.get().voice.input_device or "").strip()
            if not configured:
                return  # using the system default; nothing to warn about
            if AudioManager.resolve_device_index(configured) is not None:
                return  # configured device is present

            msg = (
                f"The configured microphone ('{configured}') is unavailable. "
                "MimOSA will use the system default. Open Settings to pick an "
                "available microphone, or run 'mimosa --check-audio' to diagnose."
            )
            logger.warning(msg)
            try:
                import gi

                gi.require_version("Gtk", "4.0")
                from gi.repository import Gtk

                dialog = Gtk.AlertDialog()
                dialog.set_message("Microphone unavailable")
                dialog.set_detail(msg)
                dialog.show(transient_for)
            except Exception:  # pragma: no cover - GTK/runtime dependent
                logger.debug("Could not show mic-unavailable dialog", exc_info=True)
        except Exception:  # pragma: no cover - defensive, never fatal
            logger.debug("Microphone availability check failed", exc_info=True)

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
                on_open_chat=self._toggle_chat,
                on_toggle_pause=self._toggle_pause,
                on_about=lambda: self._show_about(transient_for=window),
                chat_open_provider=self._is_chat_open,
                on_reset_position=lambda: window_manager.reset_position(),
            )
            self.window = window
            window_manager.apply_to_window(window)

            # Make the active visualization explicit in the logs so it's clear
            # whether the character avatar or the classic circle is showing and
            # (when the circle) why (item #12 logging cleanup).
            av = self.config_manager.get().avatar
            if av.use_circle():
                reason = "avatar disabled" if not av.enabled else "circle_only tier"
                logger.info("Visualization: classic listening circle (%s)", reason)
            else:
                logger.info("Visualization: character avatar (tier=%s)", av.tier)

            # First-run setup wizard (M4.2), modal to the avatar.
            self._maybe_run_setup_wizard(transient_for=window)

            # Warn if the configured microphone is no longer available so the
            # user isn't left wondering why voice input is silent.
            self._warn_if_mic_unavailable(transient_for=window)

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

            # M8.4: Wire avatar lip sync to speech events
            self._wire_avatar_speech_callbacks(window)

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
        """Open (or re-present) the optional text-chat window (M4.3).

        The window instance is kept alive across hides so its size and position
        are remembered when reopened. Closing it via the title-bar "X" hides it
        rather than destroying it (see ``_install_chat_close_to_hide``).
        """
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
        self._install_chat_close_to_hide()

    def _install_chat_close_to_hide(self) -> None:
        """Make the chat window hide (not destroy) on close to keep position."""
        if self._chat_window is None:
            return
        try:  # pragma: no cover - GTK-only path
            def _on_close_request(win):
                win.set_visible(False)
                return True  # stop default destroy; preserve position/size

            self._chat_window.connect("close-request", _on_close_request)
        except Exception:  # pragma: no cover - defensive
            logger.debug("Could not install chat close-to-hide handler.")

    def _is_chat_open(self) -> bool:
        """Return True when the chat window exists and is currently visible."""
        if self._chat_window is None:
            return False
        try:  # pragma: no cover - GTK-only path
            return bool(self._chat_window.get_visible())
        except Exception:  # pragma: no cover - defensive
            return False

    def _toggle_chat(self) -> None:
        """Toggle the chat window: open/show if hidden, hide if visible.

        This backs both the gear menu and the right-click menu so mic-less
        users can drive MimOSA entirely from the chat window.
        """
        if self._is_chat_open():
            try:  # pragma: no cover - GTK-only path
                self._chat_window.set_visible(False)
            except Exception:  # pragma: no cover - defensive
                logger.debug("Hiding chat window failed.")
            return
        self._open_chat()

    def _toggle_pause(self):
        """Pause/resume wake-word listening; returns the new paused state.

        Returning the authoritative state lets the avatar menu relabel itself
        ("Pause Listening" <-> "Resume Listening") without guessing.
        """
        try:
            return bool(self.voice_loop.toggle_pause())
        except Exception:  # pragma: no cover - voice loop optional
            logger.debug("Toggle pause failed; voice loop unavailable.")
            return None

    def _show_about(self, transient_for=None) -> None:
        """Show a small About dialog with the MimOSA version (M4.3)."""
        try:  # pragma: no cover - GTK-only path
            import gi

            gi.require_version("Gtk", "4.0")
            from gi.repository import Gtk

            try:
                from mimosa import __version__ as version
            except Exception:
                version = "1.1.0"

            dialog = Gtk.AboutDialog()
            if transient_for is not None:
                dialog.set_transient_for(transient_for)
            dialog.set_modal(True)
            dialog.set_program_name("MimOSA")
            dialog.set_version(str(version))
            dialog.set_comments(
                "A friendly, always-on-top voice assistant avatar."
            )
            dialog.set_license_type(Gtk.License.MIT_X11)
            dialog.present()
        except Exception:  # pragma: no cover - defensive
            logger.debug("About dialog could not be shown.", exc_info=True)

    def _wire_avatar_speech_callbacks(self, window) -> None:
        """
        Connect voice loop speech events to avatar renderer for lip sync (M8.4).
        
        Args:
            window: The AvatarWindow instance
        """
        try:
            # Check if the renderer supports speech animation (Sprite2DRenderer does)
            renderer = getattr(window, 'renderer', None)
            if renderer is None:
                return
            
            # Check if renderer has the required methods
            if not (hasattr(renderer, 'start_speaking') and hasattr(renderer, 'stop_speaking')):
                logger.debug("Renderer does not support speech callbacks")
                return
            
            # Define thread-safe callback wrappers that marshal to GTK main thread
            import gi
            gi.require_version("GLib", "2.0")
            from gi.repository import GLib
            
            def on_speech_start(text: str, duration: float):
                """Called when TTS starts speaking (worker thread)."""
                # Marshal to GTK main thread
                GLib.idle_add(lambda: renderer.start_speaking(text, duration))
            
            def on_speech_end():
                """Called when TTS finishes speaking (worker thread)."""
                # Marshal to GTK main thread
                GLib.idle_add(lambda: renderer.stop_speaking())
            
            # Register callbacks with voice loop
            self.voice_loop.set_speech_callbacks(
                on_start=on_speech_start,
                on_end=on_speech_end
            )
            
            logger.debug("Avatar speech callbacks wired successfully")
            
        except Exception as exc:
            # Non-fatal - avatar will work without lip sync
            logger.debug("Could not wire avatar speech callbacks: %s", exc)

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
            on_rerun_wizard=self._rerun_setup_wizard,
            on_train_wakeword=self._train_custom_wake_word,
            on_review_profile=self._review_profile,
            on_redo_onboarding=self._redo_onboarding,
            on_clear_memories=self._clear_all_memories,
            on_consolidate_memory=self._consolidate_memory,
            on_view_memory=self._view_memory,
            memory_stats_provider=self._memory_stats_text,
            relationship_provider=self._relationship_text,
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
        "--check-audio",
        action="store_true",
        help="Run microphone diagnostics: list input devices, show the "
        "configured one, and record a short live-volume test, then exit.",
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


def run_audio_diagnostics(seconds: float = 5.0) -> int:
    """Diagnose microphone input and exit. Returns a process exit code.

    Implements the ``--check-audio`` command:

    1. Lists every available input device with its device index (and flags the
       system default).
    2. Shows which microphone is configured (resolving the stored
       ``voice.input_device`` to a concrete index) and warns loudly if that
       device is no longer available.
    3. Runs a short recording test that prints a real-time volume meter so the
       user can confirm the mic is actually capturing audio.
    """
    from mimosa.voice.audio_manager import AudioManager, AudioUnavailableError

    print("MimOSA audio diagnostics")
    print("=" * 48)

    # 1) Enumerate input devices.
    devices = AudioManager.list_input_devices()
    default = AudioManager.get_default_input_device()
    default_index = default.index if default is not None else None

    if not devices:
        print("\nNo input (microphone) devices were found.")
        print("Check that a microphone is connected and that PyAudio + the")
        print("PortAudio system library are installed.")
        return 1

    print(f"\nInput devices ({len(devices)} found):")
    for d in devices:
        marker = "  <- system default" if d.index == default_index else ""
        print(f"  [{d.index}] {d.name}  ({d.max_input_channels} ch){marker}")

    # 2) Show the configured microphone.
    configured = ""
    try:
        from mimosa.utils.config import AppConfigManager

        manager = AppConfigManager()
        manager.load()
        configured = manager.get().voice.input_device or ""
    except Exception:  # pragma: no cover - config best-effort
        configured = ""

    print("\nConfigured microphone:")
    if not configured:
        if default_index is not None:
            print(f"  (none set -> using system default) [{default_index}] {default.name}")
        else:
            print("  (none set -> using system default)")
        resolved = default_index
    else:
        resolved = AudioManager.resolve_device_index(configured)
        if resolved is None:
            print(f"  '{configured}' is NOT available!")
            print("  WARNING: the configured microphone is unavailable; falling")
            print("           back to the system default. Open Settings to pick")
            print("           an available device.")
            resolved = default_index
        else:
            name = next((d.name for d in devices if d.index == resolved), "?")
            print(f"  '{configured}' -> [{resolved}] {name}")

    # 3) Live recording test with a real-time volume meter.
    print(f"\nRecording test ({int(seconds)}s) -- please speak into the mic...")
    mgr = AudioManager(device_index=resolved)

    def _on_level(level: float) -> None:
        bars = int(level * 40)
        meter = "#" * bars + "-" * (40 - bars)
        print(f"\r  [{meter}] {level * 100:5.1f}%", end="", flush=True)

    try:
        peak = mgr.measure_levels(seconds, _on_level)
    except AudioUnavailableError as exc:
        print(f"\n  Recording failed: {exc}")
        return 1
    except Exception as exc:  # pragma: no cover - backend dependent
        print(f"\n  Recording failed: {exc}")
        return 1

    print()  # finish the meter line
    print(f"\nPeak level: {peak * 100:.1f}%")
    if peak < 0.01:
        print("  WARNING: little or no signal detected. Check the mic connection,")
        print("           OS input permissions, and that the right device is set.")
    else:
        print("  OK: the microphone is capturing audio.")
    return 0


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

    if args.check_audio:
        return run_audio_diagnostics()

    try:
        app = MimOSAApplication(force_headless=args.no_gui)
        return app.run()
    except Exception as exc:  # pragma: no cover - top-level guard
        logger.exception("Fatal error: %s", exc)
        return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

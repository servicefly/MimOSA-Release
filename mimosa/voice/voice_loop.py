"""The MimOSA voice loop: a state machine tying the local voice stack together.

This module orchestrates the full **local-first** voice interaction cycle:

    IDLE  --(wake word)-->  LISTENING  --(speech captured)-->  PROCESSING
      ^                                                            |
      |                                                            v
   SPEAKING  <--------------------(response synthesized)----------+

Concretely, one turn is:

1. **IDLE** -- wait for the wake word (openWakeWord or the energy fallback).
2. **LISTENING** -- record the user's utterance until they stop talking
   (:meth:`AudioManager.record_until_silence`).
3. **PROCESSING** -- transcribe locally with Whisper, then route the text. As
   of M1.3 the transcript goes to the
   :class:`~mimosa.core.intent_router.IntentRouter`, which classifies the
   intent and dispatches to a skill (time, weather, calculator, question,
   greeting). Conversation context is tracked across turns by a
   :class:`~mimosa.core.conversation_manager.ConversationManager`. (If no
   router is supplied, the loop falls back to a plain *response handler* --
   e.g. the M1.2 echo handler -- for backwards compatibility and testing.)
4. **SPEAKING** -- synthesize the response locally with Piper and play it.

Everything except the (future) LLM step happens on-device, honoring MimOSA's
privacy guarantee that audio never leaves the machine.

Design notes
------------
* Components are **injected** (wake word detector, STT, TTS, audio manager) so
  the loop is easy to unit-test with mocks and so missing optional backends
  degrade gracefully rather than crashing at import time.
* All component construction is lazy/defensive: on a headless VM with no audio
  or ML stack, you can still import and instantiate :class:`VoiceLoop`; errors
  surface only when you actually call :meth:`run`/:meth:`run_once`.
"""

from __future__ import annotations

import enum
import logging
import os
import time
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# A response handler maps recognized user text -> MimOSA's reply text.
ResponseHandler = Callable[[str], str]


class VoiceState(enum.Enum):
    """States of the voice interaction loop."""

    IDLE = "idle"            # waiting for the wake word
    LISTENING = "listening"  # recording the user's utterance
    PROCESSING = "processing"  # transcribing + generating a response
    SPEAKING = "speaking"    # playing the synthesized reply
    PAUSED = "paused"        # listening temporarily suspended by the user
    STOPPED = "stopped"      # loop has been shut down


def echo_response_handler(text: str) -> str:
    """Default M1.2 response handler: echo the recognized text.

    A real LLM-backed handler is introduced in a later milestone. Keeping this
    trivial lets us validate the *audio* pipeline end-to-end in isolation.
    """
    if not text:
        return "I didn't catch that. Could you say it again?"
    return f"You said: {text}"


class VoiceLoop:
    """Coordinates wake word -> STT -> response -> TTS as a state machine.

    Args:
        audio_manager: Provides recording and playback. If ``None``, a default
            :class:`~mimosa.voice.audio_manager.AudioManager` is created.
        wake_word_detector: A :class:`~mimosa.voice.wake_word.BaseWakeWord`. If
            ``None``, one is built via
            :func:`~mimosa.voice.wake_word.create_wake_word_detector`.
        stt: A speech-to-text engine exposing ``transcribe_pcm``. If ``None``,
            a :class:`~mimosa.voice.stt.WhisperSTT` is created.
        tts: A text-to-speech engine exposing ``synthesize``. If ``None``, a
            :class:`~mimosa.voice.tts.PiperTTS` is created.
        intent_router: An :class:`~mimosa.core.intent_router.IntentRouter` that
            classifies the transcript and dispatches to a skill (M1.3). When
            provided it takes precedence over ``response_handler``. If ``None``
            and no ``response_handler`` is given, a default router is created
            lazily on first use.
        conversation_manager: A
            :class:`~mimosa.core.conversation_manager.ConversationManager` for
            multi-turn context. Created lazily if ``None``.
        response_handler: Optional simple ``text -> reply`` callable. Used only
            when no ``intent_router`` is active (e.g. the M1.2 echo handler in
            tests). If both are ``None``, the intent router path is used.
        max_record_seconds: Safety cap on a single utterance recording.

    Note:
        Construction never imports heavy/optional backends; defaults are
        constructed lazily and only *used* inside :meth:`run`/:meth:`run_once`.
    """

    def __init__(
        self,
        audio_manager=None,
        wake_word_detector=None,
        stt=None,
        tts=None,
        intent_router=None,
        conversation_manager=None,
        response_handler: Optional[ResponseHandler] = None,
        max_record_seconds: float = 15.0,
        error_reporter=None,
        personality=None,
        input_device_index: Optional[int] = None,
        output_device_index: Optional[int] = None,
        continuous_learner=None,
    ) -> None:
        self._audio = audio_manager
        # Preferred microphone (PyAudio input-device index), chosen in the setup
        # wizard / Settings. ``None`` means "use the system default device".
        self._input_device_index = input_device_index
        # Preferred speaker (PyAudio output-device index), chosen in the setup
        # wizard / Settings. ``None`` means "use the system default device".
        self._output_device_index = output_device_index
        self._wake = wake_word_detector
        self._stt = stt
        self._tts = tts
        self._router = intent_router
        self._error_reporter = error_reporter
        self._personality = personality
        self._conversation = conversation_manager
        #: Optional continuous learner (M4). When set, each completed turn is
        #: quietly analysed for facts/patterns. Fully optional and defensive --
        #: any failure is swallowed so it can never disrupt a conversation.
        self._continuous_learner = continuous_learner
        # When an explicit response_handler is given (and no router), use the
        # legacy handler path; otherwise the intent router drives responses.
        self.response_handler: Optional[ResponseHandler] = response_handler
        self._use_router = response_handler is None or intent_router is not None
        self.max_record_seconds = max_record_seconds

        self._state = VoiceState.IDLE
        self._stop_requested = False
        self._paused = False
        self._state_listeners: list = []
        #: Result of the one-time audio-hardware probe done in :meth:`run`
        #: (item #3). ``None`` until the loop starts; then ``(available, reason)``.
        #: ``app.py`` reads this to show a one-time "voice disabled" notice.
        self.audio_available: Optional[bool] = None
        self.audio_unavailable_reason: str = ""

    # -- state -------------------------------------------------------------

    @property
    def state(self) -> VoiceState:
        """The current :class:`VoiceState`."""
        return self._state

    def add_state_listener(self, callback) -> None:
        """Register a callback invoked on every state change.

        The callback receives the new :class:`VoiceState`. Listeners are an
        optional, best-effort observation seam (the GTK avatar UI in Phase 3
        subscribes here to mirror the assistant's state). A listener raising an
        exception is logged and ignored -- it can never break the voice loop.
        Adding the same callback twice is a no-op.
        """
        if callback is not None and callback not in self._state_listeners:
            self._state_listeners.append(callback)

    def remove_state_listener(self, callback) -> None:
        """Unregister a previously added state-change callback (safe if absent)."""
        try:
            self._state_listeners.remove(callback)
        except ValueError:
            pass

    def _set_state(self, state: VoiceState) -> None:
        if state is not self._state:
            logger.debug("Voice state: %s -> %s", self._state.value, state.value)
            self._state = state
            for listener in list(self._state_listeners):
                try:
                    listener(state)
                except Exception as exc:  # pragma: no cover - listeners are best-effort
                    logger.error("State listener failed: %s", exc)

    # -- lazy component accessors -----------------------------------------

    @property
    def audio(self):
        """The :class:`AudioManager`, created on first access."""
        if self._audio is None:
            from mimosa.voice.audio_manager import AudioManager

            self._audio = AudioManager(
                device_index=self._input_device_index,
                output_device=self._output_device_index,
            )
        return self._audio

    @property
    def wake(self):
        """The wake-word detector, created on first access (never raises)."""
        if self._wake is None:
            from mimosa.voice.wake_word import create_wake_word_detector

            # Honour a trained custom wake-word model when one is configured
            # (M2): the custom name becomes the wake phrase and its .onnx model
            # is loaded. Anything missing degrades to the "hey mimosa" default.
            wake_word = os.getenv("WAKE_WORD", "hey mimosa")
            model_path = None
            try:
                from mimosa.utils.config import AppConfigManager

                cfg = AppConfigManager().get()
                if cfg.voice.has_custom_model():
                    model_path = cfg.voice.custom_model_path
                    wake_word = (
                        cfg.voice.custom_wake_word_name
                        or cfg.voice.wake_word
                        or wake_word
                    )
                elif cfg.voice.wake_word:
                    wake_word = cfg.voice.wake_word
            except Exception as exc:  # pragma: no cover - config optional
                logger.debug("Could not load wake-word config (%s)", exc)

            self._wake = create_wake_word_detector(
                wake_word, model_path=model_path
            )
        return self._wake

    def reload_wake_word(self) -> None:
        """Drop the cached wake-word detector so it's rebuilt from config (M2).

        Call this after training (or otherwise changing) a custom wake word so
        the next access of :attr:`wake` picks up the new model/phrase. Safe to
        call anytime; never raises.
        """
        old = self._wake
        self._wake = None
        if old is not None:
            try:
                old.delete()
            except Exception:  # pragma: no cover - best-effort cleanup
                logger.debug("Could not release previous wake detector",
                             exc_info=True)

    @property
    def stt(self):
        """The Whisper STT engine, created on first access."""
        if self._stt is None:
            from mimosa.voice.stt import create_stt

            self._stt = create_stt()
        return self._stt

    @property
    def tts(self):
        """The Piper TTS engine, created on first access."""
        if self._tts is None:
            from mimosa.voice.tts import create_tts

            # Honour the user's voice preferences (M2 req #9): an explicit
            # tts_voice always wins; otherwise the voice-style "gender" choice
            # biases which Piper voice MimOSA speaks with. Config is optional --
            # any failure degrades to the engine default.
            voice = ""
            gender = ""
            speed = None
            try:
                from mimosa.utils.config import AppConfigManager

                cfg = AppConfigManager().get()
                voice = (cfg.voice.tts_voice or "").strip()
                gender = (cfg.personality.gender or "").strip()
                speed = cfg.voice.tts_speed
            except Exception as exc:  # pragma: no cover - config optional
                logger.debug("Could not load TTS voice prefs (%s)", exc)

            kwargs = {}
            if speed:
                kwargs["speed"] = speed
            self._tts = create_tts(voice or None, gender=gender or None, **kwargs)
        return self._tts

    @property
    def router(self):
        """The :class:`IntentRouter`, created lazily with the default LLM.

        Building the router constructs the default skill set and an LLM
        provider (Abacus.AI by default, or local when ``USE_LOCAL_LLM`` is set).
        This is only invoked when the router path is active.
        """
        if self._router is None:
            from mimosa.core.intent_router import IntentRouter
            from mimosa.llm.provider_factory import create_provider

            # Load the unified config once so we can honour the LLM provider /
            # API key the user chose in the setup wizard's "Connect Your AI
            # Brain" step, and load their custom skills (M4.1).
            app_cfg = None
            try:
                from mimosa.utils.config import AppConfigManager

                _mgr = AppConfigManager()
                _mgr.load()
                app_cfg = _mgr.get()
            except Exception as exc:  # pragma: no cover - config optional
                logger.debug("Could not load config for LLM/skills (%s)", exc)

            provider = self._build_configured_provider(app_cfg, create_provider)

            # Load user-defined custom skills (M4.1) from the unified config so
            # the user's own commands are active. Best-effort: a missing/corrupt
            # config simply means no custom skills.
            custom_skills = []
            try:
                from mimosa.skills.custom_skill import build_custom_skills

                if app_cfg is not None:
                    custom_skills = build_custom_skills(
                        app_cfg.skills.custom_specs(), llm_provider=provider
                    )
            except Exception as exc:  # pragma: no cover - config optional
                logger.debug("Could not load custom skills (%s)", exc)

            # Load the learned user profile (M3) so LLM-backed skills can
            # personalise their answers. Best-effort: any failure -> no profile.
            user_profile = None
            try:
                from mimosa.memory.profile_manager import ProfileManager

                pm = ProfileManager(autosave=False)
                if not pm.profile.is_empty():
                    user_profile = pm.profile
            except Exception as exc:  # pragma: no cover - memory optional
                logger.debug("Could not load user profile (%s)", exc)

            self._router = IntentRouter(
                llm_provider=provider,
                custom_skills=custom_skills,
                error_reporter=self._error_reporter,
                personality=self._personality,
                user_profile=user_profile,
            )
        return self._router

    def set_user_profile(self, user_profile) -> None:
        """Refresh the learned user profile on the live router (M3).

        Called by the app after onboarding completes or the profile is edited so
        in-flight skills immediately reflect the new information. Best-effort.
        """
        try:
            if self._router is not None and hasattr(self._router, "set_user_profile"):
                self._router.set_user_profile(user_profile)
        except Exception:  # pragma: no cover - best-effort
            logger.debug("Could not set user profile on router", exc_info=True)

    @staticmethod
    def _build_configured_provider(app_cfg, create_provider):
        """Create the LLM provider chosen in the setup wizard / config.

        Honours ``privacy.llm_provider`` and ``privacy.api_key``:

        * ``"none"`` -> no LLM (skills-only); returns ``None``.
        * ``"ollama"``/``"local"`` -> on-device provider (no key needed).
        * ``"abacus"``/``"openai"``/``"anthropic"`` -> cloud provider, with the
          stored API key passed through.

        Falls back to the factory default if config is unavailable. Never
        raises -- a misconfigured provider degrades to ``None`` so the rest of
        the assistant still runs.
        """
        try:
            if app_cfg is None:
                return create_provider()

            provider_name = (app_cfg.privacy.llm_provider or "").strip().lower()
            if provider_name == "none":
                logger.info("LLM disabled (provider 'none'); skills-only mode.")
                return None

            options = {}
            api_key = (getattr(app_cfg.privacy, "api_key", "") or "").strip()
            if api_key:
                options["api_key"] = api_key

            if not provider_name:
                return create_provider(**options)
            return create_provider(provider_name, **options)
        except Exception as exc:  # provider misconfig shouldn't crash setup
            logger.warning("Could not create LLM provider (%s); "
                           "LLM-backed skills will degrade.", exc)
            return None

    @property
    def conversation(self):
        """The :class:`ConversationManager`, created lazily."""
        if self._conversation is None:
            from mimosa.core.conversation_manager import ConversationManager

            max_history = int(os.getenv("MAX_CONVERSATION_HISTORY", "10"))
            self._conversation = ConversationManager(max_history=max_history)
        return self._conversation

    # -- control -----------------------------------------------------------

    def stop(self) -> None:
        """Request the loop to stop after the current turn.

        Safe to call from another thread or a signal handler.
        """
        logger.info("Voice loop stop requested.")
        self._stop_requested = True

    @property
    def is_paused(self) -> bool:
        """Whether listening is currently paused (wake word ignored)."""
        return self._paused

    def pause(self) -> bool:
        """Suspend listening: the loop ignores the wake word until resumed.

        Useful for accessibility (e.g. users driving MimOSA entirely from the
        chat window) and for muting the mic without quitting. Safe to call from
        the GTK main thread or any other thread. Returns the new paused state.
        """
        if not self._paused:
            logger.info("Voice loop paused (listening suspended).")
            self._paused = True
            self._set_state(VoiceState.PAUSED)
        return self._paused

    def resume(self) -> bool:
        """Resume listening after a :meth:`pause`. Returns the new paused state."""
        if self._paused:
            logger.info("Voice loop resumed (listening active).")
            self._paused = False
            self._set_state(VoiceState.IDLE)
        return self._paused

    def toggle_pause(self) -> bool:
        """Flip between paused and listening. Returns the new paused state."""
        return self.resume() if self._paused else self.pause()

    # -- core turn ---------------------------------------------------------

    def run_once(self, wait_for_wake: bool = True) -> Optional[str]:
        """Execute a single interaction turn and return the reply text.

        Steps: (optionally) wait for the wake word, record until silence,
        transcribe, generate a response, and speak it.

        Args:
            wait_for_wake: If ``True``, block until the wake word fires before
                recording. If ``False``, start recording immediately (useful
                for push-to-talk or testing).

        Returns:
            The reply text that was spoken, or ``None`` if the turn was aborted
            (e.g. stop requested, or no speech captured).
        """
        if wait_for_wake:
            if not self._await_wake_word():
                return None

        # LISTENING: capture the user's utterance.
        self._set_state(VoiceState.LISTENING)
        try:
            pcm = self.audio.record_until_silence(max_seconds=self.max_record_seconds)
        except Exception as exc:
            logger.error("Recording failed: %s", exc)
            self._set_state(VoiceState.IDLE)
            return None

        if not pcm:
            logger.info("No speech captured; returning to idle.")
            self._set_state(VoiceState.IDLE)
            return None

        # PROCESSING: transcribe + generate response.
        self._set_state(VoiceState.PROCESSING)
        try:
            text = self.stt.transcribe_pcm(pcm, sample_rate=self.audio.sample_rate)
        except Exception as exc:
            logger.error("Transcription failed: %s", exc)
            self._set_state(VoiceState.IDLE)
            return None

        logger.info("Recognized: %r", text)
        reply = self._generate_reply(text)

        # SPEAKING: synthesize + play the reply.
        self._speak(reply)

        self._set_state(VoiceState.IDLE)
        return reply

    def _generate_reply(self, text: str) -> str:
        """Turn recognized ``text`` into a reply via the router or handler.

        Uses the :class:`IntentRouter` (with conversation context) when the
        router path is active; otherwise falls back to the legacy
        ``response_handler``. Always returns a speakable string; never raises.
        """
        # Legacy/simple handler path (e.g. M1.2 echo handler in tests).
        if not self._use_router and self.response_handler is not None:
            try:
                reply = self.response_handler(text)
            except Exception as exc:
                logger.error("Response handler failed: %s", exc)
                reply = "Sorry, something went wrong while processing that."
            self._learn_from_exchange(text, reply)
            return reply

        # Intent-router path (M1.3): classify -> skill -> reply, with context.
        try:
            context = self.conversation.get_context_messages()
        except Exception:  # pragma: no cover - context is best-effort
            context = None

        try:
            result = self.router.route(text, context=context)
            reply = result.text
            intent = result.metadata.get("intent")
        except Exception as exc:
            logger.exception("Intent routing failed: %s", exc)
            reply = "Sorry, something went wrong while processing that."
            intent = None

        # Record the turn for multi-turn context (best-effort).
        try:
            self.conversation.add_turn(user_text=text, assistant_text=reply, intent=intent)
        except Exception:  # pragma: no cover
            pass

        # Feed the exchange to the continuous learner (opt-in, best-effort).
        self._learn_from_exchange(text, reply)

        return reply

    def _learn_from_exchange(self, user_text: str, reply: str) -> None:
        """Hand one exchange to the continuous learner, if configured.

        Opt-in and strictly best-effort: a learning failure never gates or
        interrupts the conversation loop.
        """
        if self._continuous_learner is None:
            return
        try:
            self._continuous_learner.analyze_conversation(user_text, reply)
        except Exception:  # pragma: no cover - defensive
            logger.debug("Continuous learning step failed", exc_info=True)

    def run(self) -> None:
        """Run the loop continuously until :meth:`stop` is called.

        Each iteration is a full turn (wake -> listen -> process -> speak).
        Exceptions in a single turn are logged and the loop continues, so a
        transient audio glitch won't kill the assistant.
        """
        self._stop_requested = False

        # Item #3: probe the audio hardware exactly ONCE up front. In headless /
        # no-audio environments (VMs, servers, broken sound stacks) there is no
        # input device, so we log a single clear warning and disable voice
        # instead of spinning the loop and spamming the same error every
        # iteration.
        available, reason = self._probe_audio()
        self.audio_available = available
        if not available:
            self.audio_unavailable_reason = reason
            logger.warning(
                "No audio input device found: %s. Voice features disabled.",
                reason,
            )
            self._set_state(VoiceState.STOPPED)
            return

        logger.info("Voice loop starting. Say the wake word to begin.")
        try:
            while not self._stop_requested:
                # When paused, idle quietly without touching the microphone so
                # the user can drive MimOSA from the chat window instead.
                if self._paused:
                    time.sleep(0.15)
                    continue
                try:
                    self.run_once(wait_for_wake=True)
                except KeyboardInterrupt:
                    raise
                except Exception as exc:  # keep the loop alive on per-turn errors
                    logger.exception("Unhandled error during voice turn: %s", exc)
                    self._set_state(VoiceState.IDLE)
        except KeyboardInterrupt:
            logger.info("Voice loop interrupted by user.")
        finally:
            self._set_state(VoiceState.STOPPED)
            self.shutdown()

    # -- helpers -----------------------------------------------------------

    def _probe_audio(self) -> "tuple[bool, str]":
        """Check for a usable audio input device exactly once (item #3).

        Delegates to :meth:`AudioManager.check_audio_available`, using the
        loop's own audio manager when it exposes the method, else the class-level
        static probe. Never raises -- returns ``(available, reason)``.
        """
        try:
            from mimosa.voice.audio_manager import AudioManager

            probe = getattr(self._audio, "check_audio_available", None)
            if probe is None:
                probe = AudioManager.check_audio_available
            return probe()
        except Exception as exc:  # pragma: no cover - defensive
            return False, f"audio probe failed: {exc}"

    def _await_wake_word(self) -> bool:
        """Block in IDLE until the wake word fires. Returns False if stopped."""
        self._set_state(VoiceState.IDLE)
        detected = {"hit": False}

        def _on_detected() -> None:
            detected["hit"] = True

        try:
            self.wake.listen(
                self.audio,
                on_detected=_on_detected,
                should_stop=lambda: self._stop_requested or self._paused or detected["hit"],
            )
        except Exception as exc:
            logger.error("Wake-word listening failed: %s", exc)
            return False

        return detected["hit"] and not self._stop_requested

    def _speak(self, reply: str) -> None:
        """Synthesize ``reply`` with TTS and play it; degrade gracefully.
        
        M8.4: Estimates speech duration and notifies avatar renderer for lip sync.
        """
        if not reply:
            return
        self._set_state(VoiceState.SPEAKING)
        
        # M8.4: Estimate speech duration for avatar lip sync
        speech_duration = self._estimate_speech_duration(reply)
        
        # Notify speech start (for avatar lip sync animation)
        self._notify_speech_start(reply, speech_duration)
        
        try:
            wav_bytes = self.tts.synthesize(reply)
            self.audio.play_wav_bytes(wav_bytes)
        except Exception as exc:
            # TTS/playback failures should not crash the loop -- log the reply
            # so the interaction is still observable in text.
            logger.error("Speech output failed (%s). Reply was: %r", exc, reply)
        finally:
            # Notify speech end
            self._notify_speech_end()
    
    def _estimate_speech_duration(self, text: str) -> float:
        """
        Estimate speech duration from text.
        
        Args:
            text: Text to be spoken
            
        Returns:
            Estimated duration in seconds
        """
        try:
            from mimosa.avatar.viseme_mapper import estimate_speech_duration
            return estimate_speech_duration(text, wpm=150)
        except Exception:
            # Fallback: very rough estimate (assume 150 WPM)
            words = len(text.split())
            return max(1.0, words / 150.0 * 60.0)
    
    def _notify_speech_start(self, text: str, duration: float) -> None:
        """
        Notify observers that speech has started.
        
        Args:
            text: Text being spoken
            duration: Estimated duration in seconds
        """
        # Check if we have a speech callback registered
        callback = getattr(self, '_speech_start_callback', None)
        if callback and callable(callback):
            try:
                callback(text, duration)
            except Exception as exc:
                logger.debug("Speech start callback failed: %s", exc)
    
    def _notify_speech_end(self) -> None:
        """Notify observers that speech has ended."""
        callback = getattr(self, '_speech_end_callback', None)
        if callback and callable(callback):
            try:
                callback()
            except Exception as exc:
                logger.debug("Speech end callback failed: %s", exc)
    
    def set_speech_callbacks(self, on_start=None, on_end=None):
        """
        Register callbacks for speech events (M8.4: avatar lip sync integration).
        
        Args:
            on_start: Callable[(text: str, duration: float), None] - called when speech starts
            on_end: Callable[[], None] - called when speech ends
        """
        if on_start is not None:
            self._speech_start_callback = on_start
        if on_end is not None:
            self._speech_end_callback = on_end

    def shutdown(self) -> None:
        """Release backend resources (audio, wake-word engine)."""
        try:
            if self._wake is not None:
                self._wake.delete()
        except Exception:  # pragma: no cover - best effort cleanup
            pass
        try:
            if self._audio is not None:
                self._audio.close()
        except Exception:  # pragma: no cover - best effort cleanup
            pass
        logger.info("Voice loop shut down.")

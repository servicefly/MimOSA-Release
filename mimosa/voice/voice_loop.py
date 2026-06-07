"""The MimOSA voice loop: a state machine tying the local voice stack together.

This module orchestrates the full **local-first** voice interaction cycle:

    IDLE  --(wake word)-->  LISTENING  --(speech captured)-->  PROCESSING
      ^                                                            |
      |                                                            v
   SPEAKING  <--------------------(response synthesized)----------+

Concretely, one turn is:

1. **IDLE** -- wait for the wake word (Porcupine or the energy fallback).
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
    ) -> None:
        self._audio = audio_manager
        # Preferred microphone (PyAudio input-device index), chosen in the setup
        # wizard / Settings. ``None`` means "use the system default device".
        self._input_device_index = input_device_index
        self._wake = wake_word_detector
        self._stt = stt
        self._tts = tts
        self._router = intent_router
        self._error_reporter = error_reporter
        self._personality = personality
        self._conversation = conversation_manager
        # When an explicit response_handler is given (and no router), use the
        # legacy handler path; otherwise the intent router drives responses.
        self.response_handler: Optional[ResponseHandler] = response_handler
        self._use_router = response_handler is None or intent_router is not None
        self.max_record_seconds = max_record_seconds

        self._state = VoiceState.IDLE
        self._stop_requested = False
        self._paused = False
        self._state_listeners: list = []

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

            self._audio = AudioManager(device_index=self._input_device_index)
        return self._audio

    @property
    def wake(self):
        """The wake-word detector, created on first access (never raises)."""
        if self._wake is None:
            from mimosa.voice.wake_word import create_wake_word_detector

            wake_word = os.getenv("WAKE_WORD", "hey mimosa")
            self._wake = create_wake_word_detector(wake_word)
        return self._wake

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

            self._tts = create_tts()
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

            try:
                provider = create_provider()
            except Exception as exc:  # provider misconfig shouldn't crash setup
                logger.warning("Could not create LLM provider (%s); "
                               "LLM-backed skills will degrade.", exc)
                provider = None

            # Load user-defined custom skills (M4.1) from the unified config so
            # the user's own commands are active. Best-effort: a missing/corrupt
            # config simply means no custom skills.
            custom_skills = []
            try:
                from mimosa.skills.custom_skill import build_custom_skills
                from mimosa.utils.config import AppConfigManager

                cfg = AppConfigManager()
                cfg.load()
                custom_skills = build_custom_skills(
                    cfg.get().skills.custom_specs(), llm_provider=provider
                )
            except Exception as exc:  # pragma: no cover - config optional
                logger.debug("Could not load custom skills (%s)", exc)

            self._router = IntentRouter(
                llm_provider=provider,
                custom_skills=custom_skills,
                error_reporter=self._error_reporter,
                personality=self._personality,
            )
        return self._router

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
                return self.response_handler(text)
            except Exception as exc:
                logger.error("Response handler failed: %s", exc)
                return "Sorry, something went wrong while processing that."

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

        return reply

    def run(self) -> None:
        """Run the loop continuously until :meth:`stop` is called.

        Each iteration is a full turn (wake -> listen -> process -> speak).
        Exceptions in a single turn are logged and the loop continues, so a
        transient audio glitch won't kill the assistant.
        """
        self._stop_requested = False
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
        """Synthesize ``reply`` with TTS and play it; degrade gracefully."""
        if not reply:
            return
        self._set_state(VoiceState.SPEAKING)
        try:
            wav_bytes = self.tts.synthesize(reply)
            self.audio.play_wav_bytes(wav_bytes)
        except Exception as exc:
            # TTS/playback failures should not crash the loop -- log the reply
            # so the interaction is still observable in text.
            logger.error("Speech output failed (%s). Reply was: %r", exc, reply)

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

"""Wake-word detection for MimOSA.

The wake word ("Hey MimOSA" by default) is what takes MimOSA from a low-power
idle state into active listening. Detection runs **entirely on-device** with no
API keys, accounts, or network calls required at runtime.

Two backends are provided behind a common :class:`BaseWakeWord` interface:

* :class:`OpenWakeWord` -- accurate, low-CPU keyword spotting using
  `openWakeWord <https://github.com/dscripka/openWakeWord>`_. It is 100% local
  and free: small ONNX/TFLite models run on the CPU, no key needed. This is the
  recommended backend. Until a custom "Hey MimOSA" model is trained, a bundled
  pre-trained model is used as a close stand-in (see
  :data:`OPENWAKEWORD_DEFAULT_MODEL`).
* :class:`EnergyWakeWord` -- a dependency-free fallback that triggers on a
  sustained burst of audio energy (voice activity). It is **not** true keyword
  spotting -- it cannot tell *what* you said -- but it keeps the pipeline
  usable in headless/CI environments and when openWakeWord cannot load.

Use :func:`create_wake_word_detector` to get the best available backend based
on configuration; it falls back automatically and never raises just because a
package or model is missing.

Each detector consumes fixed-size frames of 16-bit PCM mono audio via
:meth:`BaseWakeWord.process`, returning ``True`` on the frame where the wake
word is detected. :meth:`BaseWakeWord.listen` drives a continuous background
loop using an :class:`~mimosa.voice.audio_manager.AudioManager`.
"""

from __future__ import annotations

import abc
import logging
import os
from typing import Callable, Optional

logger = logging.getLogger(__name__)

#: Default human-readable wake phrase.
DEFAULT_WAKE_WORD = "hey mimosa"

#: openWakeWord ships several free, pre-trained models (no key needed):
#: ``alexa``, ``hey_mycroft``, ``hey_jarvis``, ``hey_rhasspy``, ``timer`` and
#: ``weather``. "mimosa" is not yet a bundled model, so we map known phrases to
#: the closest pre-trained stand-in. A future milestone lets users train a real
#: "Hey MimOSA" model and drop it in via ``model_path``.
OPENWAKEWORD_BUILTIN_MODELS = {
    "alexa": "alexa",
    "hey mycroft": "hey_mycroft",
    "hey_mycroft": "hey_mycroft",
    "hey jarvis": "hey_jarvis",
    "hey_jarvis": "hey_jarvis",
    "hey rhasspy": "hey_rhasspy",
    "hey_rhasspy": "hey_rhasspy",
}

#: Stand-in pre-trained model used when the requested phrase has no bundled
#: openWakeWord model (e.g. "hey mimosa"). Users wanting the exact phrase can
#: train a custom model and supply its path via ``model_path``.
OPENWAKEWORD_DEFAULT_MODEL = "hey_jarvis"

#: Default detection threshold in ``[0, 1]``; higher = stricter (fewer false
#: positives, more missed wakes).
DEFAULT_OPENWAKEWORD_THRESHOLD = 0.5


class WakeWordError(RuntimeError):
    """Raised when a wake-word backend cannot be initialized or run."""


class BaseWakeWord(abc.ABC):
    """Abstract interface every wake-word backend implements.

    Attributes:
        wake_word: The configured wake phrase (informational).
        frame_length: Number of samples expected per :meth:`process` call.
        sample_rate: Required input sample rate in Hz.
    """

    name: str = "base"

    def __init__(self, wake_word: str, frame_length: int, sample_rate: int) -> None:
        self.wake_word = wake_word
        self.frame_length = frame_length
        self.sample_rate = sample_rate

    @abc.abstractmethod
    def process(self, pcm_frame: bytes) -> bool:
        """Process one frame of audio.

        Args:
            pcm_frame: Exactly :attr:`frame_length` samples of 16-bit PCM mono.

        Returns:
            ``True`` if the wake word was detected on this frame.
        """
        raise NotImplementedError

    def listen(
        self,
        audio_manager,
        on_detected: Callable[[], None],
        should_stop: Optional[Callable[[], bool]] = None,
    ) -> None:
        """Continuously read frames and invoke ``on_detected`` on a hit.

        This is a blocking loop intended to run on a background thread. It uses
        low-CPU, fixed-size reads so idle listening stays cheap.

        Args:
            audio_manager: An :class:`~mimosa.voice.audio_manager.AudioManager`
                providing the input stream.
            on_detected: Callback invoked (no args) each time the wake word
                fires.
            should_stop: Optional predicate; when it returns ``True`` the loop
                exits. Defaults to running forever.

        Raises:
            AudioUnavailableError: If no microphone backend is available.
        """
        pa = audio_manager._ensure_backend()  # raises AudioUnavailableError
        stream = pa.open(
            format=audio_manager._format,
            channels=1,
            rate=self.sample_rate,
            input=True,
            frames_per_buffer=self.frame_length,
            input_device_index=audio_manager.input_device,
        )
        should_stop = should_stop or (lambda: False)
        logger.info("Wake-word listening started (%s backend).", self.name)
        try:
            while not should_stop():
                frame = stream.read(self.frame_length, exception_on_overflow=False)
                if self.process(frame):
                    logger.info("Wake word detected.")
                    on_detected()
        finally:
            stream.stop_stream()
            stream.close()
            logger.info("Wake-word listening stopped.")

    def delete(self) -> None:
        """Release any backend resources. Override if needed."""

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return f"{type(self).__name__}(wake_word={self.wake_word!r})"


class OpenWakeWord(BaseWakeWord):
    """openWakeWord-based detector (recommended, 100% local, no API key).

    openWakeWord runs small ONNX/TFLite models on the CPU. The models are
    downloaded once (a few MB) and cached locally; nothing is sent anywhere at
    runtime.

    Args:
        wake_word: Desired wake phrase. If it matches a bundled openWakeWord
            model it is used directly; otherwise a pre-trained stand-in
            (:data:`OPENWAKEWORD_DEFAULT_MODEL`) is used unless ``model_path``
            is given.
        model_path: Optional path to a custom ``.onnx``/``.tflite`` wake-word
            model (use this for a true "Hey MimOSA" model).
        threshold: Detection threshold in ``[0, 1]``; higher = stricter.
        inference_framework: ``"onnx"`` or ``"tflite"``; defaults to whichever
            openWakeWord prefers.

    Raises:
        WakeWordError: If ``openwakeword`` is unavailable or initialization
            fails (e.g. models cannot be downloaded/loaded).
    """

    name = "openwakeword"

    #: openWakeWord requires 16 kHz mono audio fed in 80 ms (1280-sample) chunks.
    SAMPLE_RATE = 16000
    FRAME_LENGTH = 1280

    def __init__(
        self,
        wake_word: str = DEFAULT_WAKE_WORD,
        model_path: Optional[str] = None,
        threshold: float = DEFAULT_OPENWAKEWORD_THRESHOLD,
        inference_framework: Optional[str] = None,
    ) -> None:
        try:
            import numpy as np
            from openwakeword.model import Model
        except Exception as exc:  # ImportError or load failure
            raise WakeWordError(
                "openwakeword is not installed. Run `pip install openwakeword` "
                "or use the energy-based fallback."
            ) from exc

        self._np = np
        self.threshold = max(0.0, min(1.0, float(threshold)))

        # Resolve which model(s) to load.
        model_path = model_path or os.getenv("MIMOSA_WAKEWORD_MODEL")
        kwargs = {}
        if inference_framework:
            kwargs["inference_framework"] = inference_framework

        try:
            if model_path:
                self._engine = Model(wakeword_models=[model_path], **kwargs)
                self._model_key = None  # match on any/all loaded models
            else:
                model_name = self._resolve_builtin_model(wake_word)
                # Ensure the bundled pre-trained models are present locally
                # (best-effort; a fresh install downloads a few MB once).
                self._ensure_models_downloaded()
                self._engine = Model(wakeword_models=[model_name], **kwargs)
                self._model_key = model_name
        except WakeWordError:
            raise
        except Exception as exc:
            raise WakeWordError(f"Failed to initialize openWakeWord: {exc}") from exc

        super().__init__(
            wake_word=wake_word,
            frame_length=self.FRAME_LENGTH,
            sample_rate=self.SAMPLE_RATE,
        )

    @staticmethod
    def _ensure_models_downloaded() -> None:
        """Best-effort download of bundled openWakeWord models."""
        try:
            from openwakeword.utils import download_models

            download_models()
        except Exception:  # pragma: no cover - offline / already present
            logger.debug("openWakeWord model download skipped", exc_info=True)

    @staticmethod
    def _resolve_builtin_model(wake_word: str) -> str:
        """Map a requested phrase to a bundled openWakeWord model name.

        "Hey MimOSA" has no bundled model, so we fall back to a close
        pre-trained model and log a hint about training a custom one.
        """
        normalized = (wake_word or "").strip().lower()
        if normalized in OPENWAKEWORD_BUILTIN_MODELS:
            return OPENWAKEWORD_BUILTIN_MODELS[normalized]
        logger.warning(
            "Wake word %r has no bundled openWakeWord model; using %r as a "
            "stand-in. Train a custom model and pass model_path for the exact "
            "phrase.",
            wake_word,
            OPENWAKEWORD_DEFAULT_MODEL,
        )
        return OPENWAKEWORD_DEFAULT_MODEL

    def process(self, pcm_frame: bytes) -> bool:
        """Return ``True`` if openWakeWord detects the keyword in this frame."""
        num_samples = len(pcm_frame) // 2
        if num_samples == 0:
            return False
        samples = self._np.frombuffer(
            pcm_frame[: num_samples * 2], dtype=self._np.int16
        )
        try:
            scores = self._engine.predict(samples)
        except Exception:  # pragma: no cover - defensive
            logger.debug("openWakeWord predict failed", exc_info=True)
            return False
        if not scores:
            return False
        if self._model_key is not None and self._model_key in scores:
            return float(scores[self._model_key]) >= self.threshold
        # Custom model / unknown key: trigger if any score crosses the threshold.
        return any(float(v) >= self.threshold for v in scores.values())

    def delete(self) -> None:
        """Release the openWakeWord engine and reset model state."""
        engine = getattr(self, "_engine", None)
        if engine is not None:
            try:
                reset = getattr(engine, "reset", None)
                if callable(reset):
                    reset()
            except Exception:  # pragma: no cover - defensive
                pass


class EnergyWakeWord(BaseWakeWord):
    """Dependency-free fallback wake "word" based on audio energy.

    .. warning::
        This is **not** keyword spotting. It triggers on any sustained sound
        louder than ``threshold`` for ``trigger_frames`` consecutive frames --
        it cannot distinguish "Hey MimOSA" from other speech or noise. It
        exists so the pipeline remains usable for local testing and in
        environments where openWakeWord cannot load. Prefer
        :class:`OpenWakeWord` for real use.

    Args:
        wake_word: Informational label for the configured phrase.
        sample_rate: Input sample rate (Hz).
        frame_length: Samples per :meth:`process` call.
        threshold: RMS amplitude that counts as "loud".
        trigger_frames: Consecutive loud frames required to fire.
    """

    name = "energy"

    def __init__(
        self,
        wake_word: str = DEFAULT_WAKE_WORD,
        sample_rate: int = 16000,
        frame_length: int = 512,
        threshold: int = 1000,
        trigger_frames: int = 5,
    ) -> None:
        super().__init__(wake_word, frame_length, sample_rate)
        self.threshold = threshold
        self.trigger_frames = trigger_frames
        self._loud_streak = 0

    def process(self, pcm_frame: bytes) -> bool:
        """Return ``True`` after enough consecutive loud frames."""
        # Local import avoids a hard dependency and reuses the RMS helper.
        from mimosa.voice.audio_manager import AudioManager

        if AudioManager.rms(pcm_frame) >= self.threshold:
            self._loud_streak += 1
        else:
            self._loud_streak = 0

        if self._loud_streak >= self.trigger_frames:
            self._loud_streak = 0
            return True
        return False


def create_wake_word_detector(
    wake_word: str = DEFAULT_WAKE_WORD,
    *,
    model_path: Optional[str] = None,
    threshold: float = DEFAULT_OPENWAKEWORD_THRESHOLD,
    prefer_openwakeword: bool = True,
) -> BaseWakeWord:
    """Return the best available wake-word backend.

    Tries openWakeWord first (when ``prefer_openwakeword`` and the package are
    available) and transparently falls back to :class:`EnergyWakeWord` if
    openWakeWord cannot be initialized. Never raises due to a missing package or
    model -- it logs and falls back instead.

    Args:
        wake_word: Desired wake phrase (default ``"hey mimosa"``).
        model_path: Optional custom model path for the exact phrase.
        threshold: openWakeWord detection threshold in ``[0, 1]``.
        prefer_openwakeword: Set ``False`` to force the energy fallback.

    Returns:
        A ready-to-use :class:`BaseWakeWord` instance.
    """
    if prefer_openwakeword:
        try:
            detector = OpenWakeWord(
                wake_word=wake_word,
                model_path=model_path,
                threshold=threshold,
            )
            logger.info("Using openWakeWord wake-word backend (local, no key).")
            return detector
        except WakeWordError as exc:
            logger.warning(
                "openWakeWord unavailable (%s). Falling back to energy-based "
                "detection. Wake-word accuracy will be limited.",
                exc,
            )

    return EnergyWakeWord(wake_word=wake_word)

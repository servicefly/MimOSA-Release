"""Wake-word detection for MimOSA.

The wake word ("Hey MimOSA" by default) is what takes MimOSA from a low-power
idle state into active listening. Detection runs **entirely on-device**.

Two backends are provided behind a common :class:`BaseWakeWord` interface:

* :class:`PorcupineWakeWord` -- accurate, low-CPU keyword spotting using
  `Picovoice Porcupine <https://picovoice.ai/platform/porcupine/>`_. Requires
  the ``pvporcupine`` package and a free access key. This is the recommended
  backend.
* :class:`EnergyWakeWord` -- a dependency-free fallback that triggers on a
  sustained burst of audio energy (voice activity). It is **not** true keyword
  spotting -- it cannot tell *what* you said -- but it keeps the pipeline
  usable for local testing when Porcupine isn't configured, and in headless/CI
  environments. Limitations are documented on the class.

Use :func:`create_wake_word_detector` to get the best available backend based
on configuration; it falls back automatically and never raises just because a
key or package is missing.

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

#: Default human-readable wake phrase. Porcupine maps this to a built-in
#: keyword where possible; otherwise the closest built-in is used.
DEFAULT_WAKE_WORD = "hey mimosa"

#: Porcupine built-in keywords that ship with the library (no custom model
#: file needed). "mimosa" is not built in, so we map to a close stand-in.
PORCUPINE_BUILTIN_KEYWORDS = {
    "porcupine", "bumblebee", "alexa", "computer", "jarvis", "hey google",
    "hey siri", "ok google", "picovoice", "blueberry", "grapefruit",
    "grasshopper", "terminator",
}

#: Fallback built-in keyword used when the requested phrase has no built-in
#: Porcupine model (e.g. "hey mimosa"). Users wanting the real phrase can
#: supply a custom ``.ppn`` keyword file via ``keyword_paths``.
PORCUPINE_FALLBACK_KEYWORD = "jarvis"


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


class PorcupineWakeWord(BaseWakeWord):
    """Porcupine-based wake-word detector (recommended, low CPU).

    Args:
        access_key: Picovoice access key. Falls back to the
            ``PORCUPINE_ACCESS_KEY`` env var.
        wake_word: Desired wake phrase. If it matches a Porcupine built-in
            keyword it is used directly; otherwise a built-in stand-in
            (:data:`PORCUPINE_FALLBACK_KEYWORD`) is used unless ``keyword_paths``
            is given.
        keyword_paths: Optional list of custom ``.ppn`` keyword model paths
            (use this for a true "Hey MimOSA" model).
        sensitivity: Detection sensitivity in ``[0, 1]``; higher = more
            sensitive (more true hits, more false positives).

    Raises:
        WakeWordError: If ``pvporcupine`` is unavailable or initialization
            fails (e.g. missing/invalid access key).
    """

    name = "porcupine"

    def __init__(
        self,
        access_key: Optional[str] = None,
        wake_word: str = DEFAULT_WAKE_WORD,
        keyword_paths: Optional[list] = None,
        sensitivity: float = 0.5,
    ) -> None:
        try:
            import pvporcupine
        except Exception as exc:  # ImportError or load failure
            raise WakeWordError(
                "pvporcupine is not installed. Run `pip install pvporcupine` "
                "or use the energy-based fallback."
            ) from exc

        key = access_key or os.getenv("PORCUPINE_ACCESS_KEY")
        if not key:
            raise WakeWordError(
                "No Porcupine access key. Set PORCUPINE_ACCESS_KEY (free key "
                "from https://console.picovoice.ai/) or use the fallback."
            )

        try:
            if keyword_paths:
                self._engine = pvporcupine.create(
                    access_key=key,
                    keyword_paths=keyword_paths,
                    sensitivities=[sensitivity] * len(keyword_paths),
                )
            else:
                keyword = self._resolve_builtin_keyword(wake_word)
                self._engine = pvporcupine.create(
                    access_key=key,
                    keywords=[keyword],
                    sensitivities=[sensitivity],
                )
        except Exception as exc:
            raise WakeWordError(f"Failed to initialize Porcupine: {exc}") from exc

        super().__init__(
            wake_word=wake_word,
            frame_length=self._engine.frame_length,
            sample_rate=self._engine.sample_rate,
        )

    @staticmethod
    def _resolve_builtin_keyword(wake_word: str) -> str:
        """Map a requested phrase to a Porcupine built-in keyword.

        "Hey MimOSA" has no built-in model, so we fall back to a close
        built-in keyword and log a hint about supplying a custom ``.ppn``.
        """
        normalized = wake_word.strip().lower()
        if normalized in PORCUPINE_BUILTIN_KEYWORDS:
            return normalized
        logger.warning(
            "Wake word %r has no built-in Porcupine model; using %r instead. "
            "Provide a custom .ppn via keyword_paths for the exact phrase.",
            wake_word,
            PORCUPINE_FALLBACK_KEYWORD,
        )
        return PORCUPINE_FALLBACK_KEYWORD

    def process(self, pcm_frame: bytes) -> bool:
        """Return ``True`` if Porcupine detects the keyword in this frame."""
        import struct

        num_samples = len(pcm_frame) // 2
        if num_samples == 0:
            return False
        samples = struct.unpack(f"<{num_samples}h", pcm_frame[: num_samples * 2])
        return self._engine.process(samples) >= 0

    def delete(self) -> None:
        """Release the Porcupine engine."""
        engine = getattr(self, "_engine", None)
        if engine is not None:
            try:
                engine.delete()
            except Exception:  # pragma: no cover - defensive
                pass


class EnergyWakeWord(BaseWakeWord):
    """Dependency-free fallback wake "word" based on audio energy.

    .. warning::
        This is **not** keyword spotting. It triggers on any sustained sound
        louder than ``threshold`` for ``trigger_frames`` consecutive frames --
        it cannot distinguish "Hey MimOSA" from other speech or noise. It
        exists so the pipeline remains usable for local testing and in
        environments where Porcupine is not configured. Prefer
        :class:`PorcupineWakeWord` for real use.

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
    access_key: Optional[str] = None,
    keyword_paths: Optional[list] = None,
    sensitivity: float = 0.5,
    prefer_porcupine: bool = True,
) -> BaseWakeWord:
    """Return the best available wake-word backend.

    Tries Porcupine first (when ``prefer_porcupine`` and a key/package are
    available) and transparently falls back to :class:`EnergyWakeWord` if
    Porcupine cannot be initialized. Never raises due to a missing key or
    package -- it logs and falls back instead.

    Args:
        wake_word: Desired wake phrase (default ``"hey mimosa"``).
        access_key: Picovoice key; falls back to ``PORCUPINE_ACCESS_KEY`` env.
        keyword_paths: Optional custom ``.ppn`` model paths for the exact
            phrase.
        sensitivity: Porcupine sensitivity in ``[0, 1]``.
        prefer_porcupine: Set ``False`` to force the energy fallback.

    Returns:
        A ready-to-use :class:`BaseWakeWord` instance.
    """
    if prefer_porcupine:
        try:
            detector = PorcupineWakeWord(
                access_key=access_key,
                wake_word=wake_word,
                keyword_paths=keyword_paths,
                sensitivity=sensitivity,
            )
            logger.info("Using Porcupine wake-word backend.")
            return detector
        except WakeWordError as exc:
            logger.warning(
                "Porcupine unavailable (%s). Falling back to energy-based "
                "detection. Wake-word accuracy will be limited.",
                exc,
            )

    return EnergyWakeWord(wake_word=wake_word)

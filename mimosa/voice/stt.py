"""Local Speech-to-Text (STT) for MimOSA, powered by OpenAI Whisper.

Privacy rationale
-----------------
Transcription runs **entirely on-device** using ``openai-whisper``. Spoken
audio is never uploaded to any cloud service -- this is a core MimOSA privacy
guarantee. The only network access Whisper needs is a *one-time* model
download (cached under ``~/.cache/whisper``); after that it works fully
offline.

Design notes
------------
* **Lazy imports** -- ``whisper``/``numpy`` are imported inside methods, never
  at module import time. Importing :mod:`mimosa.voice.stt` therefore always
  succeeds even on a headless VM with no ML stack installed. A clear
  :class:`STTError` is raised only when transcription is actually attempted
  without the dependency present.
* **Model size is configurable** via the ``WHISPER_MODEL`` env var (or the
  ``model_size`` constructor argument). Smaller models (``tiny``/``base``) are
  fast and CPU-friendly; larger ones (``small``/``medium``) are more accurate
  but heavier. See :data:`WHISPER_MODELS`.
* The model is loaded lazily on first use and cached for the lifetime of the
  instance, so repeated transcriptions do not pay the load cost again.

Typical use::

    stt = WhisperSTT(model_size="base")
    text = stt.transcribe_pcm(pcm_bytes, sample_rate=16_000)
"""

from __future__ import annotations

import logging
import os
import wave
from typing import Optional, Union

logger = logging.getLogger(__name__)

# Whisper sample rate. Whisper internally resamples to 16 kHz, so feeding it
# 16 kHz mono audio avoids an extra resample and matches AudioManager defaults.
WHISPER_SAMPLE_RATE = 16_000

# Recognised model sizes, smallest/fastest first. ``.en`` variants are
# English-only and slightly more accurate for English at the same size.
WHISPER_MODELS = (
    "tiny",
    "tiny.en",
    "base",
    "base.en",
    "small",
    "small.en",
    "medium",
    "medium.en",
    "large",
)

DEFAULT_WHISPER_MODEL = "base"


class STTError(RuntimeError):
    """Raised when speech-to-text cannot be performed.

    Common causes: the ``openai-whisper`` package is not installed, the model
    failed to download/load, or the supplied audio could not be decoded.
    """


class WhisperSTT:
    """Local speech-to-text engine backed by OpenAI Whisper.

    Args:
        model_size: One of :data:`WHISPER_MODELS`. Defaults to the
            ``WHISPER_MODEL`` env var, then :data:`DEFAULT_WHISPER_MODEL`.
        device: Torch device hint (``"cpu"`` or ``"cuda"``). ``None`` lets
            Whisper auto-select (CPU on machines without a GPU).
        language: Optional ISO language code (e.g. ``"en"``) to skip Whisper's
            auto language detection and speed things up. ``None`` = autodetect.
        download_root: Optional directory for cached model weights.

    Note:
        Constructing this object is cheap and never touches Whisper -- the
        model is only loaded on the first :meth:`transcribe` call (or an
        explicit :meth:`load` call).
    """

    def __init__(
        self,
        model_size: Optional[str] = None,
        device: Optional[str] = None,
        language: Optional[str] = None,
        download_root: Optional[str] = None,
    ) -> None:
        self.model_size = (
            model_size
            or os.getenv("WHISPER_MODEL")
            or DEFAULT_WHISPER_MODEL
        ).strip()
        if self.model_size not in WHISPER_MODELS:
            logger.warning(
                "Unknown Whisper model '%s'; expected one of %s. "
                "Passing it through anyway in case it is a custom/local model.",
                self.model_size,
                ", ".join(WHISPER_MODELS),
            )
        self.device = device
        self.language = language
        self.download_root = download_root
        self._model = None  # lazily loaded whisper model

    # -- model lifecycle ---------------------------------------------------

    def is_available(self) -> bool:
        """Return ``True`` if the ``whisper`` package can be imported.

        Does **not** load the model -- it only checks that the dependency is
        installed, so it is safe and fast to call (e.g. in a health check).
        """
        try:
            import whisper  # noqa: F401  (import probe only)

            return True
        except Exception:  # ImportError or transitive failure
            return False

    def load(self):
        """Load (and cache) the Whisper model, returning it.

        Raises:
            STTError: if ``whisper`` is not installed or the model fails to
                load (e.g. download failure when offline and uncached).
        """
        if self._model is not None:
            return self._model

        try:
            import whisper
        except Exception as exc:  # pragma: no cover - depends on environment
            raise STTError(
                "openai-whisper is not installed. Install it with "
                "'pip install openai-whisper' to enable local speech-to-text."
            ) from exc

        try:
            logger.info("Loading Whisper model '%s' (this may download on first run)...", self.model_size)
            self._model = whisper.load_model(
                self.model_size,
                device=self.device,
                download_root=self.download_root,
            )
            logger.info("Whisper model '%s' loaded.", self.model_size)
        except Exception as exc:
            raise STTError(
                f"Failed to load Whisper model '{self.model_size}': {exc}"
            ) from exc

        return self._model

    # -- transcription -----------------------------------------------------

    def transcribe_pcm(
        self,
        pcm: bytes,
        sample_rate: int = WHISPER_SAMPLE_RATE,
        sample_width: int = 2,
        channels: int = 1,
    ) -> str:
        """Transcribe raw 16-bit PCM audio to text.

        This is the primary entry point when wiring Whisper to
        :class:`~mimosa.voice.audio_manager.AudioManager`, whose ``record*``
        methods return raw little-endian 16-bit PCM bytes.

        Args:
            pcm: Raw PCM samples (little-endian signed 16-bit by default).
            sample_rate: Sample rate of ``pcm`` in Hz.
            sample_width: Bytes per sample (2 = 16-bit). Only 16-bit is
                supported for normalization.
            channels: Number of interleaved channels. Stereo is downmixed to
                mono by averaging.

        Returns:
            The transcribed text (stripped). Empty string if no speech.

        Raises:
            STTError: if Whisper is unavailable or audio decoding fails.
        """
        audio = self._pcm_to_float32(pcm, sample_rate, sample_width, channels)
        return self._transcribe_array(audio)

    def transcribe_file(self, path: str) -> str:
        """Transcribe an audio file (WAV/MP3/etc.) to text.

        WAV files are decoded with the stdlib :mod:`wave` module (no extra
        deps). Other formats are handed to Whisper directly, which uses
        ``ffmpeg`` under the hood.

        Args:
            path: Path to an audio file.

        Returns:
            The transcribed text (stripped).

        Raises:
            STTError: if the file is missing, undecodable, or Whisper is
                unavailable.
        """
        if not os.path.isfile(path):
            raise STTError(f"Audio file not found: {path}")

        if path.lower().endswith(".wav"):
            pcm, rate, width, channels = self._read_wav(path)
            return self.transcribe_pcm(pcm, rate, width, channels)

        # Non-WAV: let Whisper/ffmpeg handle decoding via its file loader.
        model = self.load()
        try:
            result = model.transcribe(path, language=self.language, fp16=False)
        except Exception as exc:
            raise STTError(f"Whisper failed to transcribe '{path}': {exc}") from exc
        return str(result.get("text", "")).strip()

    # -- internal helpers --------------------------------------------------

    def _transcribe_array(self, audio) -> str:
        """Run Whisper on a float32 numpy array normalized to [-1, 1]."""
        model = self.load()
        try:
            # fp16=False keeps things stable/portable on CPU-only machines.
            result = model.transcribe(audio, language=self.language, fp16=False)
        except Exception as exc:
            raise STTError(f"Whisper transcription failed: {exc}") from exc
        text = str(result.get("text", "")).strip()
        logger.debug("Whisper transcription: %r", text)
        return text

    @staticmethod
    def _pcm_to_float32(pcm: bytes, sample_rate: int, sample_width: int, channels: int):
        """Convert raw PCM bytes to a mono float32 numpy array in [-1, 1].

        Whisper expects 16 kHz mono float32. We normalize, downmix, and
        resample (linear) as needed so callers can pass whatever the mic
        produced.
        """
        try:
            import numpy as np
        except Exception as exc:  # pragma: no cover - numpy is a core dep
            raise STTError(
                "numpy is required for STT audio preprocessing but is not "
                "installed."
            ) from exc

        if sample_width != 2:
            raise STTError(
                f"Unsupported sample width {sample_width} bytes; expected 2 "
                "(16-bit PCM)."
            )

        if not pcm:
            return np.zeros(0, dtype=np.float32)

        # 16-bit signed little-endian -> float32 in [-1, 1].
        samples = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0

        # Downmix interleaved channels to mono by averaging.
        if channels > 1:
            usable = (samples.size // channels) * channels
            samples = samples[:usable].reshape(-1, channels).mean(axis=1)

        # Resample to Whisper's expected rate if necessary (linear interp).
        if sample_rate != WHISPER_SAMPLE_RATE and samples.size:
            duration = samples.size / float(sample_rate)
            target_len = int(round(duration * WHISPER_SAMPLE_RATE))
            if target_len > 0:
                src_idx = np.linspace(0, samples.size - 1, num=target_len)
                samples = np.interp(
                    src_idx, np.arange(samples.size), samples
                ).astype(np.float32)

        return np.ascontiguousarray(samples, dtype=np.float32)

    @staticmethod
    def _read_wav(path: str):
        """Read a WAV file into (pcm_bytes, sample_rate, sample_width, channels)."""
        try:
            with wave.open(path, "rb") as wf:
                channels = wf.getnchannels()
                width = wf.getsampwidth()
                rate = wf.getframerate()
                pcm = wf.readframes(wf.getnframes())
            return pcm, rate, width, channels
        except Exception as exc:
            raise STTError(f"Failed to read WAV file '{path}': {exc}") from exc


def create_stt(model_size: Optional[str] = None, **kwargs) -> WhisperSTT:
    """Factory for the default local STT engine.

    Currently always returns a :class:`WhisperSTT`. Exists so callers depend on
    a stable factory rather than a concrete class, leaving room for alternative
    local STT backends later.
    """
    return WhisperSTT(model_size=model_size, **kwargs)

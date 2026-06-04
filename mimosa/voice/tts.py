"""Local Text-to-Speech (TTS) for MimOSA, powered by Piper.

Privacy rationale
-----------------
Speech synthesis runs **entirely on-device** with `piper-tts
<https://github.com/rhasspy/piper>`_. MimOSA's spoken responses are generated
locally and never sent to a cloud TTS service. Voice model files (``.onnx`` +
``.onnx.json``) are downloaded once and then used fully offline.

Design notes
------------
* **Lazy imports** -- the ``piper`` package is imported inside methods, never
  at module import time, so importing :mod:`mimosa.voice.tts` always succeeds
  even on a headless VM without the TTS stack. A clear :class:`TTSError` is
  raised only when synthesis is actually attempted without the dependency.
* **Voice is configurable** via the ``PIPER_VOICE`` env var (default
  ``en_US-lessac-medium``) or the ``voice`` constructor argument. A voice can
  be given as a Piper voice *name* (resolved/downloaded by Piper) or as a
  filesystem path to a ``.onnx`` model.
* Output is standard 16-bit PCM WAV bytes, ready to hand straight to
  :meth:`mimosa.voice.audio_manager.AudioManager.play_wav_bytes`.

Typical use::

    tts = PiperTTS(voice="en_US-lessac-medium")
    wav_bytes = tts.synthesize("Hello, I am MimOSA.")
    audio_manager.play_wav_bytes(wav_bytes)
"""

from __future__ import annotations

import io
import logging
import os
import wave
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_PIPER_VOICE = "en_US-lessac-medium"


class TTSError(RuntimeError):
    """Raised when text-to-speech cannot be performed.

    Common causes: ``piper-tts`` not installed, the voice model could not be
    located/downloaded, or synthesis failed.
    """


class PiperTTS:
    """Local text-to-speech engine backed by Piper.

    Args:
        voice: Piper voice name (e.g. ``"en_US-lessac-medium"``) or a path to a
            ``.onnx`` voice model. Defaults to the ``PIPER_VOICE`` env var, then
            :data:`DEFAULT_PIPER_VOICE`.
        speed: Speaking rate multiplier. Piper expresses this as
            ``length_scale`` where *higher = slower*; we invert ``speed`` so a
            larger ``speed`` is faster and more intuitive. ``1.0`` is normal.
        data_dir: Optional directory to look for / download voice models into.

    Note:
        Construction is cheap and never imports Piper -- the voice model is
        loaded lazily on the first :meth:`synthesize` call (or :meth:`load`).
    """

    def __init__(
        self,
        voice: Optional[str] = None,
        speed: float = 1.0,
        data_dir: Optional[str] = None,
    ) -> None:
        self.voice = (
            voice or os.getenv("PIPER_VOICE") or DEFAULT_PIPER_VOICE
        ).strip()
        self.speed = float(speed) if speed and speed > 0 else 1.0
        self.data_dir = data_dir
        self._voice_obj = None  # lazily loaded piper PiperVoice

    # -- model lifecycle ---------------------------------------------------

    def is_available(self) -> bool:
        """Return ``True`` if the ``piper`` package can be imported.

        Does not load any voice model -- only checks the dependency, so it is
        safe/fast for health checks.
        """
        try:
            import piper  # noqa: F401  (import probe only)

            return True
        except Exception:
            return False

    def load(self):
        """Load (and cache) the Piper voice model, returning it.

        Resolution order for ``self.voice``:
          1. If it is an existing ``.onnx`` file path, load it directly.
          2. Otherwise treat it as a voice *name* and let Piper find/download
             it (newer ``piper`` exposes ``PiperVoice.download``/``find_voice``;
             we fall back gracefully across versions).

        Raises:
            TTSError: if Piper is unavailable or the voice cannot be loaded.
        """
        if self._voice_obj is not None:
            return self._voice_obj

        try:
            from piper import PiperVoice
        except Exception as exc:  # pragma: no cover - depends on environment
            raise TTSError(
                "piper-tts is not installed. Install it with "
                "'pip install piper-tts' to enable local text-to-speech."
            ) from exc

        try:
            model_path = self._resolve_voice_path(PiperVoice)
            logger.info("Loading Piper voice from '%s'...", model_path)
            self._voice_obj = PiperVoice.load(model_path)
            logger.info("Piper voice '%s' loaded.", self.voice)
        except TTSError:
            raise
        except Exception as exc:
            raise TTSError(f"Failed to load Piper voice '{self.voice}': {exc}") from exc

        return self._voice_obj

    def _resolve_voice_path(self, PiperVoice) -> str:
        """Resolve ``self.voice`` to a local ``.onnx`` model path.

        Handles both "voice is already a file path" and "voice is a name that
        Piper must locate/download" cases, tolerating API differences between
        piper-tts versions.
        """
        # Case 1: explicit path to an .onnx model.
        if self.voice.endswith(".onnx") and os.path.isfile(self.voice):
            return self.voice

        # Case 2: a name -- try Piper's own download/find helpers if present.
        download = getattr(PiperVoice, "download", None)
        if callable(download):
            try:
                return download(self.voice, data_dir=self.data_dir)
            except TypeError:
                # Older signature without data_dir.
                return download(self.voice)

        # Case 3: look for a cached model file named after the voice.
        search_dirs = [d for d in (self.data_dir, os.getcwd()) if d]
        for d in search_dirs:
            candidate = os.path.join(d, f"{self.voice}.onnx")
            if os.path.isfile(candidate):
                return candidate

        raise TTSError(
            f"Could not locate Piper voice '{self.voice}'. Provide a path to a "
            "'.onnx' voice model, place '<voice>.onnx' in the data directory, "
            "or use a newer piper-tts that can download voices automatically."
        )

    # -- synthesis ---------------------------------------------------------

    def synthesize(self, text: str) -> bytes:
        """Synthesize ``text`` into 16-bit PCM WAV bytes.

        The returned bytes are a complete, self-describing WAV container (with
        header), suitable for
        :meth:`mimosa.voice.audio_manager.AudioManager.play_wav_bytes`, writing
        to disk, or streaming to a client.

        Args:
            text: The text to speak.

        Returns:
            WAV file contents as ``bytes``. Empty/whitespace input yields a
            valid but silent (empty) WAV.

        Raises:
            TTSError: if Piper is unavailable or synthesis fails.
        """
        text = (text or "").strip()
        voice = self.load()

        buffer = io.BytesIO()
        try:
            with wave.open(buffer, "wb") as wav_file:
                # length_scale > 1 slows speech down; invert intuitive `speed`.
                length_scale = 1.0 / self.speed if self.speed else 1.0
                self._synthesize_to_wave(voice, text, wav_file, length_scale)
        except TTSError:
            raise
        except Exception as exc:
            raise TTSError(f"Piper synthesis failed: {exc}") from exc

        return buffer.getvalue()

    def synthesize_to_file(self, text: str, path: str) -> str:
        """Synthesize ``text`` and write the WAV to ``path``; return ``path``."""
        wav_bytes = self.synthesize(text)
        try:
            with open(path, "wb") as fh:
                fh.write(wav_bytes)
        except OSError as exc:
            raise TTSError(f"Failed to write synthesized audio to '{path}': {exc}") from exc
        return path

    @staticmethod
    def _synthesize_to_wave(voice, text: str, wav_file, length_scale: float) -> None:
        """Drive Piper's synthesis API, tolerating version differences.

        Different piper-tts releases expose slightly different methods. We try
        the common ones in order:
          * ``voice.synthesize_wav(text, wav_file, length_scale=...)``
          * ``voice.synthesize(text, wav_file, length_scale=...)``
          * ``voice.synthesize(text)`` returning audio chunks we write manually.
        """
        # Newer API: writes a full WAV (header + frames) for us.
        synth_wav = getattr(voice, "synthesize_wav", None)
        if callable(synth_wav):
            try:
                synth_wav(text, wav_file, length_scale=length_scale)
                return
            except TypeError:
                synth_wav(text, wav_file)
                return

        synth = getattr(voice, "synthesize", None)
        if not callable(synth):
            raise TTSError("Piper voice object exposes no known synthesize method.")

        # Some versions accept a wave_file target directly.
        try:
            synth(text, wav_file, length_scale=length_scale)
            return
        except TypeError:
            pass

        # Fallback: synthesize returns raw audio; build the WAV ourselves.
        result = synth(text)
        sample_rate = int(getattr(getattr(voice, "config", None), "sample_rate", 22_050))
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)

        if isinstance(result, (bytes, bytearray)):
            wav_file.writeframes(bytes(result))
        else:
            # Assume an iterable of audio chunks (bytes or objects with
            # ``audio_int16_bytes``).
            for chunk in result:
                data = getattr(chunk, "audio_int16_bytes", chunk)
                wav_file.writeframes(bytes(data))


def create_tts(voice: Optional[str] = None, **kwargs) -> PiperTTS:
    """Factory for the default local TTS engine.

    Currently always returns a :class:`PiperTTS`. Provides a stable entry point
    so callers don't depend on the concrete class, leaving room for alternative
    local TTS backends later.
    """
    return PiperTTS(voice=voice, **kwargs)

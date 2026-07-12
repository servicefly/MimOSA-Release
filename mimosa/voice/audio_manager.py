"""Audio I/O management for MimOSA's voice pipeline.

This module owns all microphone capture and speaker playback. It wraps
`PyAudio <https://people.csail.mit.edu/hubert/pyaudio/>`_ (PortAudio bindings)
behind a small, testable interface so the rest of the voice stack
(``wake_word``, ``stt``, ``tts``) never touches the audio backend directly.

Design goals
------------
* **Graceful degradation.** PyAudio (and the underlying PortAudio system
  library) may be missing -- e.g. in CI or a headless VM with no sound card.
  Importing this module must *never* fail. Backend errors surface as
  :class:`AudioUnavailableError` only when you actually try to use a device,
  and helpers like :meth:`AudioManager.is_available` let callers check first.
* **Local only.** Audio is captured and played entirely on-device; raw audio
  never leaves the machine. This upholds MimOSA's privacy principle.
* **Simple format contract.** Audio is handled as 16-bit signed PCM
  (``paInt16``) mono at a configurable sample rate (default 16 kHz, which is
  what both Whisper and openWakeWord expect).

Typical usage::

    mgr = AudioManager(sample_rate=16000)
    if mgr.is_available():
        frames = mgr.record(seconds=3)        # capture 3s of mic audio
        mgr.play(frames)                       # play it back
    mgr.close()
"""

from __future__ import annotations

import logging
import wave
from dataclasses import dataclass
from io import BytesIO
from typing import Callable, List, Optional, Tuple

logger = logging.getLogger(__name__)

# 16-bit PCM => 2 bytes per sample. Used for byte/duration math without
# requiring PyAudio to be importable.
_BYTES_PER_SAMPLE = 2


class AudioError(RuntimeError):
    """Base class for audio subsystem errors."""


class AudioUnavailableError(AudioError):
    """Raised when no working audio backend / device is available.

    This is the error callers should catch to fall back to text-only mode in
    environments without a microphone or speaker (CI, headless VMs, etc.).
    """


@dataclass
class AudioDevice:
    """Lightweight, backend-agnostic description of an audio device.

    Attributes:
        index: PyAudio device index (use with ``input_device_index`` etc.).
        name: Human-readable device name.
        max_input_channels: Number of input (capture) channels supported.
        max_output_channels: Number of output (playback) channels supported.
        default_sample_rate: Device's default sample rate in Hz.
    """

    index: int
    name: str
    max_input_channels: int
    max_output_channels: int
    default_sample_rate: int

    @property
    def is_input(self) -> bool:
        """True if the device can capture audio (has input channels)."""
        return self.max_input_channels > 0

    @property
    def is_output(self) -> bool:
        """True if the device can play audio (has output channels)."""
        return self.max_output_channels > 0


class AudioManager:
    """Manage microphone capture and speaker playback via PyAudio.

    The PyAudio instance is created lazily on first use so that importing and
    constructing an :class:`AudioManager` is always safe, even when the audio
    backend is unavailable.

    Args:
        sample_rate: Capture/playback sample rate in Hz. Defaults to 16000,
            matching Whisper and openWakeWord.
        channels: Number of channels (1 = mono). Mono is recommended for STT.
        chunk_size: Frames per buffer for streaming reads.
        input_device: Preferred input device index, or ``None`` for the
            system default. Resolved from config (``AUDIO_INPUT_DEVICE``).
        output_device: Preferred output device index, or ``None`` for default.
        silence_threshold: RMS amplitude below which a chunk counts as silence
            (used by :meth:`record_until_silence`).
        silence_duration: Seconds of continuous silence that ends a recording.
    """

    def __init__(
        self,
        sample_rate: int = 16000,
        channels: int = 1,
        chunk_size: int = 1024,
        input_device: Optional[int] = None,
        output_device: Optional[int] = None,
        silence_threshold: int = 500,
        silence_duration: float = 1.5,
        device_index: Optional[int] = None,
    ) -> None:
        self.sample_rate = sample_rate
        self.channels = channels
        self.chunk_size = chunk_size
        # ``device_index`` is the preferred, explicit name for the input device
        # selected by the user (e.g. via the setup wizard). It takes precedence
        # over the legacy ``input_device`` argument when both are given.
        self.input_device = device_index if device_index is not None else input_device
        self.output_device = output_device
        self.silence_threshold = silence_threshold
        self.silence_duration = silence_duration

        self._pyaudio = None  # lazily created PyAudio instance
        self._format = None  # pyaudio.paInt16, set when backend loads

    @property
    def device_index(self) -> Optional[int]:
        """The selected input-device index (alias for :attr:`input_device`)."""
        return self.input_device

    @device_index.setter
    def device_index(self, value: Optional[int]) -> None:
        self.input_device = value

    # -- backend management ------------------------------------------------

    def _ensure_backend(self):
        """Import PyAudio and instantiate it on first use.

        Returns:
            The live ``pyaudio.PyAudio`` instance.

        Raises:
            AudioUnavailableError: If PyAudio cannot be imported or initialized
                (missing package or PortAudio system library, no sound card).
        """
        if self._pyaudio is not None:
            return self._pyaudio
        try:
            import pyaudio  # imported lazily; optional dependency
        except Exception as exc:  # ImportError or library load failure
            raise AudioUnavailableError(
                "PyAudio is not available. Install it with "
                "`pip install pyaudio` and ensure the PortAudio system library "
                "is present (`sudo apt install portaudio19-dev`)."
            ) from exc
        try:
            self._pyaudio = pyaudio.PyAudio()
            self._format = pyaudio.paInt16
        except Exception as exc:
            raise AudioUnavailableError(
                f"Could not initialize the audio backend: {exc}"
            ) from exc
        return self._pyaudio

    def is_available(self) -> bool:
        """Return ``True`` if an audio backend can be initialized.

        Never raises -- safe to call as a guard before recording/playing.
        """
        try:
            self._ensure_backend()
            return True
        except AudioUnavailableError:
            return False

    # -- device enumeration ------------------------------------------------

    def list_devices(self) -> List[AudioDevice]:
        """Enumerate all audio devices known to the backend.

        Returns:
            A list of :class:`AudioDevice`. Empty if no backend is available.
        """
        try:
            pa = self._ensure_backend()
        except AudioUnavailableError:
            logger.warning("Audio backend unavailable; no devices to list.")
            return []

        devices: List[AudioDevice] = []
        for i in range(pa.get_device_count()):
            try:
                info = pa.get_device_info_by_index(i)
            except Exception:  # pragma: no cover - backend quirk
                continue
            devices.append(
                AudioDevice(
                    index=i,
                    name=str(info.get("name", f"device {i}")),
                    max_input_channels=int(info.get("maxInputChannels", 0)),
                    max_output_channels=int(info.get("maxOutputChannels", 0)),
                    default_sample_rate=int(
                        info.get("defaultSampleRate", self.sample_rate)
                    ),
                )
            )
        return devices

    @staticmethod
    def _enumerate_devices(default_sample_rate: int = 16000) -> List[AudioDevice]:
        """Enumerate every device using a throw-away PyAudio instance.

        Used by the static helpers so callers (e.g. the setup wizard) can list
        devices without constructing/owning an :class:`AudioManager`. Never
        raises -- returns ``[]`` when the audio backend is unavailable.
        """
        try:
            import pyaudio  # imported lazily; optional dependency
        except Exception:
            logger.warning("PyAudio unavailable; cannot enumerate audio devices.")
            return []
        pa = None
        try:
            pa = pyaudio.PyAudio()
        except Exception as exc:  # pragma: no cover - backend dependent
            logger.warning("Could not initialise audio backend: %s", exc)
            return []
        devices: List[AudioDevice] = []
        try:
            for i in range(pa.get_device_count()):
                try:
                    info = pa.get_device_info_by_index(i)
                except Exception:  # pragma: no cover - backend quirk
                    continue
                devices.append(
                    AudioDevice(
                        index=i,
                        name=str(info.get("name", f"device {i}")),
                        max_input_channels=int(info.get("maxInputChannels", 0)),
                        max_output_channels=int(info.get("maxOutputChannels", 0)),
                        default_sample_rate=int(
                            info.get("defaultSampleRate", default_sample_rate)
                        ),
                    )
                )
        finally:
            try:
                pa.terminate()
            except Exception:  # pragma: no cover - defensive
                pass
        return devices

    @staticmethod
    def check_audio_available() -> Tuple[bool, str]:
        """Report whether a usable audio *input* device exists (item #3).

        Designed to be called **once** at start-up so the voice loop can decide
        whether to run at all, instead of spinning and logging the same
        ``Invalid input device`` error on every iteration in headless / no-audio
        environments (VMs, servers, broken sound stacks).

        Never raises. Returns ``(available, reason)`` where ``reason`` is a short
        human-readable explanation suitable for a single log line / notification:

        * ``(False, "PyAudio is not installed")`` -- optional dependency missing.
        * ``(False, "audio backend could not be initialised: ...")`` -- PortAudio
          present but the backend failed to start (no sound server).
        * ``(False, "no audio input device found")`` -- backend works but there
          are no capture-capable devices.
        * ``(True, "<device name>")`` -- at least one input device is present.
        """
        try:
            import pyaudio  # noqa: F401 - probe only
        except Exception:
            return False, "PyAudio is not installed"

        try:
            devices = AudioManager.list_input_devices()
        except Exception as exc:  # pragma: no cover - defensive
            return False, f"audio backend could not be initialised: {exc}"

        if not devices:
            return False, "no audio input device found"

        default = AudioManager.get_default_input_device()
        name = default.name if default is not None else devices[0].name
        return True, name

    @staticmethod
    def list_input_devices() -> List[AudioDevice]:
        """Return all devices capable of audio capture (system-wide).

        A :func:`staticmethod` so the setup wizard can scan microphones without
        owning an :class:`AudioManager`. Returns ``[]`` when no audio backend is
        available (headless / CI).
        """
        return [d for d in AudioManager._enumerate_devices() if d.is_input]

    @staticmethod
    def list_output_devices() -> List[AudioDevice]:
        """Return all devices capable of audio playback (system-wide)."""
        return [d for d in AudioManager._enumerate_devices() if d.is_output]

    @staticmethod
    def get_default_input_device() -> Optional[AudioDevice]:
        """Return the system default input device, or ``None`` if unavailable.

        Mirrors PortAudio's notion of the default capture device so the wizard
        can pre-select and label it ``(Default)``.
        """
        try:
            import pyaudio  # imported lazily; optional dependency
        except Exception:
            return None
        pa = None
        try:
            pa = pyaudio.PyAudio()
            info = pa.get_default_input_device_info()
            return AudioDevice(
                index=int(info.get("index", 0)),
                name=str(info.get("name", "default")),
                max_input_channels=int(info.get("maxInputChannels", 0)),
                max_output_channels=int(info.get("maxOutputChannels", 0)),
                default_sample_rate=int(info.get("defaultSampleRate", 16000)),
            )
        except Exception:  # no default device, or backend missing
            return None
        finally:
            if pa is not None:
                try:
                    pa.terminate()
                except Exception:  # pragma: no cover - defensive
                    pass

    @staticmethod
    def get_default_output_device() -> Optional[AudioDevice]:
        """Return the system default output device, or ``None`` if unavailable.

        Mirrors PortAudio's notion of the default playback device so the setup
        wizard can pre-select and label it ``(Default)``.
        """
        try:
            import pyaudio  # imported lazily; optional dependency
        except Exception:
            return None
        pa = None
        try:
            pa = pyaudio.PyAudio()
            info = pa.get_default_output_device_info()
            return AudioDevice(
                index=int(info.get("index", 0)),
                name=str(info.get("name", "default")),
                max_input_channels=int(info.get("maxInputChannels", 0)),
                max_output_channels=int(info.get("maxOutputChannels", 0)),
                default_sample_rate=int(info.get("defaultSampleRate", 16000)),
            )
        except Exception:  # no default device, or backend missing
            return None
        finally:
            if pa is not None:
                try:
                    pa.terminate()
                except Exception:  # pragma: no cover - defensive
                    pass

    @staticmethod
    def resolve_output_device_index(value) -> Optional[int]:
        """Resolve a stored config value to a concrete output-device index.

        The playback counterpart to :meth:`resolve_device_index`. Accepts
        ``None``/``""`` (→ system default, returns ``None``), an int or numeric
        string (→ that index, if it exists), or a device *name* (→ the index of
        the first output device whose name matches). Returns ``None`` when the
        value can't be resolved, so callers fall back to the default.
        """
        if value is None:
            return None
        text = str(value).strip()
        if not text:
            return None
        outputs = AudioManager.list_output_devices()
        valid = {d.index for d in outputs}
        # Numeric index (e.g. "3" or 3).
        try:
            idx = int(text)
            return idx if idx in valid else (idx if not outputs else None)
        except (TypeError, ValueError):
            pass
        # Name match (case-insensitive, exact then substring).
        lowered = text.lower()
        for d in outputs:
            if d.name.lower() == lowered:
                return d.index
        for d in outputs:
            if lowered in d.name.lower():
                return d.index
        return None

    @staticmethod
    def resolve_device_index(value) -> Optional[int]:
        """Resolve a stored config value to a concrete input-device index.

        Accepts ``None``/``""`` (→ system default, returns ``None``), an int or
        numeric string (→ that index, if it exists), or a device *name* (→ the
        index of the first input device whose name matches). Returns ``None``
        when the value can't be resolved, so callers fall back to the default.
        """
        if value is None:
            return None
        text = str(value).strip()
        if not text:
            return None
        inputs = AudioManager.list_input_devices()
        valid = {d.index for d in inputs}
        # Numeric index (e.g. "3" or 3).
        try:
            idx = int(text)
            return idx if idx in valid else (idx if not inputs else None)
        except (TypeError, ValueError):
            pass
        # Name match (case-insensitive, exact then substring).
        lowered = text.lower()
        for d in inputs:
            if d.name.lower() == lowered:
                return d.index
        for d in inputs:
            if lowered in d.name.lower():
                return d.index
        return None

    # -- recording ---------------------------------------------------------

    def record(self, seconds: float) -> bytes:
        """Capture a fixed duration of microphone audio.

        Args:
            seconds: How long to record.

        Returns:
            Raw 16-bit PCM audio bytes.

        Raises:
            AudioUnavailableError: If no input device is available.
        """
        pa = self._ensure_backend()
        stream = pa.open(
            format=self._format,
            channels=self.channels,
            rate=self.sample_rate,
            input=True,
            frames_per_buffer=self.chunk_size,
            input_device_index=self.input_device,
        )
        frames: List[bytes] = []
        total_chunks = int(self.sample_rate / self.chunk_size * seconds)
        try:
            for _ in range(max(1, total_chunks)):
                frames.append(stream.read(self.chunk_size, exception_on_overflow=False))
        finally:
            stream.stop_stream()
            stream.close()
        return b"".join(frames)

    def record_until_silence(
        self,
        max_seconds: float = 15.0,
        start_timeout: float = 5.0,
    ) -> bytes:
        """Record until the user stops speaking (trailing-silence detection).

        Reads audio in chunks, computing each chunk's RMS amplitude. Once
        speech has started, a run of silent chunks longer than
        :attr:`silence_duration` ends the capture. This is what the voice loop
        uses to grab a single utterance after the wake word.

        Args:
            max_seconds: Hard cap on total recording length.
            start_timeout: If no speech is detected within this many seconds,
                stop and return whatever was captured (possibly empty).

        Returns:
            Raw 16-bit PCM audio bytes for the utterance.

        Raises:
            AudioUnavailableError: If no input device is available.
        """
        pa = self._ensure_backend()
        stream = pa.open(
            format=self._format,
            channels=self.channels,
            rate=self.sample_rate,
            input=True,
            frames_per_buffer=self.chunk_size,
            input_device_index=self.input_device,
        )

        frames: List[bytes] = []
        chunk_seconds = self.chunk_size / self.sample_rate
        silent_for = 0.0
        elapsed = 0.0
        speech_started = False

        try:
            while elapsed < max_seconds:
                chunk = stream.read(self.chunk_size, exception_on_overflow=False)
                frames.append(chunk)
                elapsed += chunk_seconds

                if self.rms(chunk) >= self.silence_threshold:
                    speech_started = True
                    silent_for = 0.0
                else:
                    silent_for += chunk_seconds

                if speech_started and silent_for >= self.silence_duration:
                    break  # natural end of utterance
                if not speech_started and elapsed >= start_timeout:
                    break  # user never spoke
        finally:
            stream.stop_stream()
            stream.close()
        return b"".join(frames)

    # -- microphone test ---------------------------------------------------

    def measure_levels(
        self,
        seconds: float = 2.0,
        on_level: Optional["Callable[[float], None]"] = None,
    ) -> float:
        """Record for ``seconds`` and report normalised volume levels.

        Used by the setup wizard's "Test Microphone" button to drive a live
        volume meter. Reads audio in chunks from the *selected* input device
        (``self.input_device``), computing each chunk's RMS amplitude
        normalised to ``[0, 1]`` (relative to full-scale 16-bit). For every
        chunk it invokes ``on_level(level)`` (if given) so the UI can animate a
        meter, and finally returns the **peak** level observed.

        Raises:
            AudioUnavailableError: If no input device is available.
        """
        pa = self._ensure_backend()
        stream = pa.open(
            format=self._format,
            channels=self.channels,
            rate=self.sample_rate,
            input=True,
            frames_per_buffer=self.chunk_size,
            input_device_index=self.input_device,
        )
        peak = 0.0
        chunk_seconds = self.chunk_size / self.sample_rate
        elapsed = 0.0
        try:
            while elapsed < seconds:
                chunk = stream.read(self.chunk_size, exception_on_overflow=False)
                elapsed += chunk_seconds
                level = min(1.0, self.rms(chunk) / 32767.0)
                peak = max(peak, level)
                if on_level is not None:
                    try:
                        on_level(level)
                    except Exception:  # pragma: no cover - UI callback best-effort
                        logger.debug("level callback failed", exc_info=True)
        finally:
            stream.stop_stream()
            stream.close()
        return peak

    # -- playback ----------------------------------------------------------

    def play(self, pcm: bytes, sample_rate: Optional[int] = None) -> None:
        """Play raw 16-bit PCM audio through the output device.

        Args:
            pcm: Raw 16-bit PCM audio bytes.
            sample_rate: Sample rate of ``pcm``; defaults to the manager's
                configured rate. (TTS output may use a different rate.)

        Raises:
            AudioUnavailableError: If no output device is available.
        """
        pa = self._ensure_backend()
        rate = sample_rate or self.sample_rate
        stream = pa.open(
            format=self._format,
            channels=self.channels,
            rate=rate,
            output=True,
            output_device_index=self.output_device,
        )
        try:
            stream.write(pcm)
        finally:
            stream.stop_stream()
            stream.close()

    def play_wav_bytes(self, wav_bytes: bytes) -> None:
        """Play a WAV file provided as bytes (parses header for rate/channels).

        Convenient for Piper TTS output, which is produced as WAV.

        Raises:
            AudioUnavailableError: If no output device is available.
        """
        with wave.open(BytesIO(wav_bytes), "rb") as wf:
            rate = wf.getframerate()
            pcm = wf.readframes(wf.getnframes())
        self.play(pcm, sample_rate=rate)

    # -- helpers -----------------------------------------------------------

    @staticmethod
    def rms(pcm: bytes) -> float:
        """Compute the root-mean-square amplitude of a 16-bit PCM chunk.

        Used for voice-activity / silence detection. Returns 0.0 for empty
        input. Implemented with the stdlib ``audioop`` when available, falling
        back to a pure-Python computation otherwise.

        Args:
            pcm: Raw 16-bit PCM audio bytes.

        Returns:
            RMS amplitude as a float (0..32767 range for full-scale 16-bit).
        """
        if not pcm:
            return 0.0
        try:
            import audioop  # stdlib; removed in Python 3.13+

            return float(audioop.rms(pcm, _BYTES_PER_SAMPLE))
        except Exception:
            # Pure-Python fallback (no numpy dependency required).
            import struct

            count = len(pcm) // _BYTES_PER_SAMPLE
            if count == 0:
                return 0.0
            samples = struct.unpack(f"<{count}h", pcm[: count * _BYTES_PER_SAMPLE])
            mean_sq = sum(s * s for s in samples) / count
            return mean_sq ** 0.5

    def save_wav(self, pcm: bytes, path: str, sample_rate: Optional[int] = None) -> None:
        """Write raw PCM to a ``.wav`` file (handy for debugging/tests).

        Args:
            pcm: Raw 16-bit PCM audio bytes.
            path: Destination ``.wav`` path.
            sample_rate: Sample rate to record in the header; defaults to the
                manager's configured rate.
        """
        rate = sample_rate or self.sample_rate
        with wave.open(path, "wb") as wf:
            wf.setnchannels(self.channels)
            wf.setsampwidth(_BYTES_PER_SAMPLE)
            wf.setframerate(rate)
            wf.writeframes(pcm)

    def close(self) -> None:
        """Release the audio backend. Safe to call multiple times."""
        if self._pyaudio is not None:
            try:
                self._pyaudio.terminate()
            except Exception:  # pragma: no cover - defensive
                pass
            self._pyaudio = None

    def __enter__(self) -> "AudioManager":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

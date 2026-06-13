"""Synthetic wake-word sample generation (Milestone 2, requirement #4).

To train a custom wake word we need *lots* of recordings of someone saying it.
Asking the user to record thousands of clips is impractical, so MimOSA
synthesizes them locally with the bundled **Piper TTS** -- the same engine that
gives MimOSA its voice. We render the chosen name across:

* **multiple voices** (chosen by the user's voice-style preference; see
  :func:`mimosa.voice.tts.voices_for_gender`),
* **several speaking speeds** (0.8x / 1.0x / 1.2x and in-between), and
* **pitch variations** (a light resample-based shift up/down),

to capture natural variation in how the word might be spoken. The clips are
written as 16 kHz mono WAV files (the format openWakeWord trains on) to
``~/.local/share/mimosa/training/<name>/samples/``.

Everything runs **on-device**; no audio leaves the machine. The heavy synthesis
work is injectable (``synthesize_fn``) so the test-suite can generate tiny stub
clips instantly without loading Piper or downloading any voice models. The
generator **never raises** -- on any failure it returns a result describing what
was (and wasn't) produced so the trainer can decide whether to proceed or fall
back to the default "Mimosa" wake word.
"""

from __future__ import annotations

import logging
import os
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

#: openWakeWord trains on 16 kHz mono audio.
SAMPLE_RATE = 16000

#: Default speaking-speed multipliers (req #4: 0.8 / 1.0 / 1.2x plus midpoints).
DEFAULT_SPEEDS: Tuple[float, ...] = (0.8, 0.9, 1.0, 1.1, 1.2)

#: Default pitch shifts in semitones applied via lightweight resampling.
DEFAULT_PITCH_SEMITONES: Tuple[int, ...] = (-2, 0, 2)

#: Default number of positive samples to target (req #4: 1,000-3,000).
DEFAULT_TARGET_SAMPLES = 1500

#: Spoken carrier variations so the model hears the name in natural framings.
#: Each ``{name}`` is replaced with the wake word.
PHRASE_TEMPLATES: Tuple[str, ...] = (
    "{name}",
    "{name}.",
    "{name}?",
    "hey {name}",
    "ok {name}",
    "hey {name}, ",
)

# A callable that turns (text, voice, speed) into WAV bytes.
SynthesizeFn = Callable[[str, str, float], bytes]
# Progress sink: (done, total, message).
ProgressFn = Callable[[int, int, str], None]


@dataclass
class GenerationResult:
    """Outcome of a synthetic-generation run.

    Attributes:
        output_dir: Directory the WAV clips were written to.
        sample_paths: Paths of the clips that were successfully written.
        voices_used: Distinct Piper voices that produced clips.
        requested: How many clips were targeted.
        ok: ``True`` if at least one clip was produced.
        error: Optional human-readable reason when nothing (or too little) was
            produced (e.g. Piper unavailable).
    """

    output_dir: str
    sample_paths: List[str] = field(default_factory=list)
    voices_used: List[str] = field(default_factory=list)
    requested: int = 0
    ok: bool = False
    error: str = ""

    @property
    def count(self) -> int:
        return len(self.sample_paths)


def default_samples_dir(name: str) -> Path:
    """Return ``~/.local/share/mimosa/training/<slug>/samples`` for ``name``."""
    base = Path(
        os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")
    )
    return base / "mimosa" / "training" / slugify(name) / "samples"


def slugify(name: str) -> str:
    """Filesystem-safe slug for a wake-word name (lowercase, ascii, hyphens)."""
    cleaned = "".join(
        c.lower() if c.isalnum() else "-" for c in (name or "").strip()
    )
    # Collapse repeated hyphens and strip leading/trailing ones.
    parts = [p for p in cleaned.split("-") if p]
    return "-".join(parts) or "wakeword"


class SyntheticGenerator:
    """Generate synthetic positive samples for a wake word with Piper TTS.

    Args:
        synthesize_fn: Optional injectable ``(text, voice, speed) -> wav_bytes``.
            Defaults to a Piper-backed synthesizer created lazily. Injecting a
            stub keeps tests fast and offline.
        sample_rate: Output sample rate (defaults to 16 kHz for openWakeWord).
    """

    def __init__(
        self,
        synthesize_fn: Optional[SynthesizeFn] = None,
        *,
        sample_rate: int = SAMPLE_RATE,
    ) -> None:
        self._synthesize_fn = synthesize_fn
        self.sample_rate = int(sample_rate)
        self._tts_cache: dict = {}

    # -- public API --------------------------------------------------------

    def generate(
        self,
        name: str,
        *,
        gender: str = "neutral",
        output_dir: Optional[str] = None,
        target_samples: int = DEFAULT_TARGET_SAMPLES,
        speeds: Sequence[float] = DEFAULT_SPEEDS,
        pitch_semitones: Sequence[int] = DEFAULT_PITCH_SEMITONES,
        voices: Optional[Sequence[str]] = None,
        on_progress: Optional[ProgressFn] = None,
        should_cancel: Optional[Callable[[], bool]] = None,
    ) -> GenerationResult:
        """Synthesize positive wake-word clips and write them to disk.

        Args:
            name: The wake word to synthesize (e.g. ``"Jarvis"``).
            gender: Voice-style preference selecting which Piper voices to use.
            output_dir: Where to write WAVs (defaults to
                :func:`default_samples_dir`).
            target_samples: Desired number of clips (clamped to the milestone's
                1,000-3,000 guidance when above zero).
            speeds: Speaking-rate multipliers to cover.
            pitch_semitones: Pitch shifts (semitones) to cover.
            voices: Explicit voice ids; defaults to those for ``gender``.
            on_progress: Optional ``(done, total, message)`` progress sink.
            should_cancel: Optional predicate; when it returns ``True`` the run
                stops early and returns what was produced so far.

        Returns:
            A :class:`GenerationResult`. Never raises.
        """
        name = (name or "").strip()
        out = Path(output_dir) if output_dir else default_samples_dir(name)
        result = GenerationResult(output_dir=str(out), requested=int(target_samples))

        if not name:
            result.error = "No wake-word name was provided."
            return result

        voice_list = list(voices) if voices else list(self._voices_for(gender))
        if not voice_list:
            result.error = "No TTS voices are available for synthesis."
            return result

        try:
            out.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            result.error = f"Could not create samples directory: {exc}"
            return result

        plan = self._build_plan(
            name, voice_list, list(speeds), list(pitch_semitones), target_samples
        )
        total = len(plan)
        if total == 0:
            result.error = "Nothing to generate (empty plan)."
            return result

        used_voices: set = set()
        for i, (text, voice, speed, pitch) in enumerate(plan):
            if should_cancel is not None and should_cancel():
                logger.info("Synthetic generation cancelled at %d/%d", i, total)
                break
            try:
                wav_bytes = self._synthesize(text, voice, speed)
                if pitch:
                    wav_bytes = self._shift_pitch(wav_bytes, pitch)
                path = out / f"sample_{i:05d}.wav"
                self._write_wav(path, wav_bytes)
                result.sample_paths.append(str(path))
                used_voices.add(voice)
            except Exception as exc:  # one bad clip must not abort the batch
                logger.debug("Sample %d failed (%s): %s", i, voice, exc)
            if on_progress is not None:
                self._safe_progress(
                    on_progress, i + 1, total,
                    f"Generating voice samples… ({i + 1}/{total})",
                )

        result.voices_used = sorted(used_voices)
        result.ok = bool(result.sample_paths)
        if not result.ok and not result.error:
            result.error = (
                "Text-to-speech produced no samples. Piper TTS may be "
                "unavailable or its voices could not be downloaded."
            )
        return result

    # -- planning ----------------------------------------------------------

    def _build_plan(
        self,
        name: str,
        voices: List[str],
        speeds: List[float],
        pitches: List[int],
        target: int,
    ) -> List[Tuple[str, str, float, int]]:
        """Build a deterministic list of (text, voice, speed, pitch) jobs.

        Cycles through every voice x speed x pitch x phrase combination, then
        repeats the combination cycle until ``target`` is reached (or once
        through if ``target`` is non-positive). The combination order is
        interleaved so an early-cancelled run still spans many voices/speeds.
        """
        speeds = speeds or [1.0]
        pitches = pitches or [0]
        phrases = [t.format(name=name) for t in PHRASE_TEMPLATES]

        combos: List[Tuple[str, str, float, int]] = []
        for phrase in phrases:
            for voice in voices:
                for speed in speeds:
                    for pitch in pitches:
                        combos.append((phrase, voice, float(speed), int(pitch)))

        if not combos:
            return []

        if target and target > 0:
            target = self._clamp_target(target)
            plan: List[Tuple[str, str, float, int]] = []
            idx = 0
            while len(plan) < target:
                plan.append(combos[idx % len(combos)])
                idx += 1
            return plan
        return combos

    @staticmethod
    def _clamp_target(target: int) -> int:
        """Clamp the sample target to the milestone's 1,000-3,000 guidance."""
        return max(1000, min(3000, int(target)))

    def _voices_for(self, gender: str) -> Tuple[str, ...]:
        try:
            from mimosa.voice.tts import voices_for_gender

            return voices_for_gender(gender)
        except Exception:  # pragma: no cover - defensive
            return ("en_US-lessac-medium",)

    # -- synthesis backends ------------------------------------------------

    def _synthesize(self, text: str, voice: str, speed: float) -> bytes:
        if self._synthesize_fn is not None:
            return self._synthesize_fn(text, voice, speed)
        return self._piper_synthesize(text, voice, speed)

    def _piper_synthesize(self, text: str, voice: str, speed: float) -> bytes:
        """Default Piper-backed synthesis, caching one engine per voice."""
        engine = self._tts_cache.get(voice)
        if engine is None:
            from mimosa.voice.tts import create_tts

            engine = create_tts(voice=voice, speed=speed)
            self._tts_cache[voice] = engine
        else:
            # Adjust speed without rebuilding the (expensive) voice model.
            try:
                engine.speed = float(speed) if speed and speed > 0 else 1.0
            except Exception:  # pragma: no cover - defensive
                pass
        return engine.synthesize(text)

    # -- audio helpers -----------------------------------------------------

    def _write_wav(self, path: Path, wav_bytes: bytes) -> None:
        """Normalise ``wav_bytes`` to 16 kHz mono 16-bit and write to ``path``.

        Accepts arbitrary-rate mono/stereo input and resamples/mixes down so the
        on-disk clips always match openWakeWord's expected format.
        """
        pcm, rate, channels, width = self._read_wav(wav_bytes)
        pcm = self._to_mono(pcm, channels, width)
        if rate != self.sample_rate:
            pcm = self._resample(pcm, rate, self.sample_rate, width)
        with wave.open(str(path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(self.sample_rate)
            wf.writeframes(pcm)

    @staticmethod
    def _read_wav(wav_bytes: bytes) -> Tuple[bytes, int, int, int]:
        import io

        with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
            channels = wf.getnchannels()
            width = wf.getsampwidth()
            rate = wf.getframerate()
            frames = wf.readframes(wf.getnframes())
        return frames, rate, channels, width

    @staticmethod
    def _to_mono(pcm: bytes, channels: int, width: int) -> bytes:
        if channels <= 1:
            return pcm
        try:
            import audioop

            return audioop.tomono(pcm, width, 0.5, 0.5)
        except Exception:  # pragma: no cover - fallback: take left channel
            step = channels * width
            return b"".join(pcm[i:i + width] for i in range(0, len(pcm), step))

    @staticmethod
    def _resample(pcm: bytes, src_rate: int, dst_rate: int, width: int) -> bytes:
        if src_rate == dst_rate:
            return pcm
        try:
            import audioop

            converted, _ = audioop.ratecv(pcm, width, 1, src_rate, dst_rate, None)
            return converted
        except Exception:  # pragma: no cover - last-resort nearest-neighbour
            import array

            ratio = dst_rate / float(src_rate)
            samples = array.array("h")
            samples.frombytes(pcm)
            out = array.array("h")
            n_out = int(len(samples) * ratio)
            for i in range(n_out):
                out.append(samples[int(i / ratio)])
            return out.tobytes()

    def _shift_pitch(self, wav_bytes: bytes, semitones: int) -> bytes:
        """Apply a light pitch shift by resample-then-restore (changes timbre).

        This is a cheap, dependency-free approximation: we resample the audio by
        the pitch ratio (which shifts pitch *and* duration) then resample back to
        the original rate, leaving a pitch-shifted clip of the original length's
        order. Good enough to add timbral variety for training. Never raises --
        returns the input unchanged on any failure.
        """
        if not semitones:
            return wav_bytes
        try:
            pcm, rate, channels, width = self._read_wav(wav_bytes)
            pcm = self._to_mono(pcm, channels, width)
            ratio = 2.0 ** (semitones / 12.0)
            shifted_rate = max(4000, int(self.sample_rate * ratio))
            # Resample as if recorded at a different rate, then back to target.
            stage1 = self._resample(pcm, self.sample_rate, shifted_rate, width)
            stage2 = self._resample(stage1, shifted_rate, self.sample_rate, width)
            import io

            buf = io.BytesIO()
            with wave.open(buf, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(self.sample_rate)
                wf.writeframes(stage2)
            return buf.getvalue()
        except Exception:  # pragma: no cover - defensive
            return wav_bytes

    @staticmethod
    def _safe_progress(fn: ProgressFn, done: int, total: int, msg: str) -> None:
        try:
            fn(done, total, msg)
        except Exception:  # pragma: no cover - progress sink must not break us
            logger.debug("progress sink failed", exc_info=True)

"""Audio data augmentation for wake-word training (Milestone 2, req #5).

A model trained only on clean text-to-speech clips works great in a silent room
and falls apart in a real kitchen. To make the custom wake word robust we expand
the synthetic positives (:mod:`mimosa.training.synthetic_generator`) into a much
larger, more realistic dataset by simulating the conditions MimOSA actually runs
in:

* **Background noise** -- kitchen clatter, street traffic, music and TV chatter
  mixed in at varying signal-to-noise ratios.
* **Reverb / room acoustics** -- short convolution with synthetic room impulse
  responses so the word sounds like it was said in a real room.
* **Far-field simulation** -- attenuation + reverb + gentle low-pass filtering to
  mimic speaking from across the room rather than into the mic.
* **Negative samples** -- similar-sounding words, common phrases and noise-only
  clips so the model learns what is *not* the wake word (fewer false triggers).

The goal is **5,000+ samples** total. Everything is procedural and
dependency-free (noise and impulses are synthesized with the stdlib), so it runs
fully on-device and unit-tests instantly. No real noise corpora need to ship.
Like every training stage it **never raises** -- a failed clip is skipped and the
run continues.
"""

from __future__ import annotations

import array
import io
import logging
import math
import os
import random
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16000

#: Background-noise "scenes" we can synthesize and mix in (req #5).
NOISE_TYPES: Tuple[str, ...] = ("kitchen", "street", "music", "tv")

#: Signal-to-noise ratios (dB) to mix positives at -- lower = noisier.
DEFAULT_SNRS_DB: Tuple[float, ...] = (20.0, 10.0, 5.0)

#: Target total dataset size (req #5: 5,000+).
DEFAULT_TARGET_TOTAL = 5000

#: Common phrases used as spoken negatives so the model learns conversational
#: speech is *not* the wake word.
NEGATIVE_PHRASES: Tuple[str, ...] = (
    "what time is it",
    "turn on the lights",
    "play some music",
    "how is the weather",
    "set a timer for ten minutes",
    "thank you so much",
    "see you later",
    "let me think about it",
    "open the front door",
    "good morning everyone",
)

ProgressFn = Callable[[int, int, str], None]
SynthesizeFn = Callable[[str, str, float], bytes]


@dataclass
class AugmentationResult:
    """Outcome of an augmentation run.

    Attributes:
        positives_dir: Directory holding augmented positive clips.
        negatives_dir: Directory holding negative clips.
        positive_paths: Augmented positive clip paths.
        negative_paths: Negative clip paths.
        ok: ``True`` if the dataset reached a usable size.
        error: Optional reason when the run produced too little.
    """

    positives_dir: str
    negatives_dir: str
    positive_paths: List[str] = field(default_factory=list)
    negative_paths: List[str] = field(default_factory=list)
    ok: bool = False
    error: str = ""

    @property
    def total(self) -> int:
        return len(self.positive_paths) + len(self.negative_paths)


class DataAugmenter:
    """Expand clean positives into a robust, realistic training dataset.

    Args:
        sample_rate: Working sample rate (16 kHz for openWakeWord).
        seed: Optional RNG seed for reproducible augmentation (tests).
        synthesize_fn: Optional ``(text, voice, speed) -> wav_bytes`` used to
            render *spoken* negatives. Defaults to Piper; inject a stub in tests.
    """

    def __init__(
        self,
        *,
        sample_rate: int = SAMPLE_RATE,
        seed: Optional[int] = None,
        synthesize_fn: Optional[SynthesizeFn] = None,
    ) -> None:
        self.sample_rate = int(sample_rate)
        self._rng = random.Random(seed)
        self._synthesize_fn = synthesize_fn

    # -- public API --------------------------------------------------------

    def augment(
        self,
        positive_paths: Sequence[str],
        *,
        output_dir: str,
        target_total: int = DEFAULT_TARGET_TOTAL,
        noise_types: Sequence[str] = NOISE_TYPES,
        snrs_db: Sequence[float] = DEFAULT_SNRS_DB,
        negative_ratio: float = 0.5,
        enforce_minimum: bool = True,
        on_progress: Optional[ProgressFn] = None,
        should_cancel: Optional[Callable[[], bool]] = None,
    ) -> AugmentationResult:
        """Produce an augmented positive set plus negatives.

        Args:
            positive_paths: Clean positive WAV paths from the generator.
            output_dir: Base dir; ``positives/`` and ``negatives/`` are created
                under it.
            target_total: Desired total dataset size (clamped to >= 5,000 when
                positive). Split between positives and negatives by
                ``negative_ratio``.
            noise_types: Which background scenes to mix.
            snrs_db: Signal-to-noise ratios (dB) to apply.
            negative_ratio: Fraction of the dataset that should be negatives.
            on_progress: Optional ``(done, total, message)`` sink.
            should_cancel: Optional cancel predicate.

        Returns:
            An :class:`AugmentationResult`. Never raises.
        """
        base = Path(output_dir)
        pos_dir = base / "positives"
        neg_dir = base / "negatives"
        result = AugmentationResult(
            positives_dir=str(pos_dir), negatives_dir=str(neg_dir)
        )

        clean = [p for p in positive_paths if p and os.path.isfile(p)]
        if not clean:
            result.error = "No positive samples were available to augment."
            return result

        try:
            pos_dir.mkdir(parents=True, exist_ok=True)
            neg_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            result.error = f"Could not create augmentation directories: {exc}"
            return result

        target_total = self._clamp_target(target_total, enforce_minimum)
        n_negatives = int(target_total * _clamp(negative_ratio, 0.0, 0.9))
        n_positives = max(len(clean), target_total - n_negatives)

        noise_types = list(noise_types) or list(NOISE_TYPES)
        snrs_db = list(snrs_db) or list(DEFAULT_SNRS_DB)

        done = 0
        total = n_positives + n_negatives

        # -- augmented positives ------------------------------------------
        idx = 0
        while len(result.positive_paths) < n_positives:
            if should_cancel is not None and should_cancel():
                break
            src = clean[idx % len(clean)]
            variant = idx // len(clean)
            try:
                pcm = self._load_pcm(src)
                aug = self._augment_one(pcm, noise_types, snrs_db, variant)
                path = pos_dir / f"pos_{idx:05d}.wav"
                self._write_pcm(path, aug)
                result.positive_paths.append(str(path))
            except Exception as exc:
                logger.debug("Augment positive %d failed: %s", idx, exc)
            idx += 1
            done += 1
            if on_progress is not None and done % 25 == 0:
                self._safe_progress(on_progress, done, total,
                                    f"Adding real-world noise… ({done}/{total})")

        # -- negatives -----------------------------------------------------
        self._build_negatives(
            neg_dir, n_negatives, noise_types, result, on_progress,
            should_cancel, done, total,
        )

        result.ok = result.total >= max(2, int(target_total * 0.3))
        if not result.ok and not result.error:
            result.error = "Augmentation produced too few samples."
        if on_progress is not None:
            self._safe_progress(on_progress, total, total, "Dataset ready.")
        return result

    # -- positive augmentation --------------------------------------------

    def _augment_one(
        self,
        pcm: array.array,
        noise_types: List[str],
        snrs_db: List[float],
        variant: int,
    ) -> array.array:
        """Apply a deterministic-but-varied chain of effects to one clip."""
        out = pcm
        # Cycle effect combinations across variants for coverage.
        noise = noise_types[variant % len(noise_types)]
        snr = snrs_db[variant % len(snrs_db)]

        # Reverb on roughly half the variants.
        if variant % 2 == 0:
            out = self._apply_reverb(out, room=0.3 + 0.2 * (variant % 3))
        # Far-field on every third variant.
        if variant % 3 == 0:
            out = self._apply_far_field(out)
        # Always mix some background noise for realism.
        out = self._mix_noise(out, noise, snr)
        return out

    def _mix_noise(self, pcm: array.array, noise_type: str, snr_db: float) -> array.array:
        """Mix synthesized background ``noise_type`` into ``pcm`` at ``snr_db``."""
        if not len(pcm):
            return pcm
        noise = self._make_noise(noise_type, len(pcm))
        sig_rms = _rms(pcm) or 1.0
        noise_rms = _rms(noise) or 1.0
        # Scale noise so signal/noise ratio == snr_db.
        target_noise_rms = sig_rms / (10 ** (snr_db / 20.0))
        gain = target_noise_rms / noise_rms
        out = array.array("h", bytes(len(pcm) * 2))
        for i in range(len(pcm)):
            out[i] = _clip16(pcm[i] + int(noise[i] * gain))
        return out

    def _make_noise(self, noise_type: str, n: int) -> array.array:
        """Synthesize ``n`` samples of a named background scene."""
        rng = self._rng
        buf = array.array("h", bytes(n * 2))
        if noise_type == "music":
            # A couple of detuned sine tones with slow amplitude wobble.
            f1, f2 = 220.0, 277.18
            for i in range(n):
                env = 0.5 + 0.5 * math.sin(2 * math.pi * 1.5 * i / self.sample_rate)
                v = math.sin(2 * math.pi * f1 * i / self.sample_rate)
                v += 0.7 * math.sin(2 * math.pi * f2 * i / self.sample_rate)
                buf[i] = _clip16(int(env * v * 6000))
        elif noise_type == "tv":
            # Band-limited babble: filtered white noise with speech-like bursts.
            prev = 0
            for i in range(n):
                white = rng.uniform(-1, 1)
                prev = 0.85 * prev + 0.15 * white  # low-pass -> rumble/voices
                burst = 1.0 if (i // 1600) % 3 != 2 else 0.3  # pauses
                buf[i] = _clip16(int(prev * burst * 7000))
        elif noise_type == "street":
            # Low-frequency traffic rumble + occasional broadband transient.
            prev = 0
            for i in range(n):
                white = rng.uniform(-1, 1)
                prev = 0.95 * prev + 0.05 * white  # heavy low-pass = rumble
                transient = 3.0 if rng.random() < 0.0008 else 1.0
                buf[i] = _clip16(int(prev * transient * 6500))
        else:  # "kitchen" / default: rumble plus sparse clatter clicks.
            prev = 0
            for i in range(n):
                white = rng.uniform(-1, 1)
                prev = 0.8 * prev + 0.2 * white
                click = rng.uniform(-1, 1) * 18000 if rng.random() < 0.0015 else 0
                buf[i] = _clip16(int(prev * 4000 + click))
        return buf

    def _apply_reverb(self, pcm: array.array, room: float = 0.3) -> array.array:
        """Convolve with a short synthetic impulse response (cheap reverb)."""
        if not len(pcm):
            return pcm
        room = _clamp(room, 0.1, 0.9)
        # Build a small impulse response: direct + a few decaying echoes.
        n_taps = int(self.sample_rate * 0.05 * room) + 1
        decay = 0.6 * room
        ir = [0.0] * n_taps
        ir[0] = 1.0
        spacing = max(1, n_taps // 6)
        amp = decay
        for t in range(spacing, n_taps, spacing):
            ir[t] = amp
            amp *= decay
        # Sparse convolution (only non-zero taps) to stay fast.
        taps = [(t, g) for t, g in enumerate(ir) if g]
        out = array.array("h", bytes(len(pcm) * 2))
        norm = sum(g for _, g in taps) or 1.0
        for i in range(len(pcm)):
            acc = 0.0
            for t, g in taps:
                j = i - t
                if j >= 0:
                    acc += pcm[j] * g
            out[i] = _clip16(int(acc / norm))
        return out

    def _apply_far_field(self, pcm: array.array) -> array.array:
        """Attenuate + low-pass to mimic speaking from across the room."""
        if not len(pcm):
            return pcm
        out = array.array("h", bytes(len(pcm) * 2))
        prev = 0.0
        atten = 0.55
        for i in range(len(pcm)):
            # One-pole low-pass then attenuation.
            prev = 0.6 * prev + 0.4 * pcm[i]
            out[i] = _clip16(int(prev * atten))
        return out

    # -- negatives ---------------------------------------------------------

    def _build_negatives(
        self,
        neg_dir: Path,
        n_negatives: int,
        noise_types: List[str],
        result: AugmentationResult,
        on_progress: Optional[ProgressFn],
        should_cancel: Optional[Callable[[], bool]],
        done: int,
        total: int,
    ) -> None:
        """Create negative samples: spoken phrases (if TTS) + noise-only clips."""
        # First, try spoken negatives via TTS (best signal). If unavailable we
        # fall back to noise-only negatives, which still teach "not the word".
        spoken: List[array.array] = []
        if self._synthesize_fn is not None:
            for phrase in NEGATIVE_PHRASES:
                try:
                    wav = self._synthesize_fn(phrase, "en_US-lessac-medium", 1.0)
                    spoken.append(self._pcm_from_wav_bytes(wav))
                except Exception as exc:  # pragma: no cover - stub-dependent
                    logger.debug("Negative phrase '%s' failed: %s", phrase, exc)

        i = 0
        while len(result.negative_paths) < n_negatives:
            if should_cancel is not None and should_cancel():
                break
            try:
                if spoken and i % 2 == 0:
                    base = spoken[(i // 2) % len(spoken)]
                    noise = noise_types[i % len(noise_types)]
                    pcm = self._mix_noise(base, noise, snr_db=12.0)
                else:
                    # Noise-only negative (~1s) at a random scene.
                    noise = noise_types[i % len(noise_types)]
                    pcm = self._make_noise(noise, self.sample_rate)
                path = neg_dir / f"neg_{i:05d}.wav"
                self._write_pcm(path, pcm)
                result.negative_paths.append(str(path))
            except Exception as exc:
                logger.debug("Negative %d failed: %s", i, exc)
            i += 1
            done += 1
            if on_progress is not None and done % 25 == 0:
                self._safe_progress(on_progress, done, total,
                                    f"Creating negative samples… ({done}/{total})")

    # -- audio I/O ---------------------------------------------------------

    def _load_pcm(self, path: str) -> array.array:
        with wave.open(path, "rb") as wf:
            channels = wf.getnchannels()
            width = wf.getsampwidth()
            rate = wf.getframerate()
            frames = wf.readframes(wf.getnframes())
        return self._normalize(frames, rate, channels, width)

    def _pcm_from_wav_bytes(self, wav_bytes: bytes) -> array.array:
        with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
            channels = wf.getnchannels()
            width = wf.getsampwidth()
            rate = wf.getframerate()
            frames = wf.readframes(wf.getnframes())
        return self._normalize(frames, rate, channels, width)

    def _normalize(self, frames: bytes, rate: int, channels: int, width: int) -> array.array:
        # Down-mix to mono.
        if channels > 1:
            try:
                import audioop

                frames = audioop.tomono(frames, width, 0.5, 0.5)
            except Exception:  # pragma: no cover
                pass
        # Resample to working rate.
        if rate != self.sample_rate:
            try:
                import audioop

                frames, _ = audioop.ratecv(
                    frames, width, 1, rate, self.sample_rate, None
                )
            except Exception:  # pragma: no cover
                pass
        samples = array.array("h")
        # Guard against odd-length buffers.
        usable = len(frames) - (len(frames) % 2)
        samples.frombytes(frames[:usable])
        return samples

    def _write_pcm(self, path: Path, pcm: array.array) -> None:
        with wave.open(str(path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(self.sample_rate)
            wf.writeframes(pcm.tobytes())

    # -- misc --------------------------------------------------------------

    @staticmethod
    def _clamp_target(target: int, enforce_minimum: bool = True) -> int:
        if not target or target <= 0:
            return DEFAULT_TARGET_TOTAL
        if enforce_minimum:
            return max(DEFAULT_TARGET_TOTAL, int(target))
        return int(target)

    @staticmethod
    def _safe_progress(fn: ProgressFn, done: int, total: int, msg: str) -> None:
        try:
            fn(done, total, msg)
        except Exception:  # pragma: no cover
            logger.debug("progress sink failed", exc_info=True)


# ---------------------------------------------------------------------------
# Small DSP helpers
# ---------------------------------------------------------------------------


def _rms(pcm: array.array) -> float:
    if not len(pcm):
        return 0.0
    acc = 0
    for s in pcm:
        acc += s * s
    return math.sqrt(acc / len(pcm))


def _clip16(value: int) -> int:
    if value > 32767:
        return 32767
    if value < -32768:
        return -32768
    return int(value)


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(value)))

"""Phoneme & viseme extraction for MimOSA lip-sync (M3.2).

This module turns *what MimOSA is about to say* into a **viseme timeline** -- a
time-ordered list of mouth shapes the avatar can animate in sync with audio
playback. It supports two paths, in order of preference:

1. **Phoneme-based** (accurate). When the Piper voice (or any injected
   phonemizer) can produce phonemes for the text, we map phonemes -> visemes
   (:mod:`mimosa.ui.viseme_mapper`) and estimate per-phoneme timing by
   distributing the known audio duration across phonemes, weighted by phoneme
   class (vowels are held longer than stop consonants). Piper does not expose
   per-phoneme durations through its public API, so this proportional model is
   the pragmatic, fully-local approach.

2. **Amplitude-based fallback** (robust). When phonemes are unavailable (older
   Piper, missing dependency, extraction error), we analyse the synthesized
   PCM's short-time energy envelope and drive a simple open/closed mouth. This
   guarantees the avatar still "talks" rather than crashing or freezing.

Everything is **local and private** -- no audio or text leaves the device, and
no network/3rd-party phoneme service is used. The module imports no GTK/Cairo
and only :mod:`wave`/:mod:`struct` from the stdlib for audio, so it loads fine
on a headless box. ``numpy`` is used opportunistically (lazy) for fast envelope
maths but is not required.
"""

from __future__ import annotations

import io
import json
import logging
import math
import struct
import wave
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Sequence, Tuple

from mimosa.ui.viseme_mapper import Viseme, VisemeMapper

logger = logging.getLogger(__name__)

# Minimum sensible viseme duration (seconds). Frames shorter than this read as
# visual jitter, so neighbouring frames are merged below it.
MIN_FRAME_SECONDS = 0.03


# -- data types ---------------------------------------------------------------


@dataclass
class PhonemeSpan:
    """A single phoneme placed on the timeline."""

    phoneme: str
    start: float
    end: float

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


@dataclass
class VisemeFrame:
    """A mouth shape held over a time interval ``[start, end)`` (seconds)."""

    viseme: Viseme
    start: float
    end: float

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)

    def contains(self, t: float) -> bool:
        return self.start <= t < self.end


@dataclass
class VisemeTimeline:
    """An ordered list of :class:`VisemeFrame` covering ``[0, duration)``.

    The timeline is the unit handed to the UI. It is pure data plus a couple of
    lookup helpers used by the audio-sync layer; it owns no playback state.
    """

    frames: List[VisemeFrame] = field(default_factory=list)
    duration: float = 0.0
    #: Provenance: ``"phonemes"``, ``"amplitude"``, or ``"empty"``.
    source: str = "empty"

    def __len__(self) -> int:
        return len(self.frames)

    def __bool__(self) -> bool:
        return bool(self.frames)

    @property
    def is_empty(self) -> bool:
        return not self.frames

    @classmethod
    def empty(cls) -> "VisemeTimeline":
        return cls(frames=[], duration=0.0, source="empty")

    def viseme_at(self, t: float) -> Viseme:
        """Return the viseme active at time ``t`` (clamped). Silence if empty."""
        if not self.frames:
            return Viseme.SILENCE
        if t <= self.frames[0].start:
            return self.frames[0].viseme
        for frame in self.frames:
            if frame.contains(t):
                return frame.viseme
        return self.frames[-1].viseme

    def frame_index_at(self, t: float) -> int:
        """Index of the frame active at ``t`` (0 if before start, last if after)."""
        if not self.frames:
            return -1
        if t <= self.frames[0].start:
            return 0
        for i, frame in enumerate(self.frames):
            if frame.contains(t):
                return i
        return len(self.frames) - 1

    def window_at(self, t: float) -> Tuple[Viseme, Viseme, float]:
        """Return ``(current, next, blend)`` for interpolation at time ``t``.

        ``blend`` (0..1) ramps up over the *trailing* portion of the current
        frame toward the next frame's shape, giving smooth coarticulation. When
        there is no next frame, it blends toward :attr:`Viseme.SILENCE` at the
        very end.
        """
        if not self.frames:
            return (Viseme.SILENCE, Viseme.SILENCE, 0.0)
        idx = self.frame_index_at(t)
        cur = self.frames[idx]
        nxt = self.frames[idx + 1] if idx + 1 < len(self.frames) else None

        # Blend over the last `blend_zone` of the current frame.
        blend_zone = min(0.06, cur.duration * 0.5) if cur.duration > 0 else 0.0
        if blend_zone <= 0:
            return (cur.viseme, cur.viseme, 0.0)
        blend_start = cur.end - blend_zone
        if t < blend_start:
            return (cur.viseme, cur.viseme, 0.0)
        blend = (t - blend_start) / blend_zone
        blend = max(0.0, min(1.0, blend))
        target = nxt.viseme if nxt is not None else Viseme.SILENCE
        return (cur.viseme, target, blend)


# -- phoneme timing estimation ------------------------------------------------


def _viseme_weight(viseme: Viseme) -> float:
    """Relative duration weight for a viseme class.

    Vowels are held longer than stops; fricatives sit in between. These weights
    are heuristic but produce natural-looking pacing when distributing a known
    total duration across phonemes.
    """
    if viseme in (Viseme.OPEN, Viseme.WIDE, Viseme.ROUNDED, Viseme.MID):
        return 1.7  # vowels / approximants -- sustained
    if viseme in (Viseme.CLOSED,):
        return 0.6  # bilabial stops -- brief
    if viseme in (Viseme.ALVEOLAR, Viseme.VELAR):
        return 0.8
    if viseme in (Viseme.LABIODENTAL, Viseme.DENTAL, Viseme.AFFRICATE):
        return 1.0  # fricatives / affricates
    return 0.5  # silence


def estimate_phoneme_timings(
    phonemes: Sequence[str],
    total_duration: float,
    mapper: Optional[VisemeMapper] = None,
) -> List[PhonemeSpan]:
    """Distribute ``total_duration`` across ``phonemes`` by class weight.

    Returns a list of :class:`PhonemeSpan` whose intervals tile ``[0,
    total_duration)`` with no gaps. Empty input or non-positive duration yields
    an empty list.
    """
    phonemes = [p for p in (phonemes or [])]
    if not phonemes or total_duration <= 0:
        return []
    mapper = mapper or VisemeMapper()
    weights = [max(0.01, _viseme_weight(mapper.map_one(p))) for p in phonemes]
    total_w = sum(weights)
    spans: List[PhonemeSpan] = []
    cursor = 0.0
    for i, (ph, w) in enumerate(zip(phonemes, weights)):
        if i == len(phonemes) - 1:
            end = total_duration  # avoid float drift on the final span
        else:
            end = cursor + total_duration * (w / total_w)
        spans.append(PhonemeSpan(phoneme=ph, start=cursor, end=end))
        cursor = end
    return spans


def _merge_frames(frames: List[VisemeFrame]) -> List[VisemeFrame]:
    """Merge adjacent frames with the same viseme and absorb tiny frames."""
    if not frames:
        return []
    merged: List[VisemeFrame] = [
        VisemeFrame(frames[0].viseme, frames[0].start, frames[0].end)
    ]
    for f in frames[1:]:
        last = merged[-1]
        if f.viseme == last.viseme:
            last.end = f.end
        elif f.duration < MIN_FRAME_SECONDS:
            # Too short to perceive -- extend the previous frame over it.
            last.end = f.end
        else:
            merged.append(VisemeFrame(f.viseme, f.start, f.end))
    return merged


def phonemes_to_viseme_timeline(
    spans: Sequence[PhonemeSpan],
    mapper: Optional[VisemeMapper] = None,
) -> VisemeTimeline:
    """Convert timed :class:`PhonemeSpan` s into a merged :class:`VisemeTimeline`."""
    mapper = mapper or VisemeMapper()
    if not spans:
        return VisemeTimeline.empty()
    raw = [VisemeFrame(mapper.map_one(s.phoneme), s.start, s.end) for s in spans]
    merged = _merge_frames(raw)
    duration = merged[-1].end if merged else 0.0
    return VisemeTimeline(frames=merged, duration=duration, source="phonemes")


# -- Piper phoneme-output parsing --------------------------------------------


def parse_piper_phoneme_output(data) -> Tuple[List[str], List[PhonemeSpan]]:
    """Parse Piper ``--output-phonemes`` style data into phonemes (+ spans).

    Piper / eSpeak phoneme dumps come in several shapes across versions. This
    parser is permissive and accepts any of:

    * a JSON string (object, array, or newline-delimited JSONL),
    * a ``dict`` with a ``"phonemes"`` list (strings, or ``{phoneme,start,end}``
      / ``{phoneme,duration}`` objects),
    * a ``list`` of phoneme strings,
    * a ``list`` of objects with ``phoneme`` and optional timing.

    Returns ``(phonemes, spans)``. ``spans`` is non-empty only when explicit
    timing was present; otherwise callers should estimate timing from the audio
    duration. Never raises -- malformed input yields ``([], [])``.
    """
    try:
        obj = _coerce_json(data)
    except Exception as exc:  # noqa: BLE001 - permissive parser
        logger.debug("Phoneme output not JSON-parseable: %s", exc)
        return ([], [])

    phonemes: List[str] = []
    timed: List[Tuple[str, Optional[float], Optional[float], Optional[float]]] = []

    def _ingest_item(item) -> None:
        if isinstance(item, str):
            phonemes.append(item)
            timed.append((item, None, None, None))
        elif isinstance(item, dict):
            ph = item.get("phoneme") or item.get("p") or item.get("symbol")
            if ph is None:
                return
            ph = str(ph)
            phonemes.append(ph)
            timed.append(
                (
                    ph,
                    _as_float(item.get("start")),
                    _as_float(item.get("end")),
                    _as_float(item.get("duration")),
                )
            )

    def _ingest_container(container) -> None:
        if isinstance(container, dict):
            seq = container.get("phonemes")
            if isinstance(seq, str):
                # A whitespace/character separated phoneme string.
                for tok in seq.split():
                    _ingest_item(tok)
            elif isinstance(seq, list):
                for it in seq:
                    _ingest_item(it)
            elif seq is None and "phoneme" in container:
                _ingest_item(container)
        elif isinstance(container, list):
            for it in container:
                if isinstance(it, (dict, str)):
                    _ingest_item(it)
                elif isinstance(it, list):
                    for sub in it:
                        _ingest_item(sub)

    if isinstance(obj, list) and obj and isinstance(obj[0], (dict, list)):
        for entry in obj:
            _ingest_container(entry if isinstance(entry, (dict, list)) else [entry])
    else:
        _ingest_container(obj)

    spans = _spans_from_timed(timed)
    return (phonemes, spans)


def _spans_from_timed(timed) -> List[PhonemeSpan]:
    """Build PhonemeSpans from (phoneme, start, end, duration) tuples if timed."""
    has_timing = any(
        (s is not None) or (e is not None) or (d is not None)
        for (_p, s, e, d) in timed
    )
    if not has_timing:
        return []
    spans: List[PhonemeSpan] = []
    cursor = 0.0
    for ph, start, end, dur in timed:
        s = start if start is not None else cursor
        if end is not None:
            e = end
        elif dur is not None:
            e = s + dur
        else:
            e = s  # zero-length; merge step will absorb it
        if e < s:
            e = s
        spans.append(PhonemeSpan(phoneme=ph, start=s, end=e))
        cursor = e
    return spans


def _coerce_json(data):
    if isinstance(data, (dict, list)):
        return data
    if isinstance(data, (bytes, bytearray)):
        data = data.decode("utf-8", errors="replace")
    if not isinstance(data, str):
        raise TypeError(f"unsupported phoneme data type {type(data)!r}")
    text = data.strip()
    if not text:
        return []
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Try JSONL: one JSON value per line.
        items = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            items.append(json.loads(line))
        if not items:
            raise
        return items


def _as_float(value) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# -- amplitude (energy-envelope) fallback ------------------------------------


def read_wav_pcm(wav_bytes: bytes) -> Tuple[bytes, int, int, int]:
    """Read a WAV container, returning ``(pcm, sample_rate, channels, sampwidth)``.

    Raises ``ValueError`` if the data is not a readable WAV.
    """
    try:
        with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
            channels = wf.getnchannels()
            sampwidth = wf.getsampwidth()
            rate = wf.getframerate()
            frames = wf.readframes(wf.getnframes())
        return (frames, rate, channels, sampwidth)
    except (wave.Error, EOFError, struct.error) as exc:
        raise ValueError(f"not a readable WAV: {exc}") from exc


def wav_duration_seconds(wav_bytes: bytes) -> float:
    """Return a WAV's duration in seconds (0.0 if unreadable/empty)."""
    try:
        pcm, rate, channels, sampwidth = read_wav_pcm(wav_bytes)
    except ValueError:
        return 0.0
    if rate <= 0 or channels <= 0 or sampwidth <= 0:
        return 0.0
    n_samples = len(pcm) // (channels * sampwidth)
    return n_samples / float(rate)


def amplitude_envelope(
    pcm: bytes,
    sample_rate: int,
    channels: int = 1,
    sampwidth: int = 2,
    frame_ms: float = 40.0,
) -> List[Tuple[float, float, float]]:
    """Compute a short-time RMS envelope normalized to ``0..1``.

    Returns a list of ``(start_s, end_s, level)`` windows. Uses ``numpy`` when
    available for speed, falling back to a pure-:mod:`struct` implementation.
    16-bit PCM is supported directly; other widths are handled by numpy when
    present, else approximated.
    """
    if sample_rate <= 0 or not pcm:
        return []
    frame_ms = max(5.0, float(frame_ms))
    samples_per_window = max(1, int(sample_rate * frame_ms / 1000.0))

    levels = _rms_windows(pcm, channels, sampwidth, samples_per_window)
    if not levels:
        return []
    peak = max(levels) or 1.0
    out: List[Tuple[float, float, float]] = []
    win_dur = samples_per_window / float(sample_rate)
    for i, lvl in enumerate(levels):
        start = i * win_dur
        out.append((start, start + win_dur, min(1.0, lvl / peak)))
    return out


def _rms_windows(pcm, channels, sampwidth, samples_per_window) -> List[float]:
    """Per-window RMS of (down-mixed mono) PCM. numpy-accelerated if present."""
    try:
        import numpy as np  # local, optional

        dtype = {1: np.int8, 2: np.int16, 4: np.int32}.get(sampwidth)
        if dtype is None:
            raise ValueError("unsupported sample width for numpy path")
        arr = np.frombuffer(pcm, dtype=dtype).astype(np.float64)
        if channels > 1:
            usable = (len(arr) // channels) * channels
            arr = arr[:usable].reshape(-1, channels).mean(axis=1)
        if arr.size == 0:
            return []
        norm = float(1 << (8 * sampwidth - 1))
        arr = arr / norm
        n_win = int(math.ceil(arr.size / samples_per_window))
        levels = []
        for i in range(n_win):
            chunk = arr[i * samples_per_window:(i + 1) * samples_per_window]
            if chunk.size == 0:
                continue
            levels.append(float(np.sqrt(np.mean(chunk * chunk))))
        return levels
    except Exception:  # noqa: BLE001 - fall through to pure python
        return _rms_windows_py(pcm, channels, sampwidth, samples_per_window)


def _rms_windows_py(pcm, channels, sampwidth, samples_per_window) -> List[float]:
    if sampwidth != 2:
        # Pure-python path only handles 16-bit; degrade to silence otherwise.
        return []
    total = len(pcm) // 2
    if total == 0:
        return []
    fmt_norm = 32768.0
    levels: List[float] = []
    acc = 0.0
    count = 0
    win_samples = 0
    # Iterate frames, down-mix channels by averaging.
    frame_count = total // channels
    idx = 0
    for _ in range(frame_count):
        s = 0.0
        for _c in range(channels):
            (val,) = struct.unpack_from("<h", pcm, idx * 2)
            s += val
            idx += 1
        s /= channels
        norm = s / fmt_norm
        acc += norm * norm
        count += 1
        win_samples += 1
        if win_samples >= samples_per_window:
            levels.append(math.sqrt(acc / count) if count else 0.0)
            acc = 0.0
            count = 0
            win_samples = 0
    if count:
        levels.append(math.sqrt(acc / count))
    return levels


def amplitude_to_viseme_timeline(
    pcm: bytes,
    sample_rate: int,
    channels: int = 1,
    sampwidth: int = 2,
    frame_ms: float = 40.0,
    open_threshold: float = 0.55,
    mid_threshold: float = 0.2,
    silence_threshold: float = 0.06,
) -> VisemeTimeline:
    """Build a coarse open/closed :class:`VisemeTimeline` from PCM energy.

    Level bands map to visemes: high energy -> :attr:`Viseme.OPEN`, medium ->
    :attr:`Viseme.MID`, low -> :attr:`Viseme.ALVEOLAR` (slightly open), and
    near-zero -> :attr:`Viseme.SILENCE`. Adjacent equal frames are merged.
    """
    env = amplitude_envelope(pcm, sample_rate, channels, sampwidth, frame_ms)
    if not env:
        return VisemeTimeline.empty()
    raw: List[VisemeFrame] = []
    for start, end, lvl in env:
        if lvl < silence_threshold:
            vis = Viseme.SILENCE
        elif lvl < mid_threshold:
            vis = Viseme.ALVEOLAR
        elif lvl < open_threshold:
            vis = Viseme.MID
        else:
            vis = Viseme.OPEN
        raw.append(VisemeFrame(vis, start, end))
    merged = _merge_frames(raw)
    duration = merged[-1].end if merged else 0.0
    return VisemeTimeline(frames=merged, duration=duration, source="amplitude")


# -- voice phonemization (best effort) ---------------------------------------


def phonemize_with_voice(voice, text: str) -> List[str]:
    """Best-effort phoneme extraction from a loaded Piper voice object.

    Tries a series of known APIs across piper-tts versions and returns a flat
    list of phoneme symbols, or ``[]`` if none worked. Never raises.
    """
    text = (text or "").strip()
    if not text or voice is None:
        return []
    for attr in ("phonemize", "phonemes", "text_to_phonemes"):
        fn = getattr(voice, attr, None)
        if callable(fn):
            try:
                result = fn(text)
                flat = _flatten_phonemes(result)
                if flat:
                    return flat
            except Exception as exc:  # noqa: BLE001
                logger.debug("voice.%s failed: %s", attr, exc)
    # Some versions expose a phonemizer on a config/sub-object.
    cfg = getattr(voice, "config", None)
    fn = getattr(cfg, "phonemize", None)
    if callable(fn):
        try:
            return _flatten_phonemes(fn(text))
        except Exception as exc:  # noqa: BLE001
            logger.debug("voice.config.phonemize failed: %s", exc)
    return []


def _flatten_phonemes(result) -> List[str]:
    """Flatten phonemizer output (str | list[str] | list[list[str]]) -> list[str]."""
    if result is None:
        return []
    if isinstance(result, str):
        # Could be a phoneme string; split on whitespace, else per-character.
        toks = result.split()
        if len(toks) > 1:
            return toks
        return list(result)
    flat: List[str] = []
    for item in result:
        if isinstance(item, str):
            flat.append(item)
        elif isinstance(item, (list, tuple)):
            for sub in item:
                if isinstance(sub, str):
                    flat.append(sub)
    return flat


# -- orchestrator -------------------------------------------------------------


class PhonemeExtractor:
    """Builds viseme timelines, preferring phonemes and falling back to audio.

    Args:
        mapper: A :class:`VisemeMapper` (a default one is created otherwise).
        phonemizer: Optional callable ``text -> list[str]`` (or nested lists).
            Injectable for tests / custom backends. When ``None``, a Piper voice
            passed to :meth:`extract` is probed for phonemization instead.
    """

    def __init__(
        self,
        mapper: Optional[VisemeMapper] = None,
        phonemizer: Optional[Callable[[str], Sequence[str]]] = None,
    ) -> None:
        self.mapper = mapper or VisemeMapper()
        self.phonemizer = phonemizer

    # -- individual paths --------------------------------------------------

    def from_phonemes(
        self, phonemes: Sequence[str], audio_duration: float
    ) -> VisemeTimeline:
        """Phonemes + total duration -> timeline (timing estimated by class)."""
        spans = estimate_phoneme_timings(phonemes, audio_duration, self.mapper)
        return phonemes_to_viseme_timeline(spans, self.mapper)

    def from_spans(self, spans: Sequence[PhonemeSpan]) -> VisemeTimeline:
        """Pre-timed phoneme spans -> timeline (timing preserved)."""
        return phonemes_to_viseme_timeline(spans, self.mapper)

    def from_piper_output(
        self, data, audio_duration: float
    ) -> VisemeTimeline:
        """Parse Piper phoneme output and build a timeline.

        Uses explicit timing when present; otherwise estimates from
        ``audio_duration``.
        """
        phonemes, spans = parse_piper_phoneme_output(data)
        if spans:
            return self.from_spans(spans)
        if phonemes:
            return self.from_phonemes(phonemes, audio_duration)
        return VisemeTimeline.empty()

    def from_audio(self, wav_bytes: bytes, **kwargs) -> VisemeTimeline:
        """Amplitude-envelope fallback straight from WAV bytes."""
        try:
            pcm, rate, channels, sampwidth = read_wav_pcm(wav_bytes)
        except ValueError:
            return VisemeTimeline.empty()
        return amplitude_to_viseme_timeline(
            pcm, rate, channels=channels, sampwidth=sampwidth, **kwargs
        )

    # -- orchestration -----------------------------------------------------

    def extract(
        self,
        text: Optional[str] = None,
        *,
        wav_bytes: Optional[bytes] = None,
        audio_duration: Optional[float] = None,
        voice=None,
        piper_phoneme_output=None,
    ) -> VisemeTimeline:
        """Produce the best available timeline; never raises.

        Resolution order:

        1. Explicit Piper phoneme output, if provided.
        2. Phonemes from the injected ``phonemizer`` or the Piper ``voice``.
        3. Amplitude analysis of ``wav_bytes``.
        4. An empty timeline (caller shows the basic speaking animation).

        ``audio_duration`` is derived from ``wav_bytes`` when not given.
        """
        try:
            if audio_duration is None and wav_bytes is not None:
                audio_duration = wav_duration_seconds(wav_bytes)
            dur = audio_duration or 0.0

            if piper_phoneme_output is not None:
                tl = self.from_piper_output(piper_phoneme_output, dur)
                if tl:
                    return tl

            phonemes: List[str] = []
            if text:
                if self.phonemizer is not None:
                    try:
                        phonemes = _flatten_phonemes(self.phonemizer(text))
                    except Exception as exc:  # noqa: BLE001
                        logger.debug("custom phonemizer failed: %s", exc)
                if not phonemes and voice is not None:
                    phonemes = phonemize_with_voice(voice, text)

            if phonemes and dur > 0:
                tl = self.from_phonemes(phonemes, dur)
                if tl:
                    return tl

            if wav_bytes is not None:
                tl = self.from_audio(wav_bytes)
                if tl:
                    return tl
        except Exception as exc:  # noqa: BLE001 - extraction must never crash TTS
            logger.warning("Viseme extraction failed (%s); using empty timeline", exc)

        return VisemeTimeline.empty()


def create_phoneme_extractor(**kwargs) -> PhonemeExtractor:
    """Factory mirroring the other ``create_*`` helpers in the voice package."""
    return PhonemeExtractor(**kwargs)

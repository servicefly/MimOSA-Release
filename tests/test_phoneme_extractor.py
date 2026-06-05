"""Tests for :mod:`mimosa.voice.phoneme_extractor`.

These tests are hermetic: no Piper, no audio device. Audio is synthesized in
memory with :mod:`wave`/:mod:`struct`. Phonemizers are injected as plain
callables / fakes so the extractor can be exercised without any TTS backend.
"""

import io
import json
import math
import struct
import wave

import pytest

from mimosa.ui.viseme_mapper import Viseme, VisemeMapper
from mimosa.voice import phoneme_extractor as pe
from mimosa.voice.phoneme_extractor import (
    PhonemeExtractor,
    PhonemeSpan,
    VisemeFrame,
    VisemeTimeline,
    amplitude_envelope,
    amplitude_to_viseme_timeline,
    create_phoneme_extractor,
    estimate_phoneme_timings,
    parse_piper_phoneme_output,
    phonemes_to_viseme_timeline,
    phonemize_with_voice,
    read_wav_pcm,
    wav_duration_seconds,
)


# -- helpers -----------------------------------------------------------------


def _make_wav(segments, rate=22050, channels=1, sampwidth=2):
    """Build a WAV from (duration_s, amplitude_0_1) segments. 16-bit PCM."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(sampwidth)
        wf.setframerate(rate)
        frames = bytearray()
        for dur, amp in segments:
            n = int(rate * dur)
            for i in range(n):
                # 220 Hz tone scaled by amp; silence when amp == 0.
                val = int(amp * 30000 * math.sin(2 * math.pi * 220 * i / rate))
                for _c in range(channels):
                    frames += struct.pack("<h", val)
        wf.writeframes(bytes(frames))
    return buf.getvalue()


# -- data types --------------------------------------------------------------


class TestTimelineTypes:
    def test_phoneme_span_duration(self):
        assert PhonemeSpan("a", 0.0, 0.5).duration == pytest.approx(0.5)
        assert PhonemeSpan("a", 0.5, 0.5).duration == 0.0

    def test_viseme_frame_contains_and_duration(self):
        f = VisemeFrame(Viseme.OPEN, 0.2, 0.6)
        assert f.duration == pytest.approx(0.4)
        assert f.contains(0.2)
        assert f.contains(0.59)
        assert not f.contains(0.6)  # half-open
        assert not f.contains(0.1)

    def test_empty_timeline(self):
        tl = VisemeTimeline.empty()
        assert tl.is_empty
        assert not tl
        assert len(tl) == 0
        assert tl.source == "empty"
        assert tl.viseme_at(0.0) is Viseme.SILENCE
        assert tl.frame_index_at(0.0) == -1
        assert tl.window_at(0.0) == (Viseme.SILENCE, Viseme.SILENCE, 0.0)

    def test_viseme_at_clamps(self):
        tl = VisemeTimeline(
            frames=[
                VisemeFrame(Viseme.OPEN, 0.0, 0.5),
                VisemeFrame(Viseme.CLOSED, 0.5, 1.0),
            ],
            duration=1.0,
            source="phonemes",
        )
        assert tl.viseme_at(-1.0) is Viseme.OPEN
        assert tl.viseme_at(0.25) is Viseme.OPEN
        assert tl.viseme_at(0.75) is Viseme.CLOSED
        assert tl.viseme_at(99.0) is Viseme.CLOSED

    def test_frame_index_at(self):
        tl = VisemeTimeline(
            frames=[
                VisemeFrame(Viseme.OPEN, 0.0, 0.5),
                VisemeFrame(Viseme.CLOSED, 0.5, 1.0),
            ],
            duration=1.0,
        )
        assert tl.frame_index_at(-1) == 0
        assert tl.frame_index_at(0.25) == 0
        assert tl.frame_index_at(0.6) == 1
        assert tl.frame_index_at(5) == 1

    def test_window_blends_toward_next(self):
        tl = VisemeTimeline(
            frames=[
                VisemeFrame(Viseme.OPEN, 0.0, 0.5),
                VisemeFrame(Viseme.CLOSED, 0.5, 1.0),
            ],
            duration=1.0,
        )
        # Early in the frame -> no blend.
        cur, nxt, blend = tl.window_at(0.1)
        assert cur is Viseme.OPEN and blend == 0.0
        # Near the end -> blends toward the next viseme.
        cur, nxt, blend = tl.window_at(0.49)
        assert cur is Viseme.OPEN
        assert nxt is Viseme.CLOSED
        assert blend > 0.0

    def test_window_last_frame_blends_to_silence(self):
        tl = VisemeTimeline(
            frames=[VisemeFrame(Viseme.OPEN, 0.0, 0.5)], duration=0.5
        )
        cur, nxt, blend = tl.window_at(0.499)
        assert cur is Viseme.OPEN
        assert nxt is Viseme.SILENCE
        assert blend > 0.0


# -- timing estimation -------------------------------------------------------


class TestEstimateTimings:
    def test_empty_or_zero_duration(self):
        assert estimate_phoneme_timings([], 1.0) == []
        assert estimate_phoneme_timings(["a"], 0.0) == []
        assert estimate_phoneme_timings(["a"], -1.0) == []

    def test_spans_tile_total_duration(self):
        spans = estimate_phoneme_timings(["h", "ɛ", "l", "oʊ"], 2.0)
        assert spans[0].start == 0.0
        assert spans[-1].end == pytest.approx(2.0)
        # Contiguous, no gaps.
        for a, b in zip(spans, spans[1:]):
            assert a.end == pytest.approx(b.start)

    def test_vowels_get_more_time_than_stops(self):
        # bilabial stop 'p' vs open vowel 'ɑ'
        spans = estimate_phoneme_timings(["p", "ɑ"], 1.0)
        by = {s.phoneme: s.duration for s in spans}
        assert by["ɑ"] > by["p"]


# -- phonemes -> timeline ----------------------------------------------------


class TestPhonemesToTimeline:
    def test_empty(self):
        assert phonemes_to_viseme_timeline([]).is_empty

    def test_merges_same_viseme(self):
        spans = [
            PhonemeSpan("m", 0.0, 0.2),
            PhonemeSpan("b", 0.2, 0.4),  # both bilabial -> CLOSED
            PhonemeSpan("ɑ", 0.4, 0.9),
        ]
        tl = phonemes_to_viseme_timeline(spans)
        assert tl.source == "phonemes"
        # m + b merge into a single CLOSED frame.
        assert len(tl) == 2
        assert tl.frames[0].viseme is Viseme.CLOSED
        assert tl.frames[0].end == pytest.approx(0.4)
        assert tl.duration == pytest.approx(0.9)


# -- Piper output parsing ----------------------------------------------------


class TestParsePiperOutput:
    def test_plain_list_of_strings(self):
        phonemes, spans = parse_piper_phoneme_output(["h", "ɛ", "l", "oʊ"])
        assert phonemes == ["h", "ɛ", "l", "oʊ"]
        assert spans == []  # no timing

    def test_dict_with_phoneme_string(self):
        phonemes, spans = parse_piper_phoneme_output({"phonemes": "h ɛ l oʊ"})
        assert phonemes == ["h", "ɛ", "l", "oʊ"]
        assert spans == []

    def test_json_string_object(self):
        data = json.dumps({"phonemes": ["a", "b"]})
        phonemes, spans = parse_piper_phoneme_output(data)
        assert phonemes == ["a", "b"]

    def test_timed_objects_produce_spans(self):
        data = {
            "phonemes": [
                {"phoneme": "h", "start": 0.0, "end": 0.1},
                {"phoneme": "ɛ", "start": 0.1, "end": 0.5},
            ]
        }
        phonemes, spans = parse_piper_phoneme_output(data)
        assert phonemes == ["h", "ɛ"]
        assert len(spans) == 2
        assert spans[1].start == pytest.approx(0.1)
        assert spans[1].end == pytest.approx(0.5)

    def test_duration_objects_produce_spans(self):
        data = [
            {"phoneme": "h", "duration": 0.1},
            {"phoneme": "ɛ", "duration": 0.4},
        ]
        phonemes, spans = parse_piper_phoneme_output(data)
        assert phonemes == ["h", "ɛ"]
        assert spans[0].end == pytest.approx(0.1)
        assert spans[1].start == pytest.approx(0.1)
        assert spans[1].end == pytest.approx(0.5)

    def test_jsonl(self):
        data = "\n".join(
            json.dumps({"phoneme": p, "duration": 0.1}) for p in ["a", "b", "c"]
        )
        phonemes, spans = parse_piper_phoneme_output(data)
        assert phonemes == ["a", "b", "c"]
        assert len(spans) == 3

    def test_malformed_returns_empty(self):
        assert parse_piper_phoneme_output("not json {{{") == ([], [])
        assert parse_piper_phoneme_output(None) == ([], [])
        assert parse_piper_phoneme_output(12345) == ([], [])


# -- WAV / amplitude ---------------------------------------------------------


class TestWavAndAmplitude:
    def test_read_wav_pcm(self):
        wav = _make_wav([(0.1, 0.5)], rate=22050)
        pcm, rate, channels, sampwidth = read_wav_pcm(wav)
        assert rate == 22050
        assert channels == 1
        assert sampwidth == 2
        assert len(pcm) == int(22050 * 0.1) * 2

    def test_read_wav_pcm_bad_raises(self):
        with pytest.raises(ValueError):
            read_wav_pcm(b"not a wav file")

    def test_wav_duration_seconds(self):
        wav = _make_wav([(0.5, 0.4)], rate=22050)
        assert wav_duration_seconds(wav) == pytest.approx(0.5, abs=0.01)

    def test_wav_duration_bad_returns_zero(self):
        assert wav_duration_seconds(b"garbage") == 0.0
        assert wav_duration_seconds(b"") == 0.0

    def test_amplitude_envelope_empty(self):
        assert amplitude_envelope(b"", 22050) == []
        assert amplitude_envelope(b"\x00\x00", 0) == []

    def test_amplitude_envelope_levels(self):
        wav = _make_wav([(0.3, 0.0), (0.3, 1.0)], rate=22050)
        pcm, rate, ch, sw = read_wav_pcm(wav)
        env = amplitude_envelope(pcm, rate, ch, sw, frame_ms=40)
        assert env
        # First windows (silence) low, last windows (tone) high.
        assert env[0][2] < 0.2
        assert env[-1][2] > 0.5

    def test_amplitude_pure_python_matches_shape(self):
        # Force the pure-python path by calling it directly.
        wav = _make_wav([(0.2, 0.0), (0.2, 1.0)], rate=22050)
        pcm, rate, ch, sw = read_wav_pcm(wav)
        spw = int(rate * 0.04)
        levels = pe._rms_windows_py(pcm, ch, sw, spw)
        assert levels
        assert levels[0] < levels[-1]

    def test_amplitude_to_viseme_timeline(self):
        wav = _make_wav([(0.4, 0.0), (0.4, 1.0)], rate=22050)
        pcm, rate, ch, sw = read_wav_pcm(wav)
        tl = amplitude_to_viseme_timeline(pcm, rate, ch, sw)
        assert tl.source == "amplitude"
        assert tl.frames[0].viseme is Viseme.SILENCE
        assert tl.frames[-1].viseme in (Viseme.OPEN, Viseme.MID)

    def test_amplitude_empty_pcm_timeline(self):
        assert amplitude_to_viseme_timeline(b"", 22050).is_empty


# -- voice phonemization -----------------------------------------------------


class FakeVoicePhonemize:
    def phonemize(self, text):
        return ["h", "ɛ", "l", "oʊ"]


class FakeVoiceNested:
    def text_to_phonemes(self, text):
        return [["h", "ɛ"], ["l", "oʊ"]]


class FakeVoiceRaises:
    def phonemize(self, text):
        raise RuntimeError("boom")


class TestPhonemizeWithVoice:
    def test_none_voice_or_empty_text(self):
        assert phonemize_with_voice(None, "hi") == []
        assert phonemize_with_voice(FakeVoicePhonemize(), "") == []

    def test_phonemize_method(self):
        assert phonemize_with_voice(FakeVoicePhonemize(), "hello") == [
            "h", "ɛ", "l", "oʊ",
        ]

    def test_nested_flattened(self):
        assert phonemize_with_voice(FakeVoiceNested(), "hello") == [
            "h", "ɛ", "l", "oʊ",
        ]

    def test_raises_returns_empty(self):
        assert phonemize_with_voice(FakeVoiceRaises(), "hello") == []


class TestFlattenPhonemes:
    def test_none(self):
        assert pe._flatten_phonemes(None) == []

    def test_space_separated_string(self):
        assert pe._flatten_phonemes("a b c") == ["a", "b", "c"]

    def test_single_token_string_splits_chars(self):
        assert pe._flatten_phonemes("abc") == ["a", "b", "c"]

    def test_list_and_nested(self):
        assert pe._flatten_phonemes(["a", ["b", "c"]]) == ["a", "b", "c"]


# -- orchestrator ------------------------------------------------------------


class TestPhonemeExtractor:
    def test_from_phonemes(self):
        ex = PhonemeExtractor()
        tl = ex.from_phonemes(["h", "ɛ", "l", "oʊ"], 1.0)
        assert tl.source == "phonemes"
        assert tl.duration == pytest.approx(1.0)

    def test_from_spans(self):
        ex = PhonemeExtractor()
        spans = [PhonemeSpan("ɑ", 0.0, 0.5), PhonemeSpan("m", 0.5, 0.8)]
        tl = ex.from_spans(spans)
        assert tl.source == "phonemes"
        assert tl.duration == pytest.approx(0.8)

    def test_from_piper_output_timed(self):
        ex = PhonemeExtractor()
        data = {"phonemes": [{"phoneme": "ɑ", "start": 0, "end": 0.5}]}
        tl = ex.from_piper_output(data, 0.5)
        assert tl.source == "phonemes"

    def test_from_piper_output_untimed_uses_duration(self):
        ex = PhonemeExtractor()
        tl = ex.from_piper_output(["h", "ɛ", "l", "oʊ"], 1.5)
        assert tl.source == "phonemes"
        assert tl.duration == pytest.approx(1.5)

    def test_from_piper_output_empty(self):
        ex = PhonemeExtractor()
        assert ex.from_piper_output("garbage{{", 1.0).is_empty

    def test_from_audio(self):
        ex = PhonemeExtractor()
        wav = _make_wav([(0.3, 0.0), (0.3, 1.0)], rate=22050)
        tl = ex.from_audio(wav)
        assert tl.source == "amplitude"

    def test_from_audio_bad_bytes(self):
        ex = PhonemeExtractor()
        assert ex.from_audio(b"nope").is_empty

    def test_extract_prefers_piper_output(self):
        ex = PhonemeExtractor()
        tl = ex.extract(
            text="hello",
            piper_phoneme_output={"phonemes": [{"phoneme": "ɑ", "duration": 0.5}]},
            audio_duration=0.5,
        )
        assert tl.source == "phonemes"

    def test_extract_uses_injected_phonemizer(self):
        ex = PhonemeExtractor(phonemizer=lambda t: ["h", "ɛ", "l", "oʊ"])
        tl = ex.extract(text="hello", audio_duration=1.0)
        assert tl.source == "phonemes"
        assert tl.duration == pytest.approx(1.0)

    def test_extract_falls_back_to_voice(self):
        ex = PhonemeExtractor()
        tl = ex.extract(text="hello", voice=FakeVoicePhonemize(), audio_duration=1.0)
        assert tl.source == "phonemes"

    def test_extract_falls_back_to_amplitude(self):
        ex = PhonemeExtractor()  # no phonemizer, no voice
        wav = _make_wav([(0.3, 0.0), (0.3, 1.0)], rate=22050)
        tl = ex.extract(text="hello", wav_bytes=wav)
        assert tl.source == "amplitude"

    def test_extract_derives_duration_from_wav(self):
        ex = PhonemeExtractor(phonemizer=lambda t: ["ɑ", "m"])
        wav = _make_wav([(0.8, 0.5)], rate=22050)
        tl = ex.extract(text="am", wav_bytes=wav)
        # Phoneme path wins; duration derived from the wav (~0.8s).
        assert tl.source == "phonemes"
        assert tl.duration == pytest.approx(0.8, abs=0.05)

    def test_extract_empty_when_nothing(self):
        ex = PhonemeExtractor()
        assert ex.extract().is_empty
        assert ex.extract(text="hello").is_empty  # no duration, no audio

    def test_extract_never_raises_on_bad_phonemizer(self):
        def boom(_t):
            raise RuntimeError("nope")

        ex = PhonemeExtractor(phonemizer=boom)
        wav = _make_wav([(0.3, 1.0)], rate=22050)
        # Should swallow the phonemizer error and fall back to amplitude.
        tl = ex.extract(text="hi", wav_bytes=wav)
        assert tl.source == "amplitude"

    def test_create_factory(self):
        ex = create_phoneme_extractor()
        assert isinstance(ex, PhonemeExtractor)
        assert isinstance(ex.mapper, VisemeMapper)

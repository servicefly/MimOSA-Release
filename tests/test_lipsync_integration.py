"""Integration tests for the M3.2 lip-sync pipeline.

Covers the wiring between the pieces without needing GTK, Cairo, a display, or
Piper:

* :class:`AvatarRenderer` lip-sync engagement / ticking / teardown,
* :class:`UIConfig` lip-sync fields (validation, clamping, save+load roundtrip),
* :meth:`PiperTTS.synthesize_with_visemes` with a monkeypatched ``synthesize``
  and injected phonemizer / amplitude fallback / bad-audio paths.
"""

import io
import math
import struct
import wave

import pytest

from mimosa.ui.avatar_renderer import AvatarRenderer, UIState
from mimosa.ui.ui_config import (
    DEFAULT_MOUTH_STYLE,
    DEFAULT_VISEME_SPEED,
    MAX_VISEME_SPEED,
    MIN_VISEME_SPEED,
    UIConfig,
)
from mimosa.ui.viseme_mapper import Viseme
from mimosa.voice.phoneme_extractor import (
    PhonemeExtractor,
    VisemeFrame,
    VisemeTimeline,
)


def _make_wav(segments, rate=22050, channels=1, sampwidth=2):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(sampwidth)
        wf.setframerate(rate)
        frames = bytearray()
        for dur, amp in segments:
            n = int(rate * dur)
            for i in range(n):
                val = int(amp * 30000 * math.sin(2 * math.pi * 220 * i / rate))
                frames += struct.pack("<h", val)
        wf.writeframes(bytes(frames))
    return buf.getvalue()


def _timeline(duration=1.0):
    return VisemeTimeline(
        frames=[
            VisemeFrame(Viseme.OPEN, 0.0, duration / 2),
            VisemeFrame(Viseme.CLOSED, duration / 2, duration),
        ],
        duration=duration,
        source="phonemes",
    )


# -- AvatarRenderer lip-sync -------------------------------------------------


class TestRendererLipsync:
    def test_set_timeline_engages(self):
        r = AvatarRenderer(lipsync_enabled=True)
        r.set_state(UIState.SPEAKING)
        r.set_viseme_timeline(_timeline())
        assert r.lipsync_active
        assert r.audio_sync.active

    def test_empty_timeline_does_not_engage(self):
        r = AvatarRenderer(lipsync_enabled=True)
        r.set_viseme_timeline(VisemeTimeline.empty())
        assert not r.lipsync_active

    def test_disabled_does_not_engage(self):
        r = AvatarRenderer(lipsync_enabled=False)
        r.set_viseme_timeline(_timeline())
        assert not r.lipsync_active

    def test_tick_advances_mouth_toward_open(self):
        r = AvatarRenderer(lipsync_enabled=True, viseme_speed=20.0)
        r.set_state(UIState.SPEAKING)
        r.set_viseme_timeline(_timeline(duration=2.0))
        for _ in range(30):
            r.tick(1 / 60)
        # Early in the timeline the active viseme is OPEN -> mouth opens.
        assert r.mouth.current_shape.opening > 0.4

    def test_leaving_speaking_clears_lipsync(self):
        r = AvatarRenderer(lipsync_enabled=True)
        r.set_state(UIState.SPEAKING)
        r.set_viseme_timeline(_timeline())
        assert r.lipsync_active
        r.set_state(UIState.IDLE)
        assert not r.lipsync_active
        assert not r.audio_sync.active

    def test_clear_viseme_timeline(self):
        r = AvatarRenderer(lipsync_enabled=True)
        r.set_state(UIState.SPEAKING)
        r.set_viseme_timeline(_timeline())
        r.clear_viseme_timeline()
        assert not r.lipsync_active

    def test_set_lipsync_enabled_runtime_toggle(self):
        r = AvatarRenderer(lipsync_enabled=True)
        r.set_state(UIState.SPEAKING)
        r.set_viseme_timeline(_timeline())
        r.set_lipsync_enabled(False)
        assert not r.lipsync_active
        assert not r.lipsync_enabled

    def test_tick_never_raises_after_timeline_end(self):
        r = AvatarRenderer(lipsync_enabled=True)
        r.set_state(UIState.SPEAKING)
        r.set_viseme_timeline(_timeline(duration=0.1))
        # Tick well past the end; mouth should ease shut without error.
        for _ in range(120):
            r.tick(1 / 60)
        assert r.mouth.current_shape.opening < 0.2

    def test_from_config(self):
        cfg = UIConfig(
            lipsync_enabled=True,
            viseme_speed=18.0,
            mouth_style="cartoon",
            lipsync_latency=0.1,
            lipsync_debug=True,
        )
        r = AvatarRenderer.from_config(cfg)
        assert r.lipsync_enabled is True
        assert r.lipsync_debug is True
        assert r.mouth.style == "cartoon"
        assert r.mouth.interpolation_speed == pytest.approx(18.0)
        assert r.audio_sync.latency_offset == pytest.approx(0.1)


# -- UIConfig lip-sync fields ------------------------------------------------


class TestUIConfigLipsync:
    def test_defaults(self):
        cfg = UIConfig()
        assert cfg.lipsync_enabled is True
        assert cfg.viseme_speed == DEFAULT_VISEME_SPEED
        assert cfg.mouth_style == DEFAULT_MOUTH_STYLE
        assert cfg.lipsync_latency == pytest.approx(0.05)
        assert cfg.lipsync_debug is False

    def test_viseme_speed_clamped(self):
        assert UIConfig(viseme_speed=999).validate().viseme_speed == MAX_VISEME_SPEED
        assert UIConfig(viseme_speed=0).validate().viseme_speed == MIN_VISEME_SPEED

    def test_bad_viseme_speed_defaults(self):
        assert UIConfig(viseme_speed="nope").validate().viseme_speed == DEFAULT_VISEME_SPEED

    def test_invalid_mouth_style_falls_back(self):
        assert UIConfig(mouth_style="weird").validate().mouth_style == DEFAULT_MOUTH_STYLE

    def test_latency_clamped(self):
        assert UIConfig(lipsync_latency=99).validate().lipsync_latency == pytest.approx(1.0)
        assert UIConfig(lipsync_latency=-99).validate().lipsync_latency == pytest.approx(-0.5)

    def test_bool_coercion(self):
        cfg = UIConfig(lipsync_enabled=1, lipsync_debug=0).validate()
        assert cfg.lipsync_enabled is True
        assert cfg.lipsync_debug is False

    def test_save_load_roundtrip(self, tmp_path):
        path = tmp_path / "ui.json"
        cfg = UIConfig(
            lipsync_enabled=False,
            viseme_speed=20.0,
            mouth_style="minimal",
            lipsync_latency=0.2,
            lipsync_debug=True,
        )
        assert cfg.save(path)
        loaded = UIConfig.load(path)
        assert loaded.lipsync_enabled is False
        assert loaded.viseme_speed == pytest.approx(20.0)
        assert loaded.mouth_style == "minimal"
        assert loaded.lipsync_latency == pytest.approx(0.2)
        assert loaded.lipsync_debug is True

    def test_from_dict_ignores_unknown(self):
        cfg = UIConfig.from_dict({"mouth_style": "cartoon", "bogus": 123})
        assert cfg.mouth_style == "cartoon"


# -- PiperTTS.synthesize_with_visemes ----------------------------------------


class FakePhonemeVoice:
    def phonemize(self, text):
        return ["h", "ɛ", "l", "oʊ"]


class TestSynthesizeWithVisemes:
    def _engine(self, monkeypatch, wav):
        from mimosa.voice.tts import PiperTTS

        engine = PiperTTS()
        monkeypatch.setattr(engine, "synthesize", lambda text: wav)
        return engine

    def test_phoneme_path_via_voice(self, monkeypatch):
        wav = _make_wav([(1.0, 0.5)])
        engine = self._engine(monkeypatch, wav)
        engine._voice_obj = FakePhonemeVoice()
        wav_out, tl = engine.synthesize_with_visemes("hello")
        assert wav_out == wav
        assert tl.source == "phonemes"
        assert not tl.is_empty

    def test_injected_extractor_phonemizer(self, monkeypatch):
        wav = _make_wav([(1.0, 0.5)])
        engine = self._engine(monkeypatch, wav)
        ext = PhonemeExtractor(phonemizer=lambda t: ["ɑ", "m"])
        wav_out, tl = engine.synthesize_with_visemes("am", extractor=ext)
        assert tl.source == "phonemes"

    def test_amplitude_fallback(self, monkeypatch):
        # No voice, no phonemizer -> amplitude analysis of the audio.
        wav = _make_wav([(0.4, 0.0), (0.4, 1.0)])
        engine = self._engine(monkeypatch, wav)
        engine._voice_obj = None
        wav_out, tl = engine.synthesize_with_visemes("hello")
        assert tl.source == "amplitude"

    def test_bad_audio_yields_empty(self, monkeypatch):
        engine = self._engine(monkeypatch, b"not a wav")
        engine._voice_obj = None
        wav_out, tl = engine.synthesize_with_visemes("hello")
        assert wav_out == b"not a wav"
        assert tl.is_empty

    def test_extraction_never_raises(self, monkeypatch):
        wav = _make_wav([(0.5, 0.5)])
        engine = self._engine(monkeypatch, wav)

        class BoomVoice:
            def phonemize(self, text):
                raise RuntimeError("kaboom")

        engine._voice_obj = BoomVoice()
        # Should fall back to amplitude, not raise.
        wav_out, tl = engine.synthesize_with_visemes("hello")
        assert tl.source in ("amplitude", "empty")

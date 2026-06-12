"""Tests for the MimOSA local voice pipeline (M1.2).

These tests are written to pass on a **headless CI machine with no audio
hardware and no heavy ML dependencies installed** (no PyAudio, Whisper, Piper,
or openWakeWord). Every external/optional backend is mocked, and we assert on the
graceful-degradation behavior that the privacy-focused, local-first design
guarantees.

Run with:  pytest -q tests/test_voice_pipeline.py
"""

from __future__ import annotations

import io
import struct
import wave

import pytest

from mimosa.voice import audio_manager as am
from mimosa.voice import stt as stt_mod
from mimosa.voice import tts as tts_mod
from mimosa.voice import wake_word as ww
from mimosa.voice import voice_loop as vl


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _pcm(samples):
    """Pack a list of int16 samples into little-endian PCM bytes."""
    return struct.pack("<" + "h" * len(samples), *samples)


def _silence(n):
    return _pcm([0] * n)


def _loud(n, amplitude=10000):
    # Alternating +/- amplitude => high RMS.
    return _pcm([amplitude if i % 2 == 0 else -amplitude for i in range(n)])


# ---------------------------------------------------------------------------
# AudioManager
# ---------------------------------------------------------------------------

class TestAudioManager:
    def test_import_and_construct_without_backend(self):
        # Constructing must never require PyAudio.
        mgr = am.AudioManager()
        assert mgr.sample_rate == 16000
        assert mgr.channels == 1

    def test_is_available_false_without_pyaudio(self):
        # On this VM PyAudio is not installed, so availability is False and it
        # must NOT raise.
        mgr = am.AudioManager()
        assert mgr.is_available() is False

    def test_record_raises_audio_unavailable(self):
        mgr = am.AudioManager()
        with pytest.raises(am.AudioUnavailableError):
            mgr.record(1.0)

    def test_rms_silence_is_zero(self):
        assert am.AudioManager.rms(_silence(512)) == 0.0

    def test_rms_loud_is_high(self):
        val = am.AudioManager.rms(_loud(512, 10000))
        assert val > 9000

    def test_rms_empty_is_zero(self):
        assert am.AudioManager.rms(b"") == 0.0

    def test_save_wav_roundtrip(self, tmp_path):
        mgr = am.AudioManager()
        pcm = _loud(160)
        out = tmp_path / "out.wav"
        mgr.save_wav(pcm, str(out))
        assert out.exists()
        with wave.open(str(out), "rb") as wf:
            assert wf.getnchannels() == 1
            assert wf.getsampwidth() == 2
            assert wf.getframerate() == 16000
            assert wf.readframes(wf.getnframes()) == pcm

    # --- device selection (Fix #1) ---------------------------------------

    def test_device_index_param_sets_input_device(self):
        mgr = am.AudioManager(device_index=4)
        assert mgr.input_device == 4
        assert mgr.device_index == 4

    def test_device_index_setter_aliases_input_device(self):
        mgr = am.AudioManager()
        mgr.device_index = 9
        assert mgr.input_device == 9
        mgr.device_index = None
        assert mgr.input_device is None

    def test_resolve_device_index_passthrough_and_parsing(self):
        # Without a backend, numeric values pass through unchanged.
        assert am.AudioManager.resolve_device_index(3) == 3
        assert am.AudioManager.resolve_device_index("3") == 3
        # Blank / None -> system default (None).
        assert am.AudioManager.resolve_device_index("") is None
        assert am.AudioManager.resolve_device_index(None) is None

    def test_list_input_devices_is_static_and_safe(self):
        # No PyAudio on this VM -> empty list, never raises.
        assert am.AudioManager.list_input_devices() == []

    def test_get_default_input_device_safe(self):
        assert am.AudioManager.get_default_input_device() is None

    def test_measure_levels_raises_without_backend(self):
        mgr = am.AudioManager()
        with pytest.raises(am.AudioUnavailableError):
            mgr.measure_levels(seconds=0.1)


# ---------------------------------------------------------------------------
# Wake word
# ---------------------------------------------------------------------------

class TestWakeWord:
    def test_factory_returns_a_detector(self):
        # The factory never raises just because a package/model is missing; it
        # returns a usable backend (openWakeWord when available, else energy).
        detector = ww.create_wake_word_detector("hey mimosa")
        assert isinstance(detector, ww.BaseWakeWord)
        assert isinstance(detector, (ww.OpenWakeWord, ww.EnergyWakeWord))

    def test_factory_force_energy(self):
        # prefer_openwakeword=False forces the dependency-free energy fallback.
        detector = ww.create_wake_word_detector("hey mimosa",
                                                prefer_openwakeword=False)
        assert isinstance(detector, ww.EnergyWakeWord)

    def test_factory_falls_back_when_openwakeword_unavailable(self, monkeypatch):
        # Simulate openWakeWord failing to initialise -> must fall back, never raise.
        def _boom(*args, **kwargs):
            raise ww.WakeWordError("simulated missing openwakeword")

        monkeypatch.setattr(ww, "OpenWakeWord", _boom)
        detector = ww.create_wake_word_detector("hey mimosa")
        assert isinstance(detector, ww.EnergyWakeWord)

    def test_energy_detector_attributes(self):
        detector = ww.EnergyWakeWord(sample_rate=16000, frame_length=512)
        assert detector.sample_rate == 16000
        assert detector.frame_length == 512
        assert detector.wake_word == "hey mimosa"

    def test_energy_process_silence_is_false(self):
        detector = ww.EnergyWakeWord(frame_length=512)
        # A run of silence should not trigger.
        triggered = any(detector.process(_silence(512)) for _ in range(5))
        assert triggered is False

    def test_energy_process_loud_eventually_triggers(self):
        detector = ww.EnergyWakeWord(frame_length=512)
        # Sustained loud audio should eventually cross the threshold.
        triggered = any(detector.process(_loud(512, 12000)) for _ in range(10))
        assert triggered is True

    def test_openwakeword_resolves_builtin_model(self):
        # Known phrases map to bundled models; unknown ones use the stand-in.
        assert ww.OpenWakeWord._resolve_builtin_model("hey jarvis") == "hey_jarvis"
        assert ww.OpenWakeWord._resolve_builtin_model("alexa") == "alexa"
        assert (
            ww.OpenWakeWord._resolve_builtin_model("hey mimosa")
            == ww.OPENWAKEWORD_DEFAULT_MODEL
        )

    def test_openwakeword_missing_package_raises_wakeworderror(self, monkeypatch):
        # Direct construction surfaces the dependency error when the package is
        # unavailable (the factory swallows it; constructing directly does not).
        import builtins

        real_import = builtins.__import__

        def _fake_import(name, *args, **kwargs):
            if name == "openwakeword" or name.startswith("openwakeword."):
                raise ImportError("simulated missing openwakeword")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", _fake_import)
        with pytest.raises(ww.WakeWordError):
            ww.OpenWakeWord(wake_word="hey mimosa")

    def test_openwakeword_detects_with_fake_engine(self, monkeypatch):
        # Drive process() with a stub engine to verify thresholding without
        # loading real ONNX models.
        detector = ww.OpenWakeWord.__new__(ww.OpenWakeWord)  # bypass __init__
        import numpy as np

        detector._np = np
        detector.threshold = 0.5
        detector._model_key = "hey_jarvis"
        detector.frame_length = ww.OpenWakeWord.FRAME_LENGTH
        detector.sample_rate = ww.OpenWakeWord.SAMPLE_RATE

        class _Engine:
            def __init__(self):
                self.score = 0.0

            def predict(self, samples):
                return {"hey_jarvis": self.score}

        detector._engine = _Engine()
        frame = _silence(ww.OpenWakeWord.FRAME_LENGTH)
        detector._engine.score = 0.1
        assert detector.process(frame) is False
        detector._engine.score = 0.9
        assert detector.process(frame) is True


# ---------------------------------------------------------------------------
# STT (Whisper) -- mocked
# ---------------------------------------------------------------------------

class TestWhisperSTT:
    def test_construct_uses_env(self, monkeypatch):
        monkeypatch.setenv("WHISPER_MODEL", "small")
        engine = stt_mod.WhisperSTT()
        assert engine.model_size == "small"

    def test_default_model(self, monkeypatch):
        monkeypatch.delenv("WHISPER_MODEL", raising=False)
        engine = stt_mod.WhisperSTT()
        assert engine.model_size == stt_mod.DEFAULT_WHISPER_MODEL

    def test_is_available_false_without_whisper(self):
        assert stt_mod.WhisperSTT().is_available() is False

    def test_load_raises_without_whisper(self):
        with pytest.raises(stt_mod.STTError):
            stt_mod.WhisperSTT().load()

    def test_transcribe_pcm_with_mocked_model(self):
        engine = stt_mod.WhisperSTT()

        class _FakeModel:
            def transcribe(self, audio, **kwargs):
                # numpy array of the right dtype should reach us.
                assert hasattr(audio, "dtype")
                return {"text": "  hello world  "}

        engine._model = _FakeModel()
        text = engine.transcribe_pcm(_loud(16000), sample_rate=16000)
        assert text == "hello world"

    def test_transcribe_pcm_resamples(self):
        engine = stt_mod.WhisperSTT()
        captured = {}

        class _FakeModel:
            def transcribe(self, audio, **kwargs):
                captured["len"] = len(audio)
                return {"text": "ok"}

        engine._model = _FakeModel()
        # 8 kHz input, 1 second -> should resample to ~16 kHz samples.
        engine.transcribe_pcm(_loud(8000), sample_rate=8000)
        assert captured["len"] == pytest.approx(16000, abs=2)

    def test_transcribe_rejects_non_16bit(self):
        engine = stt_mod.WhisperSTT()
        engine._model = object()  # won't be reached
        with pytest.raises(stt_mod.STTError):
            engine.transcribe_pcm(b"abc", sample_rate=16000, sample_width=4)

    def test_transcribe_file_missing(self):
        engine = stt_mod.WhisperSTT()
        with pytest.raises(stt_mod.STTError):
            engine.transcribe_file("/nonexistent/file.wav")


# ---------------------------------------------------------------------------
# TTS (Piper) -- mocked
# ---------------------------------------------------------------------------

class TestPiperTTS:
    def test_construct_uses_env(self, monkeypatch):
        monkeypatch.setenv("PIPER_VOICE", "en_GB-alan-medium")
        engine = tts_mod.PiperTTS()
        assert engine.voice == "en_GB-alan-medium"

    def test_default_voice(self, monkeypatch):
        monkeypatch.delenv("PIPER_VOICE", raising=False)
        engine = tts_mod.PiperTTS()
        assert engine.voice == tts_mod.DEFAULT_PIPER_VOICE

    def test_is_available_false_without_piper(self):
        assert tts_mod.PiperTTS().is_available() is False

    def test_load_raises_without_piper(self):
        with pytest.raises(tts_mod.TTSError):
            tts_mod.PiperTTS().load()

    def test_synthesize_with_mocked_voice(self):
        engine = tts_mod.PiperTTS()

        class _FakeVoice:
            # Exercise the synthesize_wav path: write a tiny valid WAV.
            def synthesize_wav(self, text, wav_file, length_scale=1.0):
                assert text == "hello"
                wav_file.setnchannels(1)
                wav_file.setsampwidth(2)
                wav_file.setframerate(22050)
                wav_file.writeframes(_loud(100))

        engine._voice_obj = _FakeVoice()
        out = engine.synthesize("hello")
        # Output should be a parseable WAV container.
        with wave.open(io.BytesIO(out), "rb") as wf:
            assert wf.getnchannels() == 1
            assert wf.getframerate() == 22050

    def test_speed_inverts_to_length_scale(self):
        engine = tts_mod.PiperTTS(speed=2.0)
        captured = {}

        class _FakeVoice:
            def synthesize_wav(self, text, wav_file, length_scale=1.0):
                captured["ls"] = length_scale
                wav_file.setnchannels(1)
                wav_file.setsampwidth(2)
                wav_file.setframerate(22050)

        engine._voice_obj = _FakeVoice()
        engine.synthesize("x")
        # speed 2.0 -> length_scale 0.5 (faster).
        assert captured["ls"] == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# VoiceLoop state machine
# ---------------------------------------------------------------------------

class _FakeAudio:
    """Minimal AudioManager stand-in for loop tests."""

    sample_rate = 16000

    def __init__(self, pcm=b"x"):
        self._pcm = pcm
        self.played = []
        self.closed = False

    def record_until_silence(self, max_seconds=15.0):
        return self._pcm

    def play_wav_bytes(self, wav_bytes):
        self.played.append(wav_bytes)

    def close(self):
        self.closed = True


class _FakeSTT:
    def __init__(self, text="turn on the lights"):
        self.text = text

    def transcribe_pcm(self, pcm, sample_rate=16000):
        return self.text


class _FakeTTS:
    def __init__(self):
        self.spoken = []

    def synthesize(self, text):
        self.spoken.append(text)
        return b"WAVDATA"


class TestVoiceLoop:
    def test_initial_state_idle(self):
        loop = vl.VoiceLoop(audio_manager=_FakeAudio())
        assert loop.state is vl.VoiceState.IDLE

    def test_run_once_echo_pipeline(self):
        # Explicitly use the legacy echo handler to exercise the simple
        # text->reply path (the M1.3 default is now the intent router, tested
        # separately in test_intent_router.py).
        audio = _FakeAudio(pcm=b"speech")
        stt = _FakeSTT("hello there")
        tts = _FakeTTS()
        loop = vl.VoiceLoop(
            audio_manager=audio,
            stt=stt,
            tts=tts,
            response_handler=vl.echo_response_handler,
        )
        reply = loop.run_once(wait_for_wake=False)
        assert reply == "You said: hello there"
        assert tts.spoken == ["You said: hello there"]
        assert audio.played == [b"WAVDATA"]
        assert loop.state is vl.VoiceState.IDLE

    def test_run_once_no_speech_returns_none(self):
        audio = _FakeAudio(pcm=b"")  # nothing captured
        loop = vl.VoiceLoop(audio_manager=audio, stt=_FakeSTT(), tts=_FakeTTS())
        assert loop.run_once(wait_for_wake=False) is None
        assert loop.state is vl.VoiceState.IDLE

    def test_custom_response_handler(self):
        audio = _FakeAudio(pcm=b"speech")
        tts = _FakeTTS()
        loop = vl.VoiceLoop(
            audio_manager=audio,
            stt=_FakeSTT("ping"),
            tts=tts,
            response_handler=lambda t: f"echo:{t}",
        )
        assert loop.run_once(wait_for_wake=False) == "echo:ping"

    def test_stt_failure_returns_none(self):
        class _BadSTT:
            def transcribe_pcm(self, pcm, sample_rate=16000):
                raise stt_mod.STTError("boom")

        audio = _FakeAudio(pcm=b"speech")
        loop = vl.VoiceLoop(audio_manager=audio, stt=_BadSTT(), tts=_FakeTTS())
        assert loop.run_once(wait_for_wake=False) is None
        assert loop.state is vl.VoiceState.IDLE

    def test_tts_failure_does_not_crash(self):
        class _BadTTS:
            def synthesize(self, text):
                raise tts_mod.TTSError("boom")

        audio = _FakeAudio(pcm=b"speech")
        loop = vl.VoiceLoop(
            audio_manager=audio,
            stt=_FakeSTT("hi"),
            tts=_BadTTS(),
            response_handler=vl.echo_response_handler,
        )
        # Reply is still returned even though speaking failed.
        assert loop.run_once(wait_for_wake=False) == "You said: hi"

    def test_stop_sets_flag(self):
        loop = vl.VoiceLoop(audio_manager=_FakeAudio())
        loop.stop()
        assert loop._stop_requested is True

    def test_shutdown_closes_audio(self):
        audio = _FakeAudio()
        loop = vl.VoiceLoop(audio_manager=audio)
        loop.shutdown()
        assert audio.closed is True

    def test_echo_handler_empty(self):
        assert "didn't catch" in vl.echo_response_handler("")

    # -- pause / resume (Fix #4: accessibility for mic-less users) ---------

    def test_initial_not_paused(self):
        loop = vl.VoiceLoop(audio_manager=_FakeAudio())
        assert loop.is_paused is False

    def test_pause_sets_paused_state(self):
        loop = vl.VoiceLoop(audio_manager=_FakeAudio())
        assert loop.pause() is True
        assert loop.is_paused is True
        assert loop.state is vl.VoiceState.PAUSED

    def test_resume_clears_paused_state(self):
        loop = vl.VoiceLoop(audio_manager=_FakeAudio())
        loop.pause()
        assert loop.resume() is False
        assert loop.is_paused is False
        assert loop.state is vl.VoiceState.IDLE

    def test_pause_is_idempotent(self):
        loop = vl.VoiceLoop(audio_manager=_FakeAudio())
        loop.pause()
        # Second pause should keep it paused without error.
        assert loop.pause() is True
        assert loop.is_paused is True

    def test_resume_when_not_paused_is_noop(self):
        loop = vl.VoiceLoop(audio_manager=_FakeAudio())
        assert loop.resume() is False
        assert loop.is_paused is False

    def test_toggle_pause_flips_state(self):
        loop = vl.VoiceLoop(audio_manager=_FakeAudio())
        assert loop.toggle_pause() is True   # now paused
        assert loop.is_paused is True
        assert loop.toggle_pause() is False  # now listening
        assert loop.is_paused is False

    def test_paused_state_is_distinct_value(self):
        # PAUSED must be its own VoiceState so the UI can show a paused look.
        assert vl.VoiceState.PAUSED.value == "paused"
        assert vl.VoiceState.PAUSED is not vl.VoiceState.IDLE

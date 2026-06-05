"""Tests for the M2.3 SystemOptimizer.

Pure-logic tests over fake profiler/hardware snapshots -- no I/O. We assert the
performance tiering and each derived setting (audio backend, Whisper size, TTS
quality, wake-word sensitivity, history limit) for low/medium/high machines.

Run with:  pytest -q tests/test_system_optimizer.py
"""

from __future__ import annotations

import pytest

from mimosa.system.hardware_detector import (
    AudioInfo,
    CPUInfo,
    HardwareProfile,
    MemoryInfo,
)
from mimosa.system.system_optimizer import SystemOptimizer
from mimosa.system.system_profiler import SystemProfile


class FakeProfiler:
    def __init__(self, profile=None):
        self._profile = profile or SystemProfile()

    @property
    def profile(self):
        return self._profile


class FakeHardware:
    def __init__(self, profile):
        self._profile = profile

    @property
    def profile(self):
        return self._profile


def make_hw(cores=4, ram_gb=8.0, audio="PipeWire", mics=("mic0",)):
    return HardwareProfile(
        cpu=CPUInfo(logical_cores=cores, physical_cores=cores),
        memory=MemoryInfo(total_bytes=int(ram_gb * 1024 ** 3), available_bytes=int(ram_gb * 1024 ** 3 / 2)),
        audio=AudioInfo(backend=audio, server_running=bool(audio)),
        microphones=list(mics),
    )


def optimizer_for(hw):
    return SystemOptimizer(profiler=FakeProfiler(), hardware=FakeHardware(hw))


class TestPerformanceTier:
    def test_high_tier(self):
        cfg = optimizer_for(make_hw(cores=8, ram_gb=16)).optimize()
        assert cfg.performance_tier == "high"
        assert cfg.whisper_model == "small"
        assert cfg.tts_quality == "high"

    def test_low_tier(self):
        cfg = optimizer_for(make_hw(cores=2, ram_gb=4)).optimize()
        assert cfg.performance_tier == "low"
        assert cfg.whisper_model == "tiny"
        assert cfg.tts_quality == "low"

    def test_medium_tier(self):
        cfg = optimizer_for(make_hw(cores=4, ram_gb=8)).optimize()
        assert cfg.performance_tier == "medium"
        assert cfg.whisper_model == "base"
        assert cfg.tts_quality == "standard"

    def test_low_when_ram_starved_even_with_cores(self):
        cfg = optimizer_for(make_hw(cores=16, ram_gb=3)).optimize()
        assert cfg.performance_tier == "low"


class TestAudioBackendSelection:
    @pytest.mark.parametrize("backend,expected", [
        ("PipeWire", "pipewire"),
        ("PulseAudio", "pulseaudio"),
        ("ALSA", "alsa"),
        (None, None),
    ])
    def test_backend_mapping(self, backend, expected):
        cfg = optimizer_for(make_hw(audio=backend)).optimize()
        assert cfg.audio_backend == expected


class TestWakeWordSensitivity:
    def test_higher_on_low_end(self):
        low = optimizer_for(make_hw(cores=2, ram_gb=4)).optimize()
        high = optimizer_for(make_hw(cores=8, ram_gb=16)).optimize()
        assert low.wake_word_sensitivity > high.wake_word_sensitivity

    def test_bumped_when_no_mic(self):
        with_mic = optimizer_for(make_hw(cores=4, ram_gb=8, mics=("mic0",))).optimize()
        no_mic = optimizer_for(make_hw(cores=4, ram_gb=8, mics=())).optimize()
        assert no_mic.wake_word_sensitivity > with_mic.wake_word_sensitivity


class TestHistoryLimit:
    @pytest.mark.parametrize("ram_gb,expected", [
        (64, 40), (16, 25), (8, 15), (4, 8), (2, 5),
    ])
    def test_history_scales_with_ram(self, ram_gb, expected):
        cfg = optimizer_for(make_hw(cores=4, ram_gb=ram_gb)).optimize()
        assert cfg.max_history_turns == expected


class TestConfigShape:
    def test_cached_config(self):
        opt = optimizer_for(make_hw())
        assert opt.config is opt.config

    def test_notes_present(self):
        cfg = optimizer_for(make_hw()).optimize()
        assert "performance" in cfg.notes
        assert "stt" in cfg.notes

    def test_as_dict(self):
        cfg = optimizer_for(make_hw()).optimize()
        d = cfg.as_dict()
        assert d["whisper_model"]
        assert "wake_word_sensitivity" in d

    def test_missing_facts_defaults_low(self):
        # Empty hardware profile -> conservative low tier, no crash.
        cfg = SystemOptimizer(profiler=FakeProfiler(), hardware=FakeHardware(HardwareProfile())).optimize()
        assert cfg.performance_tier == "low"
        assert cfg.audio_backend is None

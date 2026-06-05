"""Tests for the M2.3 SystemInfoSkill and its router integration.

The skill is driven by fake profiler/hardware/optimizer objects so the spoken
answers are deterministic regardless of the host. We also assert that the
intent router classifies system-information queries to ``system_info`` (and not
to system_control, application, or the LLM question skill).

Run with:  pytest -q tests/test_system_info.py
"""

from __future__ import annotations

import pytest

from mimosa.skills.system_info import SystemInfoSkill
from mimosa.system.hardware_detector import (
    AudioInfo,
    CPUInfo,
    DisplayInfo,
    GPUInfo,
    HardwareProfile,
    MemoryInfo,
)
from mimosa.system.system_optimizer import SystemOptimizer
from mimosa.system.system_profiler import SystemProfile
from mimosa.core.intent_router import IntentRouter, INTENT_SYSTEM_INFO, INTENT_SYSTEM


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class FakeProfiler:
    def __init__(self, profile):
        self._profile = profile

    @property
    def profile(self):
        return self._profile

    def refresh(self):
        return self._profile


class FakeHardware:
    def __init__(self, profile):
        self._profile = profile

    @property
    def profile(self):
        return self._profile

    def refresh(self):
        return self._profile


def kubuntu_profile():
    return SystemProfile(
        distro_id="ubuntu",
        distro_name="Ubuntu 26.04 LTS",
        distro_version="26.04",
        desktop_environment="KDE",
        display_server="wayland",
        plasma_version="6.0.4",
        architecture="x86_64",
        kernel="6.11.0-generic",
        python_version="3.12.0",
        is_kubuntu=True,
        is_kde=True,
    )


def rich_hardware():
    return HardwareProfile(
        cpu=CPUInfo(model="Intel Core i7-1260P", physical_cores=12, logical_cores=16, max_frequency_mhz=4700.0),
        memory=MemoryInfo(total_bytes=16 * 1024 ** 3, available_bytes=8 * 1024 ** 3),
        gpus=[GPUInfo(vendor="Intel", description="Intel Iris Xe Graphics")],
        displays=[DisplayInfo(name="eDP-1", resolution="1920x1080", primary=True)],
        audio=AudioInfo(backend="PipeWire", server_running=True, sinks=1, sources=2),
        microphones=["Built-in Microphone"],
    )


def make_skill(profile=None, hw=None):
    profile = profile or kubuntu_profile()
    hw = hw or rich_hardware()
    prof = FakeProfiler(profile)
    hard = FakeHardware(hw)
    opt = SystemOptimizer(profiler=prof, hardware=hard)
    return SystemInfoSkill(profiler=prof, hardware=hard, optimizer=opt)


# ---------------------------------------------------------------------------
# Skill answers
# ---------------------------------------------------------------------------

class TestAnswers:
    def test_desktop(self):
        res = make_skill().handle("what desktop am I using?")
        assert res.success
        assert "KDE" in res.text

    def test_display_server(self):
        res = make_skill().handle("is this wayland or x11?")
        assert "Wayland" in res.text

    def test_plasma_version(self):
        res = make_skill().handle("what version of plasma am I on?")
        assert "6.0.4" in res.text

    def test_distro(self):
        res = make_skill().handle("what operating system is this?")
        assert "Ubuntu 26.04 LTS" in res.text
        assert "Kubuntu" in res.text

    def test_audio_backend(self):
        res = make_skill().handle("what audio backend am I using?")
        assert "PipeWire" in res.text

    def test_microphone(self):
        res = make_skill().handle("do I have a microphone?")
        assert "Built-in Microphone" in res.text

    def test_memory(self):
        res = make_skill().handle("how much RAM do I have?")
        assert "16" in res.text

    def test_cpu(self):
        res = make_skill().handle("what CPU do I have?")
        assert "Intel Core i7-1260P" in res.text
        assert "16 threads" in res.text

    def test_gpu(self):
        res = make_skill().handle("what graphics card do I have?")
        assert "Iris Xe" in res.text

    def test_displays(self):
        res = make_skill().handle("what's my screen resolution?")
        assert "1920x1080" in res.text

    def test_kernel(self):
        res = make_skill().handle("what kernel and architecture?")
        assert "6.11.0-generic" in res.text
        assert "x86_64" in res.text

    def test_full_specs(self):
        res = make_skill().handle("show me my system specs")
        assert res.success
        assert "Ubuntu" in res.text

    def test_recommendation(self):
        res = make_skill().handle("what settings do you recommend for this machine?")
        assert res.success
        assert "recommend" in res.text.lower()

    def test_empty_input(self):
        res = make_skill().handle("")
        assert not res.success

    def test_uses_llm_false(self):
        assert make_skill().uses_llm is False


# ---------------------------------------------------------------------------
# Graceful degradation (unknown / empty facts)
# ---------------------------------------------------------------------------

class TestDegradation:
    def test_unknown_desktop(self):
        res = make_skill(profile=SystemProfile()).handle("what desktop am I using?")
        assert not res.success
        assert "couldn't" in res.text.lower()

    def test_no_audio_backend(self):
        res = make_skill(hw=HardwareProfile()).handle("what audio backend am I using?")
        assert not res.success

    def test_no_microphone(self):
        res = make_skill(hw=HardwareProfile()).handle("do I have a microphone?")
        assert res.success  # honest "no mic" is still a successful answer
        assert "didn't detect" in res.text.lower() or "no" in res.text.lower()

    def test_non_kde_plasma_query(self):
        prof = SystemProfile(desktop_environment="GNOME", is_kde=False)
        res = make_skill(profile=prof).handle("what plasma version?")
        assert res.success
        assert "isn't a KDE" in res.text or "no Plasma" in res.text


# ---------------------------------------------------------------------------
# Router integration
# ---------------------------------------------------------------------------

class TestRouterClassification:
    @pytest.fixture(scope="class")
    def router(self):
        return IntentRouter()  # no LLM; pure heuristics

    @pytest.mark.parametrize("text", [
        "what desktop am I using?",
        "what desktop environment is this",
        "is this wayland or x11?",
        "what version of plasma am I running?",
        "show me my system specs",
        "what are my system specifications",
        "what audio backend am I using?",
        "how much RAM do I have?",
        "how many CPU cores do I have?",
        "what graphics card is in this machine?",
        "do I have a microphone?",
        "what operating system is this?",
        "what version of kubuntu am I on?",
        "tell me about this computer",
        "what settings do you recommend for this system?",
    ])
    def test_routes_to_system_info(self, router, text):
        assert router.classify(text).intent == INTENT_SYSTEM_INFO

    @pytest.mark.parametrize("text", [
        "turn the volume up",
        "set brightness to 50 percent",
        "mute the sound",
        "is my wifi on?",
        "how much battery do I have left?",
    ])
    def test_control_still_routes_to_system_control(self, router, text):
        # System-control commands must NOT be swallowed by system_info.
        assert router.classify(text).intent == INTENT_SYSTEM

    def test_full_route_returns_result(self, router):
        result = router.route("what desktop environment am I using?")
        assert result.skill == "system_info"
        assert result.text

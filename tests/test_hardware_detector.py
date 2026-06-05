"""Tests for the M2.3 HardwareDetector.

Fully hermetic: a fake ``psutil`` module, a scripted command runner, an
injectable ``which``, and ``/proc`` & ``/sys`` roots redirected into pytest
``tmp_path`` directories. Nothing touches the real machine.

Run with:  pytest -q tests/test_hardware_detector.py
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mimosa.system.hardware_detector import (
    HardwareDetector,
    RunOutput,
)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class FakePsutil:
    def __init__(self, logical=8, physical=4, total=16 * 1024 ** 3, available=8 * 1024 ** 3, freq_max=3600.0):
        self._logical = logical
        self._physical = physical
        self._total = total
        self._available = available
        self._freq_max = freq_max

    def cpu_count(self, logical=True):
        return self._logical if logical else self._physical

    def cpu_freq(self):
        class _F:
            max = self._freq_max
        return _F()

    def virtual_memory(self):
        class _M:
            total = self._total
            available = self._available
        return _M()


class FakeShell:
    def __init__(self, responses=None, available=()):
        self.responses = responses or {}
        self.available = set(available)
        self.calls = []

    def run(self, argv):
        self.calls.append(list(argv))
        joined = " ".join(argv)
        for key, out in self.responses.items():
            if key in joined:
                return out
        return RunOutput(0, "", "")

    def which(self, tool):
        return f"/usr/bin/{tool}" if tool in self.available else None


def make_detector(tmp_path, *, psutil_module="missing", responses=None, available=(),
                  cpuinfo=None, meminfo=None, drm=None, environ=None):
    proc = tmp_path / "proc"
    sysd = tmp_path / "sys"
    proc.mkdir(exist_ok=True)
    sysd.mkdir(exist_ok=True)
    if cpuinfo is not None:
        (proc / "cpuinfo").write_text(cpuinfo)
    if meminfo is not None:
        (proc / "meminfo").write_text(meminfo)
    if drm is not None:
        drm_root = sysd / "class" / "drm"
        drm_root.mkdir(parents=True, exist_ok=True)
        for name, spec in drm.items():
            d = drm_root / name
            d.mkdir(parents=True, exist_ok=True)
            if "status" in spec:
                (d / "status").write_text(spec["status"])
            if "vendor" in spec:
                dev = d / "device"
                dev.mkdir(exist_ok=True)
                (dev / "vendor").write_text(spec["vendor"])

    shell = FakeShell(responses=responses, available=available)
    pm = None if psutil_module == "missing" else psutil_module
    det = HardwareDetector(
        psutil_module=pm,
        runner=shell.run,
        which=shell.which,
        proc_root=str(proc),
        sys_root=str(sysd),
        environ=environ or {},
    )
    return det, shell


# ---------------------------------------------------------------------------
# CPU
# ---------------------------------------------------------------------------

class TestCPU:
    def test_cpu_from_psutil_and_proc(self, tmp_path):
        det, _ = make_detector(
            tmp_path,
            psutil_module=FakePsutil(logical=8, physical=4),
            cpuinfo="processor\t: 0\nmodel name\t: Test CPU 9000\n",
        )
        cpu = det.detect_cpu()
        assert cpu.logical_cores == 8
        assert cpu.physical_cores == 4
        assert cpu.model == "Test CPU 9000"
        assert cpu.max_frequency_mhz == 3600.0

    def test_cpu_without_psutil_falls_back(self, tmp_path):
        det, _ = make_detector(tmp_path, cpuinfo="model name\t: Fallback CPU\n")
        cpu = det.detect_cpu()
        assert cpu.model == "Fallback CPU"
        # logical cores still discovered via os.cpu_count()
        assert cpu.logical_cores is not None

    def test_cpu_model_missing_is_none(self, tmp_path):
        det, _ = make_detector(tmp_path, psutil_module=FakePsutil())
        cpu = det.detect_cpu()
        assert cpu.model is None


# ---------------------------------------------------------------------------
# Memory
# ---------------------------------------------------------------------------

class TestMemory:
    def test_memory_from_psutil(self, tmp_path):
        det, _ = make_detector(tmp_path, psutil_module=FakePsutil(total=16 * 1024 ** 3))
        mem = det.detect_memory()
        assert mem.total_gb == 16.0

    def test_memory_from_proc_meminfo(self, tmp_path):
        det, _ = make_detector(
            tmp_path,
            meminfo="MemTotal:       8000000 kB\nMemAvailable:    4000000 kB\n",
        )
        mem = det.detect_memory()
        assert mem.total_bytes == 8000000 * 1024
        assert mem.available_bytes == 4000000 * 1024

    def test_memory_absent(self, tmp_path):
        det, _ = make_detector(tmp_path)
        mem = det.detect_memory()
        assert mem.total_gb is None


# ---------------------------------------------------------------------------
# GPU
# ---------------------------------------------------------------------------

class TestGPU:
    def test_gpu_from_lspci(self, tmp_path):
        lspci = (
            "00:02.0 VGA compatible controller: Intel Corporation UHD Graphics\n"
            "01:00.0 3D controller: NVIDIA Corporation GA107M\n"
        )
        det, _ = make_detector(
            tmp_path, responses={"lspci": RunOutput(0, lspci)}, available=("lspci",)
        )
        gpus = det.detect_gpus()
        vendors = {g.vendor for g in gpus}
        assert "Intel" in vendors
        assert "NVIDIA" in vendors

    def test_gpu_from_sysfs_fallback(self, tmp_path):
        det, _ = make_detector(
            tmp_path,
            drm={"card0": {"vendor": "0x10de\n", "status": "connected"}},
        )
        gpus = det.detect_gpus()
        assert len(gpus) == 1
        assert gpus[0].vendor == "NVIDIA"

    def test_no_gpu(self, tmp_path):
        det, _ = make_detector(tmp_path)
        assert det.detect_gpus() == []


# ---------------------------------------------------------------------------
# Displays
# ---------------------------------------------------------------------------

class TestDisplays:
    def test_displays_from_xrandr(self, tmp_path):
        xrandr = (
            "Screen 0: minimum 320 x 200, current 3840 x 1080\n"
            "eDP-1 connected primary 1920x1080+0+0 (normal left inverted right) 309mm x 174mm\n"
            "   1920x1080     60.00*+\n"
            "HDMI-1 connected 1920x1080+1920+0 (normal) 510mm x 290mm\n"
            "   1920x1080     60.00*\n"
        )
        det, _ = make_detector(
            tmp_path, responses={"xrandr": RunOutput(0, xrandr)}, available=("xrandr",)
        )
        displays = det.detect_displays()
        assert len(displays) == 2
        assert displays[0].primary is True
        assert displays[0].resolution == "1920x1080"

    def test_displays_from_sysfs_fallback(self, tmp_path):
        det, _ = make_detector(
            tmp_path,
            drm={
                "card0-eDP-1": {"status": "connected"},
                "card0-HDMI-1": {"status": "disconnected"},
            },
        )
        displays = det.detect_displays()
        names = {d.name for d in displays}
        assert "card0-eDP-1" in names
        assert "card0-HDMI-1" not in names

    def test_multi_monitor_flag(self, tmp_path):
        xrandr = (
            "eDP-1 connected primary 1920x1080+0+0\n   1920x1080 60.0*+\n"
            "DP-1 connected 2560x1440+1920+0\n   2560x1440 60.0*\n"
        )
        det, _ = make_detector(tmp_path, responses={"xrandr": RunOutput(0, xrandr)}, available=("xrandr",))
        assert det.profile.multi_monitor is True


# ---------------------------------------------------------------------------
# Audio
# ---------------------------------------------------------------------------

class TestAudio:
    def test_pipewire_detected(self, tmp_path):
        det, _ = make_detector(
            tmp_path,
            available=("wpctl", "pactl", "pw-cli"),
            responses={
                "pactl info": RunOutput(0, "Server Name: PulseAudio (on PipeWire)\n"),
                "short sinks": RunOutput(0, "0\talsa_output\n"),
                "short sources": RunOutput(0, "0\talsa_input\n1\talsa_output.monitor\n"),
                "pw-cli info": RunOutput(0, "id 0"),
            },
        )
        audio = det.detect_audio()
        assert audio.backend == "PipeWire"
        assert audio.server_running is True

    def test_pulseaudio_detected(self, tmp_path):
        det, _ = make_detector(
            tmp_path,
            available=("pactl",),
            responses={"pactl info": RunOutput(0, "Server Name: pulseaudio\n")},
        )
        audio = det.detect_audio()
        assert audio.backend == "PulseAudio"

    def test_alsa_fallback(self, tmp_path):
        det, _ = make_detector(tmp_path, available=("aplay",))
        audio = det.detect_audio()
        assert audio.backend == "ALSA"

    def test_no_audio_backend(self, tmp_path):
        det, _ = make_detector(tmp_path)
        audio = det.detect_audio()
        assert audio.backend is None


# ---------------------------------------------------------------------------
# Microphones
# ---------------------------------------------------------------------------

class TestMicrophones:
    def test_mics_from_pactl_excludes_monitor(self, tmp_path):
        det, _ = make_detector(
            tmp_path,
            available=("pactl",),
            responses={
                "short sources": RunOutput(
                    0,
                    "0\talsa_input.pci-0000_00.analog-stereo\tmodule\n"
                    "1\talsa_output.pci.monitor\tmodule\n",
                )
            },
        )
        mics = det.detect_microphones()
        assert mics == ["alsa_input.pci-0000_00.analog-stereo"]

    def test_mics_from_arecord(self, tmp_path):
        det, _ = make_detector(
            tmp_path,
            available=("arecord",),
            responses={"arecord": RunOutput(0, "card 0: PCH [HDA Intel PCH], device 0: ALC [Mic]\n")},
        )
        mics = det.detect_microphones()
        assert mics == ["HDA Intel PCH"]

    def test_no_microphone(self, tmp_path):
        det, _ = make_detector(tmp_path)
        assert det.detect_microphones() == []
        assert det.profile.has_microphone is False


# ---------------------------------------------------------------------------
# Caching & summary
# ---------------------------------------------------------------------------

class TestProfileAggregate:
    def test_profile_cached_and_refresh(self, tmp_path):
        det, _ = make_detector(tmp_path, psutil_module=FakePsutil())
        first = det.profile
        assert det.profile is first
        assert det.refresh() is not first

    def test_summary_string(self, tmp_path):
        det, _ = make_detector(tmp_path, psutil_module=FakePsutil(logical=8, total=16 * 1024 ** 3))
        summary = det.profile.summary()
        assert "GB RAM" in summary

    def test_as_dict_has_derived_fields(self, tmp_path):
        det, _ = make_detector(tmp_path, psutil_module=FakePsutil())
        d = det.profile.as_dict()
        assert "multi_monitor" in d
        assert "has_microphone" in d
        assert d["memory"]["total_gb"] is not None

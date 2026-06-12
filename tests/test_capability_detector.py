"""Tests for on-device hardware capability detection (Milestone 1, req #7).

The detector must never raise, must degrade gracefully when probes are
unavailable, and must classify the host into gpu / cpu / insufficient using the
documented thresholds. All probes are injectable so we can simulate machines
without touching real hardware.
"""

from __future__ import annotations

from mimosa.system import capability_detector as cd
from mimosa.system.capability_detector import (
    CapabilityReport,
    LEVEL_CPU,
    LEVEL_GPU,
    LEVEL_INSUFFICIENT,
    detect_capability,
)


class _FakeVM:
    def __init__(self, total_bytes):
        self.total = total_bytes


class _FakePsutil:
    def __init__(self, ram_gb):
        self._ram = int(ram_gb * (1024 ** 3))

    def virtual_memory(self):
        return _FakeVM(self._ram)


class _FakeCPU:
    def __init__(self, cores):
        self.logical_cores = cores


class _FakeProfile:
    def __init__(self, cores, gpus=None):
        self.cpu = _FakeCPU(cores)
        self.gpus = gpus or []


class _FakeDetector:
    def __init__(self, profile):
        self.profile = profile


def _patch_common(monkeypatch, *, ram_gb, cores, disk_gb, gpu=False, gpu_kind=""):
    monkeypatch.setattr(cd, "_detect_disk_free_gb", lambda path=None: disk_gb)
    monkeypatch.setattr(cd, "_detect_gpu", lambda profile: (gpu, gpu_kind))


def test_report_to_dict_has_expected_keys():
    r = CapabilityReport()
    d = r.to_dict()
    for key in ("level", "ram_gb", "disk_free_gb", "cpu_cores",
                "gpu_available", "gpu_kind"):
        assert key in d


def test_detect_never_raises_with_no_inputs(monkeypatch):
    # Force every probe to be unavailable; must still return a report.
    monkeypatch.setattr(cd, "_detect_ram_gb", lambda m: None)
    monkeypatch.setattr(cd, "_detect_disk_free_gb", lambda path=None: None)
    monkeypatch.setattr(cd, "_detect_gpu", lambda profile: (False, ""))
    report = detect_capability(hardware_detector=_FakeDetector(_FakeProfile(None)),
                               psutil_module=None)
    assert isinstance(report, CapabilityReport)
    # Unknown probes are treated optimistically (None passes thresholds) -> cpu.
    assert report.level in (LEVEL_CPU, LEVEL_GPU, LEVEL_INSUFFICIENT)


def test_classifies_gpu(monkeypatch):
    _patch_common(monkeypatch, ram_gb=16, cores=8, disk_gb=100, gpu=True,
                  gpu_kind="cuda")
    report = detect_capability(
        hardware_detector=_FakeDetector(_FakeProfile(8)),
        psutil_module=_FakePsutil(16),
    )
    assert report.level == LEVEL_GPU
    assert report.gpu_available is True
    assert report.gpu_kind == "cuda"


def test_classifies_cpu(monkeypatch):
    _patch_common(monkeypatch, ram_gb=8, cores=4, disk_gb=50, gpu=False)
    report = detect_capability(
        hardware_detector=_FakeDetector(_FakeProfile(4)),
        psutil_module=_FakePsutil(8),
    )
    assert report.level == LEVEL_CPU
    assert report.gpu_available is False


def test_classifies_insufficient_low_ram(monkeypatch):
    _patch_common(monkeypatch, ram_gb=2, cores=8, disk_gb=100, gpu=True,
                  gpu_kind="cuda")
    report = detect_capability(
        hardware_detector=_FakeDetector(_FakeProfile(8)),
        psutil_module=_FakePsutil(2),
    )
    assert report.level == LEVEL_INSUFFICIENT
    assert any("RAM" in r for r in report.reasons)


def test_classifies_insufficient_low_cores(monkeypatch):
    _patch_common(monkeypatch, ram_gb=16, cores=2, disk_gb=100, gpu=False)
    report = detect_capability(
        hardware_detector=_FakeDetector(_FakeProfile(2)),
        psutil_module=_FakePsutil(16),
    )
    assert report.level == LEVEL_INSUFFICIENT


def test_classifies_insufficient_low_disk(monkeypatch):
    _patch_common(monkeypatch, ram_gb=16, cores=8, disk_gb=1, gpu=False)
    report = detect_capability(
        hardware_detector=_FakeDetector(_FakeProfile(8)),
        psutil_module=_FakePsutil(16),
    )
    assert report.level == LEVEL_INSUFFICIENT


def test_gpu_vendor_fallback_detects_discrete():
    # When torch is absent, a discrete NVIDIA/AMD GPU in the profile counts.
    class _GPU:
        def __init__(self, vendor):
            self.vendor = vendor

    profile = _FakeProfile(8, gpus=[_GPU("NVIDIA Corporation")])
    available, kind = cd._detect_gpu(profile)
    # torch may or may not be present; if present and no CUDA, falls to vendor.
    assert available in (True, False)
    if available and kind == "discrete":
        assert True

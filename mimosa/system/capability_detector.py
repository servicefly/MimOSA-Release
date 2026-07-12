"""Hardware capability detection for on-device training (Milestone 1, req #7).

A future milestone lets users *train their own wake word* on-device. Training is
far heavier than inference, so before we expose that feature we need to know
whether the machine can realistically handle it. This module answers a single
question -- *"how capable is this computer for training?"* -- and returns one of
three levels:

* ``"gpu"``         -- a capable GPU (CUDA/MPS) plus enough RAM/disk/CPU; the
                       fast path for training.
* ``"cpu"``         -- no usable GPU, but enough RAM/disk/CPU to train slowly on
                       the CPU.
* ``"insufficient"`` -- not enough RAM, disk, or CPU cores to train comfortably.

Design principles (consistent with the rest of :mod:`mimosa.system`):

* **Local & private.** Reads come from :mod:`psutil`, :mod:`shutil`, the
  existing :class:`~mimosa.system.hardware_detector.HardwareDetector`, and an
  optional :mod:`torch` probe. Nothing is sent anywhere.
* **Graceful degradation.** Every probe is wrapped so a missing dependency or
  permission error never raises -- we fall back to conservative values.
* **Silent.** Results are *logged* (debug/info) and stored in config for later
  use; no UI is shown yet (per the milestone spec).
"""

from __future__ import annotations

import logging
import os
import shutil
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger("mimosa.system.capability_detector")

# -- Minimum requirements for on-device training ----------------------------
MIN_TRAINING_RAM_GB = 4.0
MIN_TRAINING_DISK_GB = 5.0
MIN_TRAINING_CPU_CORES = 4

#: Capability levels (stable strings; stored in config).
LEVEL_GPU = "gpu"
LEVEL_CPU = "cpu"
LEVEL_INSUFFICIENT = "insufficient"

# -- Avatar rendering tiers (Milestone 8.1) ---------------------------------
#
# The v2.0.0 avatar system ships in tiers. v2.0.0-alpha implements only the
# universal 2D sprite baseline (which runs everywhere) and the legacy circle
# fallback for genuinely constrained machines. The richer ``"live2d"`` (2.5D)
# and ``"3d"`` tiers are reserved here so the detector's public contract is
# stable for v2.1.0+ without another API change.
TIER_3D = "3d"          # reserved for v2.1.0+: high-end GPU character models
TIER_LIVE2D = "live2d"  # reserved for v2.1.0+: mid-tier 2.5D rigged avatars
TIER_2D = "2d"          # universal animated 2D sprite baseline (this release)
TIER_CIRCLE = "circle_only"  # fall back to the classic listening circle

#: Tiers this build can actually render. Detection never returns a reserved
#: tier until its renderer lands, so callers can trust the value.
SUPPORTED_AVATAR_TIERS = (TIER_2D, TIER_CIRCLE)

#: All tier identifiers, including reserved-for-future ones (stable strings).
VALID_AVATAR_TIERS = (TIER_3D, TIER_LIVE2D, TIER_2D, TIER_CIRCLE)

# Minimum resources for the universal 2D sprite avatar. These are far lighter
# than *training* requirements -- the 2D renderer is a modest Cairo/GTK draw
# loop, so almost any desktop that can show the circle can show a 2D sprite.
# Below these we fall back to the classic circle to guarantee smooth UI.
MIN_AVATAR_2D_RAM_GB = 2.0
MIN_AVATAR_2D_CPU_CORES = 2


@dataclass
class CapabilityReport:
    """The outcome of a capability scan.

    Attributes:
        level: One of ``"gpu"`` / ``"cpu"`` / ``"insufficient"``.
        ram_gb: Total system RAM in GB (``None`` if unknown).
        disk_free_gb: Free disk space (GB) on the data/home volume.
        cpu_cores: Logical CPU cores (``None`` if unknown).
        gpu_available: Whether a usable training GPU (CUDA/MPS/discrete) exists.
        gpu_kind: Short label for the GPU path ("cuda"/"mps"/"discrete"/"").
        reasons: Human-readable notes explaining the verdict.
    """

    level: str = LEVEL_INSUFFICIENT
    ram_gb: Optional[float] = None
    disk_free_gb: Optional[float] = None
    cpu_cores: Optional[int] = None
    gpu_available: bool = False
    gpu_kind: str = ""
    reasons: Optional[list] = None

    def __post_init__(self) -> None:
        if self.reasons is None:
            self.reasons = []

    def to_dict(self) -> dict:
        return {
            "level": self.level,
            "ram_gb": self.ram_gb,
            "disk_free_gb": self.disk_free_gb,
            "cpu_cores": self.cpu_cores,
            "gpu_available": self.gpu_available,
            "gpu_kind": self.gpu_kind,
        }


def _detect_ram_gb(psutil_module) -> Optional[float]:
    try:
        if psutil_module is not None:
            return round(psutil_module.virtual_memory().total / (1024 ** 3), 2)
    except Exception:  # pragma: no cover - defensive
        logger.debug("RAM probe failed", exc_info=True)
    return None


def _detect_disk_free_gb(path: Optional[str] = None) -> Optional[float]:
    """Free space (GB) on the volume that will hold training artifacts."""
    candidate = path or os.path.expanduser("~/.local/share/mimosa")
    # Walk up to the first existing parent so usage works pre-install.
    probe = candidate
    while probe and not os.path.exists(probe):
        parent = os.path.dirname(probe)
        if parent == probe:
            break
        probe = parent
    try:
        usage = shutil.disk_usage(probe or "/")
        return round(usage.free / (1024 ** 3), 2)
    except Exception:  # pragma: no cover - defensive
        logger.debug("Disk probe failed", exc_info=True)
    return None


def _detect_gpu(hardware_profile) -> tuple:
    """Return ``(gpu_available, gpu_kind)`` using torch then the HW profile.

    Prefers a :mod:`torch` probe (authoritative for CUDA/MPS training), and
    falls back to the detected discrete-GPU vendor list when torch is absent.
    """
    # 1) torch is the source of truth for trainable accelerators.
    try:
        import torch  # type: ignore

        if torch.cuda.is_available():
            return True, "cuda"
        mps = getattr(getattr(torch, "backends", None), "mps", None)
        if mps is not None and mps.is_available():
            return True, "mps"
    except Exception:
        logger.debug("torch GPU probe unavailable", exc_info=True)

    # 2) Fall back to detected discrete GPUs (NVIDIA/AMD => likely trainable).
    try:
        for gpu in getattr(hardware_profile, "gpus", []) or []:
            vendor = (getattr(gpu, "vendor", "") or "").lower()
            if "nvidia" in vendor or "amd" in vendor or "advanced micro" in vendor:
                return True, "discrete"
    except Exception:  # pragma: no cover - defensive
        logger.debug("GPU vendor probe failed", exc_info=True)
    return False, ""


def detect_capability(
    *,
    hardware_detector=None,
    psutil_module: object = "auto",
    disk_path: Optional[str] = None,
) -> CapabilityReport:
    """Scan the host and return a :class:`CapabilityReport`.

    Args:
        hardware_detector: Optional
            :class:`~mimosa.system.hardware_detector.HardwareDetector` (built
            lazily if omitted). Injectable for tests.
        psutil_module: The :mod:`psutil` module (or a fake). ``"auto"`` imports
            the real one if available, else ``None``.
        disk_path: Volume to measure free space on (defaults to MimOSA's data
            dir / nearest existing parent).

    Returns:
        A :class:`CapabilityReport`. Never raises.
    """
    if psutil_module == "auto":
        try:
            import psutil  # type: ignore

            psutil_module = psutil
        except Exception:  # pragma: no cover - psutil is a dependency
            psutil_module = None

    # Hardware profile (CPU cores, GPUs) -- reuse the existing detector.
    profile = None
    try:
        if hardware_detector is None:
            from mimosa.system.hardware_detector import HardwareDetector

            hardware_detector = HardwareDetector(psutil_module=psutil_module)
        profile = hardware_detector.profile
    except Exception:  # pragma: no cover - defensive
        logger.debug("Hardware profile unavailable", exc_info=True)

    ram_gb = _detect_ram_gb(psutil_module)
    disk_free_gb = _detect_disk_free_gb(disk_path)
    cpu_cores = None
    if profile is not None:
        cpu_cores = getattr(profile.cpu, "logical_cores", None)
    if cpu_cores is None:
        try:
            cpu_cores = os.cpu_count()
        except Exception:  # pragma: no cover
            cpu_cores = None

    gpu_available, gpu_kind = _detect_gpu(profile)

    report = CapabilityReport(
        ram_gb=ram_gb,
        disk_free_gb=disk_free_gb,
        cpu_cores=cpu_cores,
        gpu_available=gpu_available,
        gpu_kind=gpu_kind,
    )

    # -- Verdict ----------------------------------------------------------
    reasons = report.reasons
    enough_ram = ram_gb is None or ram_gb >= MIN_TRAINING_RAM_GB
    enough_disk = disk_free_gb is None or disk_free_gb >= MIN_TRAINING_DISK_GB
    enough_cpu = cpu_cores is None or cpu_cores >= MIN_TRAINING_CPU_CORES

    if ram_gb is not None and ram_gb < MIN_TRAINING_RAM_GB:
        reasons.append(f"RAM {ram_gb} GB < {MIN_TRAINING_RAM_GB} GB needed")
    if disk_free_gb is not None and disk_free_gb < MIN_TRAINING_DISK_GB:
        reasons.append(
            f"Free disk {disk_free_gb} GB < {MIN_TRAINING_DISK_GB} GB needed"
        )
    if cpu_cores is not None and cpu_cores < MIN_TRAINING_CPU_CORES:
        reasons.append(f"{cpu_cores} CPU cores < {MIN_TRAINING_CPU_CORES} needed")

    if not (enough_ram and enough_disk and enough_cpu):
        report.level = LEVEL_INSUFFICIENT
    elif gpu_available:
        report.level = LEVEL_GPU
        reasons.append(f"GPU available ({gpu_kind})")
    else:
        report.level = LEVEL_CPU
        reasons.append("No training GPU; CPU training possible")

    logger.info(
        "Hardware capability: level=%s ram=%sGB disk_free=%sGB cores=%s gpu=%s(%s)",
        report.level, ram_gb, disk_free_gb, cpu_cores, gpu_available, gpu_kind,
    )
    return report


def detect_avatar_tier(report: Optional[CapabilityReport] = None, **kwargs) -> str:
    """Pick the best avatar rendering tier the host can drive smoothly.

    The v2.0.0 avatar system replaces the classic listening circle with an
    animated character. This function decides *which* renderer to use. For
    v2.0.0-alpha the only implemented tiers are the universal 2D sprite avatar
    and the classic circle fallback, so this returns one of:

    * ``"2d"``          -- the universal animated 2D sprite avatar (default on
                           essentially every modern desktop).
    * ``"circle_only"`` -- fall back to the legacy listening circle on machines
                           too constrained (very low RAM / single core) to
                           animate a sprite comfortably.

    The richer ``"3d"`` and ``"live2d"`` tiers are intentionally *not* returned
    yet -- their renderers land in v2.1.0+. The return value is guaranteed to be
    a member of :data:`SUPPORTED_AVATAR_TIERS` so callers never receive a tier
    they cannot render.

    Args:
        report: A pre-computed :class:`CapabilityReport`. When omitted, a fresh
            scan runs via :func:`detect_capability` (``kwargs`` are forwarded,
            which keeps the probes injectable for tests).

    Returns:
        ``"2d"`` or ``"circle_only"``. Never raises.
    """
    try:
        if report is None:
            report = detect_capability(**kwargs)

        ram_gb = getattr(report, "ram_gb", None)
        cpu_cores = getattr(report, "cpu_cores", None)

        # Unknown values (probe failed) are treated optimistically: the 2D
        # avatar is lightweight, so we only *drop* to the circle when we have
        # concrete evidence the machine is genuinely too small.
        enough_ram = ram_gb is None or ram_gb >= MIN_AVATAR_2D_RAM_GB
        enough_cpu = cpu_cores is None or cpu_cores >= MIN_AVATAR_2D_CPU_CORES

        tier = TIER_2D if (enough_ram and enough_cpu) else TIER_CIRCLE
    except Exception:  # pragma: no cover - defensive; never break the UI
        logger.debug("Avatar tier detection failed; defaulting to circle", exc_info=True)
        logger.info(
            "Avatar tier: %s (fallback \u2014 hardware probe failed)", TIER_CIRCLE
        )
        return TIER_CIRCLE

    if tier == TIER_CIRCLE:
        # Make the fallback explicit so users/log readers understand *why* the
        # classic circle was chosen instead of the animated sprite avatar.
        reasons = []
        if not enough_ram:
            reasons.append("low RAM")
        if not enough_cpu:
            reasons.append("few CPU cores")
        reason = ", ".join(reasons) or "constrained hardware"
        logger.info("Avatar tier: %s (fallback \u2014 %s)", tier, reason)
    else:
        logger.info("Avatar tier: %s", tier)
    return tier

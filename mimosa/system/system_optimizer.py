"""Hardware-aware configuration tuning for MimOSA (M2.3).

MimOSA runs on everything from a beefy desktop to a modest laptop. Rather than
shipping one-size-fits-all defaults, the :class:`SystemOptimizer` reads the
:class:`~mimosa.system.system_profiler.SystemProfiler` and
:class:`~mimosa.system.hardware_detector.HardwareDetector` snapshots and derives
sensible runtime settings:

* **Audio backend** -- which of PipeWire/PulseAudio/ALSA to drive.
* **Wake-word sensitivity** -- a touch more forgiving on low-end mics/CPUs.
* **Speech-to-text model size** -- a larger Whisper model only where the CPU and
  RAM can keep up, so the voice loop stays responsive.
* **Text-to-speech quality** -- higher-quality synthesis on capable machines.
* **Conversation history limit** -- bounded by available RAM so long sessions
  never balloon memory.

Everything here is *pure logic over the two snapshots* -- no I/O, no subprocess,
no LLM -- which makes it trivially testable and instant to compute.

Design principles
-----------------
* **Local & private.** Pure computation over already-collected local facts.
* **Graceful degradation.** Missing facts fall back to conservative defaults
  rather than failing.
* **Deterministic & testable.** Same inputs always yield the same recommended
  config.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from typing import Any, Dict, Optional

from mimosa.system.hardware_detector import HardwareDetector, HardwareProfile
from mimosa.system.system_profiler import SystemProfile, SystemProfiler

logger = logging.getLogger("mimosa.system.system_optimizer")


@dataclass
class OptimizedConfig:
    """Recommended runtime settings derived from the host's capabilities.

    Attributes:
        audio_backend: Backend to drive (``"pipewire"``/``"pulseaudio"``/
            ``"alsa"``), or ``None`` if no audio was detected.
        wake_word_sensitivity: Float in ``[0, 1]``; higher = more sensitive
            (more triggers). Tuned up on weaker hardware to avoid missed wakes.
        whisper_model: Suggested Whisper model size
            (``"tiny"``/``"base"``/``"small"``/``"medium"``).
        tts_quality: ``"low"``/``"standard"``/``"high"``.
        max_history_turns: Cap on retained conversation turns.
        performance_tier: ``"low"``/``"medium"``/``"high"`` overall rating.
        notes: Human-readable explanations for the choices.
    """

    audio_backend: Optional[str] = None
    wake_word_sensitivity: float = 0.5
    whisper_model: str = "base"
    tts_quality: str = "standard"
    max_history_turns: int = 10
    performance_tier: str = "medium"
    notes: Dict[str, str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.notes is None:
            self.notes = {}

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


class SystemOptimizer:
    """Derive an :class:`OptimizedConfig` from system + hardware profiles.

    Args:
        profiler: A :class:`SystemProfiler` (constructed lazily if omitted).
        hardware: A :class:`HardwareDetector` (constructed lazily if omitted).
    """

    def __init__(
        self,
        *,
        profiler: Optional[SystemProfiler] = None,
        hardware: Optional[HardwareDetector] = None,
    ) -> None:
        self._profiler = profiler or SystemProfiler()
        self._hardware = hardware or HardwareDetector()
        self._cache: Optional[OptimizedConfig] = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def config(self) -> OptimizedConfig:
        if self._cache is None:
            self._cache = self.optimize()
        return self._cache

    def optimize(self) -> OptimizedConfig:
        """Compute and return a fresh :class:`OptimizedConfig`."""
        profile = self._profiler.profile
        hw = self._hardware.profile

        cfg = OptimizedConfig()
        cfg.audio_backend = self._select_audio_backend(hw)
        tier = self._performance_tier(hw)
        cfg.performance_tier = tier
        cfg.whisper_model = self._select_whisper_model(hw, tier)
        cfg.tts_quality = self._select_tts_quality(tier)
        cfg.wake_word_sensitivity = self._select_wake_word_sensitivity(hw, tier)
        cfg.max_history_turns = self._select_history_limit(hw)
        cfg.notes = self._explain(profile, hw, cfg)
        logger.debug("Optimized config: %s", cfg.as_dict())
        return cfg

    # ------------------------------------------------------------------
    # Individual decisions
    # ------------------------------------------------------------------

    @staticmethod
    def _select_audio_backend(hw: HardwareProfile) -> Optional[str]:
        backend = (hw.audio.backend or "").lower()
        if backend == "pipewire":
            return "pipewire"
        if backend == "pulseaudio":
            return "pulseaudio"
        if backend == "alsa":
            return "alsa"
        return None

    @staticmethod
    def _performance_tier(hw: HardwareProfile) -> str:
        """Rate the machine low/medium/high from cores + RAM."""
        cores = hw.cpu.logical_cores or 1
        ram_gb = hw.memory.total_gb or 0.0

        # High: plenty of both. Low: short on either. Medium otherwise.
        if cores >= 8 and ram_gb >= 16:
            return "high"
        if cores <= 2 or ram_gb < 4:
            return "low"
        return "medium"

    @staticmethod
    def _select_whisper_model(hw: HardwareProfile, tier: str) -> str:
        """Pick a Whisper size the CPU/RAM can run comfortably offline."""
        if tier == "high":
            return "small"
        if tier == "low":
            return "tiny"
        return "base"

    @staticmethod
    def _select_tts_quality(tier: str) -> str:
        return {"high": "high", "medium": "standard", "low": "low"}[tier]

    @staticmethod
    def _select_wake_word_sensitivity(hw: HardwareProfile, tier: str) -> float:
        """More sensitive on weaker hardware / when no mic is detected.

        Rationale: a slower machine may drop frames, making the wake-word
        engine less likely to trigger; nudging sensitivity up compensates.
        """
        base = {"high": 0.5, "medium": 0.6, "low": 0.7}[tier]
        if not hw.has_microphone:
            base = min(1.0, base + 0.05)
        return round(base, 2)

    @staticmethod
    def _select_history_limit(hw: HardwareProfile) -> int:
        """Cap retained turns by total RAM to keep memory bounded."""
        ram_gb = hw.memory.total_gb or 0.0
        if ram_gb >= 32:
            return 40
        if ram_gb >= 16:
            return 25
        if ram_gb >= 8:
            return 15
        if ram_gb >= 4:
            return 8
        return 5

    @staticmethod
    def _explain(profile: SystemProfile, hw: HardwareProfile, cfg: OptimizedConfig) -> Dict[str, str]:
        notes: Dict[str, str] = {}
        notes["performance"] = (
            f"Rated '{cfg.performance_tier}' from "
            f"{hw.cpu.logical_cores or '?'} CPU threads and "
            f"{hw.memory.total_gb or '?'} GB RAM."
        )
        if cfg.audio_backend:
            notes["audio"] = f"Using the detected {cfg.audio_backend} backend."
        else:
            notes["audio"] = "No audio backend detected; audio features limited."
        notes["stt"] = (
            f"Whisper '{cfg.whisper_model}' chosen to balance accuracy and "
            f"latency on this CPU."
        )
        notes["history"] = (
            f"Keeping up to {cfg.max_history_turns} turns to bound memory use."
        )
        if not hw.has_microphone:
            notes["microphone"] = "No microphone detected; voice input may be unavailable."
        return notes

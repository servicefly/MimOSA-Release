"""Hardware detection for MimOSA (M2.3).

Where :mod:`mimosa.system.system_profiler` answers *"what OS/desktop is this?"*,
this module answers *"what hardware is underneath?"* -- the audio backend, the
displays, the microphone(s), the CPU, the RAM, and the GPU. The
:class:`SystemOptimizer` uses these facts to pick sensible defaults (which audio
backend to drive, how large a Whisper model the CPU can handle, how much
conversation history RAM allows), and the voice ``SystemInfoSkill`` reads them
out loud ("show me my system specs").

Design principles
-----------------
* **Local & private.** Facts come from :mod:`psutil`, ``/proc``, ``/sys`` and a
  handful of standard CLI probes (``pactl``, ``wpctl``, ``lspci``,
  ``xrandr``...). Nothing is sent anywhere; no LLM is involved.
* **Graceful degradation.** Any probe may be missing (headless box, no audio
  server, no ``lspci``). Each detector catches its own failures and returns
  ``None``/empty rather than raising, so a partial picture is always available.
* **Bounded.** Every subprocess call has a short timeout.
* **Cached.** Detection runs once and is memoized; :meth:`refresh` re-scans.
* **Testable.** ``psutil``, the command runner, ``which``, and the ``/proc`` &
  ``/sys`` roots are injectable, so tests are fully hermetic.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

logger = logging.getLogger("mimosa.system.hardware_detector")

DEFAULT_TIMEOUT = 4.0

Runner = Callable[[Sequence[str]], "RunOutput"]
Which = Callable[[str], Optional[str]]


@dataclass
class RunOutput:
    """Captured result of running a subprocess."""

    returncode: int
    stdout: str = ""
    stderr: str = ""


def _default_runner(argv: Sequence[str]) -> RunOutput:
    try:
        proc = subprocess.run(  # noqa: S603 - fixed argv lists, no shell
            list(argv),
            check=False,
            capture_output=True,
            text=True,
            timeout=DEFAULT_TIMEOUT,
        )
        return RunOutput(proc.returncode, proc.stdout or "", proc.stderr or "")
    except FileNotFoundError:
        return RunOutput(127, "", "command not found")
    except subprocess.TimeoutExpired:
        return RunOutput(124, "", "command timed out")
    except OSError as exc:  # pragma: no cover - unusual exec failure
        return RunOutput(1, "", str(exc))


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------

@dataclass
class CPUInfo:
    """Processor facts."""

    model: Optional[str] = None
    physical_cores: Optional[int] = None
    logical_cores: Optional[int] = None
    max_frequency_mhz: Optional[float] = None
    architecture: Optional[str] = None


@dataclass
class MemoryInfo:
    """RAM facts (bytes + convenient GB)."""

    total_bytes: Optional[int] = None
    available_bytes: Optional[int] = None

    @property
    def total_gb(self) -> Optional[float]:
        return round(self.total_bytes / (1024 ** 3), 2) if self.total_bytes else None

    @property
    def available_gb(self) -> Optional[float]:
        return (
            round(self.available_bytes / (1024 ** 3), 2)
            if self.available_bytes is not None
            else None
        )


@dataclass
class GPUInfo:
    """A single graphics adapter."""

    vendor: Optional[str] = None  # "Intel", "NVIDIA", "AMD", ...
    description: Optional[str] = None


@dataclass
class DisplayInfo:
    """A single connected display/output."""

    name: Optional[str] = None
    resolution: Optional[str] = None  # e.g. "1920x1080"
    primary: bool = False


@dataclass
class AudioInfo:
    """Audio subsystem facts."""

    backend: Optional[str] = None  # "PipeWire", "PulseAudio", "ALSA"
    server_running: bool = False
    sinks: int = 0
    sources: int = 0


@dataclass
class HardwareProfile:
    """Aggregate snapshot of the host hardware."""

    cpu: CPUInfo = field(default_factory=CPUInfo)
    memory: MemoryInfo = field(default_factory=MemoryInfo)
    gpus: List[GPUInfo] = field(default_factory=list)
    displays: List[DisplayInfo] = field(default_factory=list)
    audio: AudioInfo = field(default_factory=AudioInfo)
    microphones: List[str] = field(default_factory=list)

    @property
    def multi_monitor(self) -> bool:
        return len(self.displays) > 1

    @property
    def has_microphone(self) -> bool:
        return len(self.microphones) > 0

    def as_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["memory"]["total_gb"] = self.memory.total_gb
        data["memory"]["available_gb"] = self.memory.available_gb
        data["multi_monitor"] = self.multi_monitor
        data["has_microphone"] = self.has_microphone
        return data

    def summary(self) -> str:
        """A short speakable hardware summary."""
        parts = []
        if self.cpu.logical_cores:
            label = f"{self.cpu.logical_cores}-core CPU"
            if self.cpu.model:
                label = f"{self.cpu.model} ({self.cpu.logical_cores} threads)"
            parts.append(label)
        if self.memory.total_gb:
            parts.append(f"{self.memory.total_gb:g} GB RAM")
        if self.gpus:
            vendors = ", ".join(g.vendor or "GPU" for g in self.gpus)
            parts.append(f"{vendors} graphics")
        if self.audio.backend:
            parts.append(f"{self.audio.backend} audio")
        if self.displays:
            parts.append(
                f"{len(self.displays)} display" + ("s" if len(self.displays) != 1 else "")
            )
        return "; ".join(parts) if parts else "hardware details unavailable"


class HardwareDetector:
    """Detect and cache host hardware facts.

    Args:
        psutil_module: The :mod:`psutil` module (or a fake). Defaults to the
            real one if importable, else ``None`` (CPU/RAM degrade gracefully).
        runner: argv -> :class:`RunOutput` executor (defaults to subprocess).
        which: tool-name -> path resolver (defaults to :func:`shutil.which`).
        proc_root: Base path for ``/proc`` reads (overridable for tests).
        sys_root: Base path for ``/sys`` reads (overridable for tests).
        environ: Environment mapping (for display-server-aware probing).
    """

    def __init__(
        self,
        *,
        psutil_module: Any = "auto",
        runner: Optional[Runner] = None,
        which: Optional[Which] = None,
        proc_root: str = "/proc",
        sys_root: str = "/sys",
        environ: Optional[Dict[str, str]] = None,
    ) -> None:
        if psutil_module == "auto":
            try:
                import psutil  # type: ignore

                psutil_module = psutil
            except Exception:  # pragma: no cover - psutil is a dependency
                psutil_module = None
        self._psutil = psutil_module
        self._run = runner or _default_runner
        self._which = which or shutil.which
        self._proc = Path(proc_root)
        self._sys = Path(sys_root)
        self._environ = environ if environ is not None else dict(os.environ)
        self._cache: Optional[HardwareProfile] = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def profile(self) -> HardwareProfile:
        if self._cache is None:
            self._cache = self._detect()
        return self._cache

    def refresh(self) -> HardwareProfile:
        self._cache = self._detect()
        return self._cache

    def _detect(self) -> HardwareProfile:
        return HardwareProfile(
            cpu=self.detect_cpu(),
            memory=self.detect_memory(),
            gpus=self.detect_gpus(),
            displays=self.detect_displays(),
            audio=self.detect_audio(),
            microphones=self.detect_microphones(),
        )

    # ------------------------------------------------------------------
    # CPU
    # ------------------------------------------------------------------

    def detect_cpu(self) -> CPUInfo:
        info = CPUInfo()
        import platform as _platform

        info.architecture = _platform.machine() or None

        if self._psutil is not None:
            try:
                info.logical_cores = self._psutil.cpu_count(logical=True)
                info.physical_cores = self._psutil.cpu_count(logical=False)
            except Exception:  # pragma: no cover
                pass
            try:
                freq = self._psutil.cpu_freq()
                if freq and getattr(freq, "max", 0):
                    info.max_frequency_mhz = round(float(freq.max), 1)
            except Exception:  # pragma: no cover - not all platforms expose freq
                pass

        info.model = self._cpu_model_from_proc()
        # Fall back for core count if psutil was unavailable.
        if info.logical_cores is None:
            try:
                info.logical_cores = os.cpu_count()
            except Exception:  # pragma: no cover
                pass
        return info

    def _cpu_model_from_proc(self) -> Optional[str]:
        """Read the CPU model name from ``/proc/cpuinfo``."""
        path = self._proc / "cpuinfo"
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
        for line in text.splitlines():
            if ":" not in line:
                continue
            key, _, value = line.partition(":")
            key = key.strip().lower()
            if key in ("model name", "hardware", "cpu"):
                value = value.strip()
                if value:
                    return value
        return None

    # ------------------------------------------------------------------
    # Memory
    # ------------------------------------------------------------------

    def detect_memory(self) -> MemoryInfo:
        info = MemoryInfo()
        if self._psutil is not None:
            try:
                vm = self._psutil.virtual_memory()
                info.total_bytes = int(vm.total)
                info.available_bytes = int(vm.available)
                return info
            except Exception:  # pragma: no cover
                pass
        # Fallback: parse /proc/meminfo (values in kB).
        path = self._proc / "meminfo"
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            return info
        values: Dict[str, int] = {}
        for line in text.splitlines():
            m = re.match(r"(\w+):\s+(\d+)\s*kB", line)
            if m:
                values[m.group(1)] = int(m.group(2)) * 1024
        info.total_bytes = values.get("MemTotal")
        info.available_bytes = values.get("MemAvailable")
        return info

    # ------------------------------------------------------------------
    # GPU
    # ------------------------------------------------------------------

    def detect_gpus(self) -> List[GPUInfo]:
        """Detect graphics adapters via ``lspci`` (preferred) or sysfs."""
        gpus: List[GPUInfo] = []
        if self._which("lspci"):
            out = self._run(["lspci"])
            if out.returncode == 0:
                for line in out.stdout.splitlines():
                    if re.search(r"\b(VGA compatible controller|3D controller|Display controller)\b", line, re.I):
                        desc = line.split(":", 2)[-1].strip() if ":" in line else line.strip()
                        gpus.append(GPUInfo(vendor=self._gpu_vendor(desc), description=desc))
        if gpus:
            return gpus

        # Fallback: enumerate DRM cards in sysfs and read their vendor IDs.
        gpus.extend(self._gpus_from_sysfs())
        return gpus

    @staticmethod
    def _gpu_vendor(text: str) -> Optional[str]:
        lowered = text.lower()
        if "nvidia" in lowered:
            return "NVIDIA"
        # Use word boundaries for short tokens so "corporATIon" doesn't match
        # "ati" and "AMD" isn't matched inside unrelated words.
        if (
            re.search(r"\bamd\b", lowered)
            or "advanced micro devices" in lowered
            or re.search(r"\bati\b", lowered)
            or "radeon" in lowered
        ):
            return "AMD"
        if "intel" in lowered:
            return "Intel"
        if "vmware" in lowered or "virtualbox" in lowered or "qxl" in lowered or "red hat" in lowered:
            return "Virtual"
        return None

    #: PCI vendor IDs -> friendly name.
    _PCI_VENDORS = {
        "0x8086": "Intel",
        "0x10de": "NVIDIA",
        "0x1002": "AMD",
        "0x1af4": "Virtual",
        "0x1234": "Virtual",
        "0x15ad": "Virtual",
    }

    def _gpus_from_sysfs(self) -> List[GPUInfo]:
        gpus: List[GPUInfo] = []
        drm = self._sys / "class" / "drm"
        try:
            entries = sorted(drm.iterdir())
        except OSError:
            return gpus
        for entry in entries:
            # Only top-level cardN nodes (skip cardN-eDP-1 connector nodes).
            if not re.fullmatch(r"card\d+", entry.name):
                continue
            vendor_id = None
            try:
                vendor_id = (entry / "device" / "vendor").read_text().strip().lower()
            except OSError:
                pass
            vendor = self._PCI_VENDORS.get(vendor_id or "", None)
            gpus.append(GPUInfo(vendor=vendor, description=f"{entry.name} ({vendor_id})" if vendor_id else entry.name))
        return gpus

    # ------------------------------------------------------------------
    # Displays
    # ------------------------------------------------------------------

    def detect_displays(self) -> List[DisplayInfo]:
        """Detect connected displays.

        Prefers ``xrandr`` (works on X11 and XWayland). Falls back to sysfs DRM
        connector status so a basic count is available even headless-ish.
        """
        displays: List[DisplayInfo] = []
        if self._which("xrandr"):
            out = self._run(["xrandr", "--query"])
            if out.returncode == 0 and out.stdout:
                displays = self._parse_xrandr(out.stdout)
                if displays:
                    return displays

        # Fallback: connected DRM connectors in sysfs.
        return self._displays_from_sysfs()

    @staticmethod
    def _parse_xrandr(text: str) -> List[DisplayInfo]:
        displays: List[DisplayInfo] = []
        current: Optional[DisplayInfo] = None
        for line in text.splitlines():
            m = re.match(r"^(\S+)\s+connected\s+(primary\s+)?(\d+x\d+)?", line)
            if m and " connected" in line:
                name = m.group(1)
                primary = bool(m.group(2))
                resolution = m.group(3)
                current = DisplayInfo(name=name, resolution=resolution, primary=primary)
                displays.append(current)
            elif current is not None and current.resolution is None:
                # The active mode line is indented and marked with '*'.
                mode = re.match(r"^\s+(\d+x\d+)\s.*\*", line)
                if mode:
                    current.resolution = mode.group(1)
        return displays

    def _displays_from_sysfs(self) -> List[DisplayInfo]:
        displays: List[DisplayInfo] = []
        drm = self._sys / "class" / "drm"
        try:
            entries = sorted(drm.iterdir())
        except OSError:
            return displays
        for entry in entries:
            status_path = entry / "status"
            try:
                status = status_path.read_text().strip()
            except OSError:
                continue
            if status == "connected":
                displays.append(DisplayInfo(name=entry.name))
        return displays

    # ------------------------------------------------------------------
    # Audio
    # ------------------------------------------------------------------

    def detect_audio(self) -> AudioInfo:
        """Identify the active audio backend and basic sink/source counts.

        Detection order mirrors a modern Kubuntu stack: PipeWire (``wpctl``/
        ``pw-cli``) is preferred, then PulseAudio (``pactl``), then bare ALSA
        (``aplay``). The presence of a *running server* is distinguished from a
        merely *installed* tool.
        """
        info = AudioInfo()

        # PipeWire?
        if self._which("wpctl") or self._which("pw-cli"):
            info.backend = "PipeWire"
            # pactl works against PipeWire's pulse shim too; use it for counts.
            self._fill_pulse_counts(info)
            info.server_running = info.server_running or self._pipewire_running()
            return info

        # PulseAudio?
        if self._which("pactl"):
            info.backend = "PulseAudio"
            self._fill_pulse_counts(info)
            return info

        # Bare ALSA?
        if self._which("aplay") or (self._proc / "asound" / "cards").exists():
            info.backend = "ALSA"
            info.server_running = True  # ALSA is kernel-level, always "up"
            return info

        return info

    def _pipewire_running(self) -> bool:
        if self._which("pw-cli"):
            out = self._run(["pw-cli", "info", "0"])
            return out.returncode == 0
        return False

    def _fill_pulse_counts(self, info: AudioInfo) -> None:
        if not self._which("pactl"):
            return
        info_out = self._run(["pactl", "info"])
        if info_out.returncode == 0:
            info.server_running = True
        sinks = self._run(["pactl", "list", "short", "sinks"])
        if sinks.returncode == 0:
            info.sinks = len([l for l in sinks.stdout.splitlines() if l.strip()])
        sources = self._run(["pactl", "list", "short", "sources"])
        if sources.returncode == 0:
            info.sources = len([l for l in sources.stdout.splitlines() if l.strip()])

    # ------------------------------------------------------------------
    # Microphones
    # ------------------------------------------------------------------

    def detect_microphones(self) -> List[str]:
        """List available capture (microphone) devices.

        Uses ``pactl list short sources`` (filtering out monitor sources) when
        available, else ``arecord -l``, else ALSA ``/proc`` enumeration.
        """
        mics: List[str] = []
        if self._which("pactl"):
            out = self._run(["pactl", "list", "short", "sources"])
            if out.returncode == 0:
                for line in out.stdout.splitlines():
                    if not line.strip() or ".monitor" in line:
                        continue
                    fields = line.split("\t")
                    if len(fields) >= 2:
                        mics.append(fields[1])
                if mics:
                    return mics

        if self._which("arecord"):
            out = self._run(["arecord", "-l"])
            if out.returncode == 0:
                for line in out.stdout.splitlines():
                    m = re.match(r"card \d+: .*?\[(.+?)\]", line)
                    if m:
                        mics.append(m.group(1))
                if mics:
                    return mics

        # Fallback: ALSA capture-capable cards.
        return self._mics_from_proc()

    def _mics_from_proc(self) -> List[str]:
        mics: List[str] = []
        cards = self._proc / "asound" / "cards"
        try:
            text = cards.read_text(encoding="utf-8")
        except OSError:
            return mics
        for line in text.splitlines():
            m = re.search(r"\]:\s*(.+)$", line)
            if m:
                mics.append(m.group(1).strip())
        return mics

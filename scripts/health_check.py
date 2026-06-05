#!/usr/bin/env python3
"""MimOSA environment health check.

Run this after installing dependencies to verify that your machine is ready to
run MimOSA. It checks, and reports in a readable format:

    1. Python version (3.10+ required)
    2. Whether critical third-party imports load
    3. Abacus.AI RouteLLM connectivity (only if ABACUS_API_KEY is present)
    4. System info (OS, distro, desktop environment, CPU/RAM)

Usage:
    python scripts/health_check.py

Exit code is 0 when no *critical* checks fail, 1 otherwise (handy for CI).
The script is intentionally dependency-light at import time so it can run even
before all optional packages are installed.
"""

from __future__ import annotations

import importlib
import os
import platform
import sys
from pathlib import Path

# Make the project root importable when run directly (python scripts/...).
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Minimum supported Python version.
MIN_PYTHON = (3, 10)

# Third-party packages MimOSA relies on. (module_name, friendly_name,
# critical?) -- non-critical ones only produce warnings.
CRITICAL_IMPORTS = [
    ("requests", "requests (HTTP client)", True),
    ("dotenv", "python-dotenv (.env loader)", True),
    ("psutil", "psutil (system monitoring)", True),
    ("pvporcupine", "pvporcupine (wake word)", False),
    ("whisper", "openai-whisper (STT)", False),
    ("pyaudio", "pyaudio (audio I/O)", False),
]

# ANSI colors (disabled automatically when output is not a TTY).
_USE_COLOR = sys.stdout.isatty()


def _c(text: str, color: str) -> str:
    if not _USE_COLOR:
        return text
    codes = {"green": "32", "red": "31", "yellow": "33", "cyan": "36", "bold": "1"}
    return f"\033[{codes.get(color, '0')}m{text}\033[0m"


def _ok(msg: str) -> None:
    print(f"  {_c('[ OK ]', 'green')} {msg}")


def _fail(msg: str) -> None:
    print(f"  {_c('[FAIL]', 'red')} {msg}")


def _warn(msg: str) -> None:
    print(f"  {_c('[WARN]', 'yellow')} {msg}")


def _header(title: str) -> None:
    print("\n" + _c(title, "bold"))
    print(_c("-" * len(title), "cyan"))


def check_python_version() -> bool:
    """Verify the interpreter meets the minimum version requirement."""
    _header("1. Python version")
    version = sys.version_info
    pretty = f"{version.major}.{version.minor}.{version.micro}"
    if (version.major, version.minor) >= MIN_PYTHON:
        _ok(f"Python {pretty} (>= {MIN_PYTHON[0]}.{MIN_PYTHON[1]} required)")
        return True
    _fail(
        f"Python {pretty} is too old; "
        f"{MIN_PYTHON[0]}.{MIN_PYTHON[1]}+ is required."
    )
    return False


def check_imports() -> bool:
    """Try importing each dependency; criticals failing => overall failure."""
    _header("2. Dependency imports")
    all_critical_ok = True
    for module_name, friendly, critical in CRITICAL_IMPORTS:
        try:
            importlib.import_module(module_name)
            _ok(friendly)
        except Exception as exc:  # ImportError or transitive import errors
            if critical:
                _fail(f"{friendly} -- {exc}")
                all_critical_ok = False
            else:
                _warn(f"{friendly} not available ({exc}). Install later.")
    return all_critical_ok


def check_mimosa_package() -> bool:
    """Verify the local mimosa package and its LLM factory import cleanly."""
    _header("3. MimOSA package & LLM abstraction")
    try:
        import mimosa  # noqa: F401
        from mimosa.llm import create_provider

        provider = create_provider()
        _ok(f"mimosa v{mimosa.__version__} imported")
        _ok(f"LLM provider factory -> {provider!r}")
        return True
    except Exception as exc:
        _fail(f"Could not initialize MimOSA package: {exc}")
        return False


def check_abacus_connectivity() -> bool:
    """Test Abacus.AI connectivity, but only if an API key is configured.

    Returns True when the key is absent (not a failure -- just skipped) or when
    the connectivity check succeeds. Returns False only on an actual failed
    connection attempt.
    """
    _header("4. Abacus.AI RouteLLM connectivity")

    # Load .env if python-dotenv is available, so the key can be picked up.
    try:
        from dotenv import load_dotenv

        load_dotenv(PROJECT_ROOT / ".env")
    except Exception:
        pass

    if not os.getenv("ABACUS_API_KEY"):
        _warn("ABACUS_API_KEY not set -- skipping connectivity test.")
        return True

    try:
        from mimosa.llm.abacus_provider import AbacusProvider

        provider = AbacusProvider()
        if provider.health_check():
            _ok("Reached Abacus.AI RouteLLM and received a response.")
            return True
        _fail("Abacus.AI health check failed (key set but no valid response).")
        return False
    except Exception as exc:
        _fail(f"Abacus.AI connectivity error: {exc}")
        return False


def report_system_info() -> None:
    """Print OS, distro, desktop, display server, CPU/RAM/GPU (M2.3 profilers).

    Uses the M2.3 :class:`SystemProfiler` and :class:`HardwareDetector` when
    importable (they degrade gracefully on any host), and falls back to a basic
    ``platform``/``/etc/os-release`` read if the package can't be imported.
    """
    _header("5. System information")
    try:
        from mimosa.system.hardware_detector import HardwareDetector
        from mimosa.system.system_profiler import SystemProfiler

        profile = SystemProfiler().profile
        hw = HardwareDetector().profile

        print(f"  OS            : {platform.system()} {platform.release()}")
        print(f"  Architecture  : {profile.architecture or platform.machine()}")
        print(f"  Distro        : {profile.distro_name or 'unknown'}")
        de = profile.desktop_environment or "unknown"
        if profile.plasma_version and profile.desktop_environment == "KDE":
            de = f"KDE Plasma {profile.plasma_version}"
        print(f"  Desktop env.  : {de}")
        print(f"  Display server: {profile.display_server or 'unknown'}")
        print(f"  Kernel        : {profile.kernel or 'unknown'}")
        if hw.cpu.logical_cores:
            model = f" ({hw.cpu.model})" if hw.cpu.model else ""
            print(f"  CPU           : {hw.cpu.logical_cores} threads{model}")
        if hw.memory.total_gb:
            avail = f" ({hw.memory.available_gb:g} GB free)" if hw.memory.available_gb else ""
            print(f"  Total RAM     : {hw.memory.total_gb:g} GB{avail}")
        if hw.gpus:
            print(f"  GPU           : {', '.join(g.description or g.vendor or 'GPU' for g in hw.gpus)}")
        print(f"  Audio backend : {hw.audio.backend or 'none detected'}")
        print(f"  Displays      : {len(hw.displays)}  Microphones: {len(hw.microphones)}")
        return
    except Exception as exc:  # pragma: no cover - fallback path
        _warn(f"Rich system profiling unavailable ({exc}); using basic info.")

    print(f"  OS            : {platform.system()} {platform.release()}")
    print(f"  Architecture  : {platform.machine()}")
    distro = "unknown"
    os_release = Path("/etc/os-release")
    if os_release.exists():
        info = {}
        for line in os_release.read_text().splitlines():
            if "=" in line:
                key, _, value = line.partition("=")
                info[key] = value.strip().strip('"')
        distro = info.get("PRETTY_NAME", info.get("NAME", "unknown"))
    print(f"  Distro        : {distro}")
    de = os.getenv("XDG_CURRENT_DESKTOP") or os.getenv("DESKTOP_SESSION") or "unknown"
    print(f"  Desktop env.  : {de}")


def check_kubuntu_compatibility() -> bool:
    """Report Kubuntu 26.04 compatibility and runtime capabilities (M2.3).

    This is a *non-critical* check: MimOSA is built and tuned for Kubuntu 26.04
    with KDE Plasma, but it runs anywhere thanks to graceful degradation. We
    therefore emit warnings (never hard failures) when the host differs, and
    surface what voice features will actually work here.
    """
    _header("6. Kubuntu 26.04 / KDE compatibility")
    try:
        from mimosa.system.hardware_detector import HardwareDetector
        from mimosa.system.kde_integration import KDEIntegration
        from mimosa.system.system_optimizer import SystemOptimizer
        from mimosa.system.system_profiler import SystemProfiler
    except Exception as exc:  # pragma: no cover - import guard
        _warn(f"M2.3 system modules unavailable: {exc}")
        return True

    profiler = SystemProfiler()
    profile = profiler.profile
    hw = HardwareDetector()
    hardware = hw.profile

    # Target-platform match (informational).
    if profile.is_kubuntu:
        _ok("Running on Kubuntu (the target platform).")
        if profile.distro_version and profile.distro_version.startswith("26.04"):
            _ok("Distro version 26.04 matches the target release.")
        elif profile.distro_version:
            _warn(f"Distro version {profile.distro_version} differs from target 26.04.")
    elif profile.distro_id == "ubuntu":
        _warn("Ubuntu detected but not the KDE (Kubuntu) variant; KDE features limited.")
    else:
        _warn(
            f"Host is {profile.distro_name or 'a non-Ubuntu system'}; MimOSA will run "
            "with graceful degradation but is tuned for Kubuntu 26.04."
        )

    # Desktop / display server.
    if profile.is_kde:
        _ok(f"KDE Plasma session detected ({profile.plasma_version or 'version unknown'}).")
    else:
        _warn(f"Desktop is {profile.desktop_environment or 'unknown'} (KDE-specific features will no-op).")
    if profile.display_server:
        _ok(f"Display server: {profile.display_server}.")
    else:
        _warn("No graphical display server detected (headless?).")

    # Runtime audio / input verification.
    if hardware.audio.backend:
        state = "running" if hardware.audio.server_running else "installed"
        _ok(f"Audio backend: {hardware.audio.backend} ({state}).")
    else:
        _warn("No audio backend detected (PipeWire/PulseAudio/ALSA); speech output limited.")
    if hardware.has_microphone:
        _ok(f"Microphone(s) detected: {len(hardware.microphones)}.")
    else:
        _warn("No microphone detected; voice input may be unavailable.")

    # KDE integration capability report.
    kde = KDEIntegration(is_kde=profile.is_kde)
    caps = kde.capabilities()
    if caps["available"]:
        _ok(f"KDE D-Bus integration available via {caps['transport']}.")
    else:
        _warn(
            "KDE D-Bus integration unavailable "
            f"(transport={caps['transport'] or 'none'}); notifications/desktops will no-op."
        )

    # Recommended tuning.
    optimizer = SystemOptimizer(profiler=profiler, hardware=hw)
    cfg = optimizer.config
    _ok(
        f"Recommended tuning: {cfg.performance_tier} tier -> Whisper '{cfg.whisper_model}', "
        f"{cfg.tts_quality} TTS, wake sensitivity {cfg.wake_word_sensitivity}, "
        f"{cfg.max_history_turns} history turns."
    )
    return True


def main() -> int:
    """Run all checks and return a process exit code (0 = healthy)."""
    print(_c("=" * 60, "cyan"))
    print(_c("  MimOSA Health Check", "bold"))
    print(_c("=" * 60, "cyan"))

    results = {
        "Python version": check_python_version(),
        "Critical imports": check_imports(),
        "MimOSA package": check_mimosa_package(),
        "Abacus connectivity": check_abacus_connectivity(),
    }
    report_system_info()
    check_kubuntu_compatibility()

    _header("Summary")
    all_ok = True
    for name, ok in results.items():
        (_ok if ok else _fail)(name)
        all_ok = all_ok and ok

    print()
    if all_ok:
        print(_c("All critical checks passed. MimOSA is ready to run.", "green"))
        return 0
    print(_c("Some critical checks failed. See details above.", "red"))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

"""Operating-system & session profiling for MimOSA (M2.3).

This module answers questions like *"what distribution am I running on?"*,
*"is this KDE Plasma or GNOME?"*, *"Wayland or X11?"*, and *"what's the kernel
and CPU architecture?"*. The :class:`SystemProfiler` collects this once, caches
it, and exposes it as a typed :class:`SystemProfile` so the rest of MimOSA --
the optimizer, the KDE integration, the health checks, and the voice
``SystemInfoSkill`` -- can adapt its behavior to the host without each of them
re-parsing ``/etc/os-release`` or poking environment variables.

Design principles
-----------------
* **Local & private.** Everything is read from ``/etc/os-release``, environment
  variables, the :mod:`platform` module, and (optionally) ``plasmashell
  --version``. Nothing leaves the machine; no LLM is involved.
* **Graceful degradation.** MimOSA targets Kubuntu 26.04 but must run anywhere
  for development and testing. Every field is optional: a missing
  ``/etc/os-release``, an unset ``XDG_CURRENT_DESKTOP``, or a non-KDE session
  yields ``None``/sensible defaults rather than an exception.
* **Bounded.** The single external command (``plasmashell --version``) runs
  with a short timeout so a wedged helper can never stall startup.
* **Cached.** Detection runs once on first access and the result is memoized;
  subsequent reads are free. :meth:`SystemProfiler.refresh` forces a re-scan.
* **Testable.** The os-release path, the environment mapping, and the command
  runner are all injectable, so unit tests are fully hermetic and never depend
  on the machine they run on.
"""

from __future__ import annotations

import logging
import os
import platform
import re
import shutil
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Dict, Mapping, Optional, Sequence

logger = logging.getLogger("mimosa.system.system_profiler")

#: Default path to the os-release file (overridable for tests).
DEFAULT_OS_RELEASE = "/etc/os-release"

#: Timeout (seconds) for the optional ``plasmashell --version`` probe.
DEFAULT_TIMEOUT = 3.0

# Injectable types for testing.
Runner = Callable[[Sequence[str]], "RunOutput"]
Which = Callable[[str], Optional[str]]


@dataclass
class RunOutput:
    """Captured result of running a subprocess."""

    returncode: int
    stdout: str = ""
    stderr: str = ""


def _default_runner(argv: Sequence[str]) -> RunOutput:
    """Run ``argv`` with a fixed timeout, never raising for non-zero exits."""
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


@dataclass
class SystemProfile:
    """A snapshot of the host operating system and desktop session.

    Every field is optional so the profile is meaningful on any machine.

    Attributes:
        distro_id: ``ID`` from os-release, lowercased (e.g. ``"ubuntu"``).
        distro_name: Human-readable name (e.g. ``"Ubuntu 26.04 LTS"``).
        distro_version: ``VERSION_ID`` (e.g. ``"26.04"``).
        distro_variant: ``VARIANT`` if present (e.g. ``"Kubuntu"``).
        desktop_environment: Normalized DE name (``"KDE"``, ``"GNOME"``, ...).
        desktop_raw: The raw ``XDG_CURRENT_DESKTOP`` value.
        display_server: ``"wayland"``, ``"x11"`` or ``None``.
        session_type_raw: The raw ``XDG_SESSION_TYPE`` value.
        plasma_version: KDE Plasma version string if detected.
        architecture: CPU architecture (``platform.machine()``).
        kernel: Kernel release (``platform.release()``).
        kernel_version: Full kernel version string.
        hostname: Network node name.
        python_version: Running Python version.
        is_kubuntu: Convenience flag -- Ubuntu base with a KDE session/variant.
        is_kde: Convenience flag -- the active desktop is KDE Plasma.
    """

    distro_id: Optional[str] = None
    distro_name: Optional[str] = None
    distro_version: Optional[str] = None
    distro_variant: Optional[str] = None
    desktop_environment: Optional[str] = None
    desktop_raw: Optional[str] = None
    display_server: Optional[str] = None
    session_type_raw: Optional[str] = None
    plasma_version: Optional[str] = None
    architecture: Optional[str] = None
    kernel: Optional[str] = None
    kernel_version: Optional[str] = None
    hostname: Optional[str] = None
    python_version: Optional[str] = None
    is_kubuntu: bool = False
    is_kde: bool = False
    raw_os_release: Dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, object]:
        """Return a plain ``dict`` (e.g. for logging or metadata)."""
        return asdict(self)

    def summary(self) -> str:
        """A short, human/speakable one-line summary of the system."""
        parts = []
        if self.distro_name:
            parts.append(self.distro_name)
        elif self.distro_id:
            parts.append(self.distro_id)
        if self.desktop_environment:
            de = self.desktop_environment
            if self.plasma_version and de == "KDE":
                de = f"KDE Plasma {self.plasma_version}"
            parts.append(de)
        if self.display_server:
            parts.append(self.display_server.capitalize())
        if self.architecture:
            parts.append(self.architecture)
        return ", ".join(parts) if parts else "an unidentified system"


#: Map raw XDG desktop tokens to a normalized, friendly DE name.
_DESKTOP_ALIASES = {
    "kde": "KDE",
    "plasma": "KDE",
    "gnome": "GNOME",
    "gnome-flashback": "GNOME",
    "ubuntu:gnome": "GNOME",
    "xfce": "XFCE",
    "lxqt": "LXQt",
    "lxde": "LXDE",
    "mate": "MATE",
    "cinnamon": "Cinnamon",
    "x-cinnamon": "Cinnamon",
    "deepin": "Deepin",
    "budgie": "Budgie",
    "unity": "Unity",
    "pantheon": "Pantheon",
}


class SystemProfiler:
    """Detect and cache the host OS / desktop session profile.

    Args:
        os_release_path: Path to the os-release file. Defaults to
            ``/etc/os-release``; tests point this at a temp file.
        environ: Environment mapping to read XDG variables from. Defaults to
            :data:`os.environ`; tests pass an explicit dict.
        runner: Callable executing an argv list -> :class:`RunOutput`. Defaults
            to a real subprocess runner; tests inject a fake.
        which: Callable resolving a tool name to a path or ``None`` (defaults to
            :func:`shutil.which`).
    """

    def __init__(
        self,
        *,
        os_release_path: str = DEFAULT_OS_RELEASE,
        environ: Optional[Mapping[str, str]] = None,
        runner: Optional[Runner] = None,
        which: Optional[Which] = None,
    ) -> None:
        self._os_release_path = Path(os_release_path)
        self._environ = environ if environ is not None else os.environ
        self._run = runner or _default_runner
        self._which = which or shutil.which
        self._cache: Optional[SystemProfile] = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def profile(self) -> SystemProfile:
        """The cached :class:`SystemProfile`, detecting it on first access."""
        if self._cache is None:
            self._cache = self._detect()
        return self._cache

    def refresh(self) -> SystemProfile:
        """Force a fresh detection, replacing the cache."""
        self._cache = self._detect()
        return self._cache

    # ------------------------------------------------------------------
    # Detection
    # ------------------------------------------------------------------

    def _detect(self) -> SystemProfile:
        """Run all detectors and assemble a :class:`SystemProfile`."""
        os_release = self._parse_os_release()
        desktop_raw, desktop = self._detect_desktop()
        session_raw, display_server = self._detect_display_server()
        plasma = self._detect_plasma_version() if desktop == "KDE" else None

        distro_id = (os_release.get("ID") or "").lower() or None
        variant = os_release.get("VARIANT") or None
        # Kubuntu identifies as ID=ubuntu; the KDE session/variant marks it.
        is_kde = desktop == "KDE"
        is_kubuntu = bool(
            distro_id == "ubuntu"
            and (
                is_kde
                or (variant and "kubuntu" in variant.lower())
                or (os_release.get("VARIANT_ID", "").lower() == "kubuntu")
            )
        )

        try:
            uname = platform.uname()
            kernel = uname.release
            kernel_version = uname.version
            hostname = uname.node
            architecture = uname.machine
        except Exception:  # pragma: no cover - platform should not fail
            kernel = kernel_version = hostname = architecture = None

        profile = SystemProfile(
            distro_id=distro_id,
            distro_name=os_release.get("PRETTY_NAME") or os_release.get("NAME"),
            distro_version=os_release.get("VERSION_ID"),
            distro_variant=variant,
            desktop_environment=desktop,
            desktop_raw=desktop_raw,
            display_server=display_server,
            session_type_raw=session_raw,
            plasma_version=plasma,
            architecture=architecture,
            kernel=kernel,
            kernel_version=kernel_version,
            hostname=hostname,
            python_version=platform.python_version(),
            is_kubuntu=is_kubuntu,
            is_kde=is_kde,
            raw_os_release=os_release,
        )
        logger.debug("Detected system profile: %s", profile.summary())
        return profile

    def _parse_os_release(self) -> Dict[str, str]:
        """Parse ``/etc/os-release`` into a dict of unquoted values.

        Returns an empty dict if the file is missing or unreadable -- this is
        expected on non-Linux dev machines and is not an error.
        """
        try:
            text = self._os_release_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            logger.debug("os-release not readable at %s", self._os_release_path)
            return {}

        data: Dict[str, str] = {}
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip()
            # Strip matching surrounding quotes.
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            # Unescape common shell escapes.
            value = value.replace("\\$", "$").replace('\\"', '"').replace("\\\\", "\\")
            data[key] = value
        return data

    def _detect_desktop(self) -> tuple[Optional[str], Optional[str]]:
        """Return ``(raw_value, normalized_DE)`` from ``XDG_CURRENT_DESKTOP``.

        Falls back to ``XDG_SESSION_DESKTOP`` and ``DESKTOP_SESSION`` when the
        primary variable is unset.
        """
        raw = (
            self._environ.get("XDG_CURRENT_DESKTOP")
            or self._environ.get("XDG_SESSION_DESKTOP")
            or self._environ.get("DESKTOP_SESSION")
            or ""
        ).strip()
        if not raw:
            return None, None

        # XDG_CURRENT_DESKTOP may be colon-separated (e.g. "KDE" or
        # "ubuntu:GNOME"). Check each token against the alias table.
        for token in raw.split(":"):
            key = token.strip().lower()
            if key in _DESKTOP_ALIASES:
                return raw, _DESKTOP_ALIASES[key]
        # Unknown DE: title-case the first token so we still say *something*.
        first = raw.split(":")[0].strip()
        return raw, first.upper() if first.isupper() else first.capitalize() or None

    def _detect_display_server(self) -> tuple[Optional[str], Optional[str]]:
        """Return ``(raw_session_type, normalized_display_server)``.

        Uses ``XDG_SESSION_TYPE`` first; if unset, infers Wayland from the
        presence of ``WAYLAND_DISPLAY`` and X11 from ``DISPLAY``.
        """
        raw = (self._environ.get("XDG_SESSION_TYPE") or "").strip().lower()
        if raw in ("wayland", "x11"):
            return raw, raw
        if raw == "tty":
            return raw, None
        # Infer from display sockets.
        if self._environ.get("WAYLAND_DISPLAY"):
            return raw or None, "wayland"
        if self._environ.get("DISPLAY"):
            return raw or None, "x11"
        return raw or None, None

    def _detect_plasma_version(self) -> Optional[str]:
        """Detect the KDE Plasma version.

        Prefers the ``KDE_SESSION_VERSION`` env var (cheap, no subprocess) and
        falls back to parsing ``plasmashell --version`` when the binary exists.
        """
        env_version = (self._environ.get("KDE_SESSION_VERSION") or "").strip()
        # KDE_SESSION_VERSION is the major Plasma generation (e.g. "5" or "6").
        # Try to get a precise version from plasmashell when available.
        if self._which("plasmashell"):
            out = self._run(["plasmashell", "--version"])
            if out.returncode == 0 and out.stdout:
                # Output like: "plasmashell 6.0.4"
                m = re.search(r"plasmashell\s+([0-9][0-9.]*)", out.stdout)
                if m:
                    return m.group(1)
        return env_version or None

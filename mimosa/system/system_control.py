"""System control primitives for MimOSA (M2.2).

This module wraps the command-line tools that adjust low-level system state on a
Linux (Kubuntu 26.04) desktop -- audio volume, screen brightness, Wi-Fi, and
battery -- behind a small, uniform, *defensive* API. The
:class:`~mimosa.skills.system_control.SystemControlSkill` calls these functions
to turn voice commands ("turn the volume up", "set brightness to 40 percent",
"is my wifi on?") into real actions.

Design principles
-----------------
* **Local & private.** Everything is a local ``subprocess`` call or a read of a
  ``/sys`` file. No network, no LLM.
* **Graceful degradation.** Desktops vary wildly in which tools are installed
  (PipeWire's ``wpctl`` vs PulseAudio's ``pactl`` vs ALSA's ``amixer``;
  ``brightnessctl`` vs ``xbacklight``). Each operation probes for an available
  backend with :func:`shutil.which` and returns a structured
  :class:`CommandResult` describing success/failure instead of throwing. If no
  backend exists, the result is a clean "tool not available" message rather
  than a crash.
* **Bounded.** Every subprocess call has a timeout so a hung helper can never
  freeze the voice loop.
* **Testable.** All subprocess execution goes through an injectable ``runner``
  and tool discovery through an injectable ``which`` callable, so the unit
  tests are fully hermetic and never touch real hardware.

Returned values
---------------
Functions return a :class:`CommandResult` with ``success``, a speakable
``message``, and a ``data`` dict carrying structured values (e.g. the new
volume level) for logging/tests.
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger("mimosa.system.system_control")

#: Default timeout (seconds) for any system command.
DEFAULT_TIMEOUT = 5.0

# Injectable types for testing.
Runner = Callable[[Sequence[str]], "RunOutput"]
Which = Callable[[str], Optional[str]]


@dataclass
class RunOutput:
    """The captured result of running a subprocess."""

    returncode: int
    stdout: str = ""
    stderr: str = ""


@dataclass
class CommandResult:
    """Structured outcome of a system-control operation.

    Attributes:
        success: ``True`` if the operation completed.
        message: Short, speakable description of what happened (or why not).
        data: Structured values (e.g. ``{"volume": 50, "muted": False}``).
    """

    success: bool
    message: str
    data: Dict[str, object] = field(default_factory=dict)


def _default_runner(argv: Sequence[str]) -> RunOutput:
    """Run ``argv`` with a fixed timeout and capture its output.

    Never raises for a non-zero exit; surfaces timeouts/not-found as a
    :class:`RunOutput` with a non-zero return code so callers stay uniform.
    """
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


def _clamp(value: int, low: int = 0, high: int = 100) -> int:
    """Clamp ``value`` into the inclusive ``[low, high]`` range."""
    return max(low, min(high, value))


class SystemController:
    """Backend-agnostic controller for volume, brightness, Wi-Fi and battery.

    Args:
        runner: Callable that executes an argv list and returns a
            :class:`RunOutput`. Defaults to a real subprocess runner; tests
            inject a fake.
        which: Callable resolving a tool name to a path or ``None`` (defaults to
            :func:`shutil.which`). Lets tests simulate which backends exist.
        power_supply_root: Base path for battery sysfs nodes (overridable for
            tests). Defaults to ``/sys/class/power_supply``.
    """

    def __init__(
        self,
        *,
        runner: Optional[Runner] = None,
        which: Optional[Which] = None,
        power_supply_root: str = "/sys/class/power_supply",
    ) -> None:
        self._run = runner or _default_runner
        self._which = which or shutil.which
        self._power_root = Path(power_supply_root)

    # ==================================================================
    # Volume
    # ==================================================================

    def _audio_backend(self) -> Optional[str]:
        """Return the first available audio backend, by preference."""
        for tool in ("wpctl", "pactl", "amixer"):
            if self._which(tool):
                return tool
        return None

    def get_volume(self) -> CommandResult:
        """Return the current output volume (0-100) and mute state."""
        backend = self._audio_backend()
        if backend is None:
            return self._no_tool("volume", ("wpctl", "pactl", "amixer"))

        if backend == "wpctl":
            out = self._run(["wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"])
            if out.returncode == 0:
                # Output like: "Volume: 0.45" or "Volume: 0.45 [MUTED]"
                m = re.search(r"Volume:\s*([0-9.]+)", out.stdout)
                if m:
                    vol = int(round(float(m.group(1)) * 100))
                    muted = "MUTED" in out.stdout.upper()
                    return CommandResult(
                        True,
                        f"The volume is at {vol} percent" + (" and muted." if muted else "."),
                        {"volume": vol, "muted": muted, "backend": backend},
                    )
        elif backend == "pactl":
            out = self._run(["pactl", "get-sink-volume", "@DEFAULT_SINK@"])
            if out.returncode == 0:
                m = re.search(r"(\d+)%", out.stdout)
                if m:
                    vol = int(m.group(1))
                    mute = self._run(["pactl", "get-sink-mute", "@DEFAULT_SINK@"])
                    muted = "yes" in mute.stdout.lower()
                    return CommandResult(
                        True,
                        f"The volume is at {vol} percent" + (" and muted." if muted else "."),
                        {"volume": vol, "muted": muted, "backend": backend},
                    )
        else:  # amixer
            out = self._run(["amixer", "get", "Master"])
            if out.returncode == 0:
                m = re.search(r"\[(\d+)%\]", out.stdout)
                muted = "[off]" in out.stdout.lower()
                if m:
                    vol = int(m.group(1))
                    return CommandResult(
                        True,
                        f"The volume is at {vol} percent" + (" and muted." if muted else "."),
                        {"volume": vol, "muted": muted, "backend": backend},
                    )
        return CommandResult(False, "I couldn't read the current volume.", {"backend": backend})

    def set_volume(self, percent: int) -> CommandResult:
        """Set the output volume to an absolute ``percent`` (0-100)."""
        percent = _clamp(int(percent))
        backend = self._audio_backend()
        if backend is None:
            return self._no_tool("volume", ("wpctl", "pactl", "amixer"))

        if backend == "wpctl":
            argv = ["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", f"{percent}%"]
        elif backend == "pactl":
            argv = ["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{percent}%"]
        else:
            argv = ["amixer", "set", "Master", f"{percent}%"]

        out = self._run(argv)
        if out.returncode == 0:
            return CommandResult(
                True,
                f"Volume set to {percent} percent.",
                {"volume": percent, "backend": backend},
            )
        return self._failed("set the volume", out)

    def change_volume(self, delta: int) -> CommandResult:
        """Raise (positive) or lower (negative) the volume by ``delta`` percent."""
        backend = self._audio_backend()
        if backend is None:
            return self._no_tool("volume", ("wpctl", "pactl", "amixer"))

        sign = "+" if delta >= 0 else "-"
        amount = abs(int(delta))
        if backend == "wpctl":
            argv = ["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", f"{amount/100:.2f}{sign}"]
        elif backend == "pactl":
            argv = ["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{sign}{amount}%"]
        else:
            argv = ["amixer", "set", "Master", f"{amount}%{sign}"]

        out = self._run(argv)
        if out.returncode != 0:
            return self._failed("change the volume", out)
        verb = "up" if delta >= 0 else "down"
        # Report the resulting level when we can read it back.
        current = self.get_volume()
        if current.success and "volume" in current.data:
            return CommandResult(
                True,
                f"Turned the volume {verb}. It's now at {current.data['volume']} percent.",
                {"volume": current.data["volume"], "delta": delta, "backend": backend},
            )
        return CommandResult(True, f"Turned the volume {verb}.", {"delta": delta, "backend": backend})

    def set_mute(self, muted: bool) -> CommandResult:
        """Mute or unmute the default output."""
        backend = self._audio_backend()
        if backend is None:
            return self._no_tool("mute", ("wpctl", "pactl", "amixer"))

        if backend == "wpctl":
            argv = ["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", "1" if muted else "0"]
        elif backend == "pactl":
            argv = ["pactl", "set-sink-mute", "@DEFAULT_SINK@", "1" if muted else "0"]
        else:
            argv = ["amixer", "set", "Master", "mute" if muted else "unmute"]

        out = self._run(argv)
        if out.returncode == 0:
            word = "muted" if muted else "unmuted"
            return CommandResult(True, f"Audio {word}.", {"muted": muted, "backend": backend})
        return self._failed("change mute", out)

    def toggle_mute(self) -> CommandResult:
        """Toggle the mute state of the default output."""
        current = self.get_volume()
        if current.success and "muted" in current.data:
            return self.set_mute(not bool(current.data["muted"]))
        # If we couldn't read state, default to muting (safe, reversible).
        return self.set_mute(True)

    # ==================================================================
    # Brightness
    # ==================================================================

    def _brightness_backend(self) -> Optional[str]:
        for tool in ("brightnessctl", "xbacklight"):
            if self._which(tool):
                return tool
        return None

    def get_brightness(self) -> CommandResult:
        """Return the current screen brightness as a percentage (0-100)."""
        backend = self._brightness_backend()
        if backend is None:
            return self._no_tool("brightness", ("brightnessctl", "xbacklight"))

        if backend == "brightnessctl":
            cur = self._run(["brightnessctl", "get"])
            mx = self._run(["brightnessctl", "max"])
            if cur.returncode == 0 and mx.returncode == 0:
                try:
                    current = int(cur.stdout.strip())
                    maximum = int(mx.stdout.strip())
                    pct = _clamp(int(round(current / maximum * 100))) if maximum else 0
                    return CommandResult(
                        True,
                        f"Screen brightness is at {pct} percent.",
                        {"brightness": pct, "backend": backend},
                    )
                except (ValueError, ZeroDivisionError):
                    pass
        else:  # xbacklight
            out = self._run(["xbacklight", "-get"])
            if out.returncode == 0:
                try:
                    pct = _clamp(int(round(float(out.stdout.strip()))))
                    return CommandResult(
                        True,
                        f"Screen brightness is at {pct} percent.",
                        {"brightness": pct, "backend": backend},
                    )
                except ValueError:
                    pass
        return CommandResult(False, "I couldn't read the screen brightness.", {"backend": backend})

    def set_brightness(self, percent: int) -> CommandResult:
        """Set screen brightness to an absolute ``percent`` (0-100)."""
        percent = _clamp(int(percent))
        backend = self._brightness_backend()
        if backend is None:
            return self._no_tool("brightness", ("brightnessctl", "xbacklight"))

        if backend == "brightnessctl":
            argv = ["brightnessctl", "set", f"{percent}%"]
        else:
            argv = ["xbacklight", "-set", str(percent)]

        out = self._run(argv)
        if out.returncode == 0:
            return CommandResult(
                True,
                f"Brightness set to {percent} percent.",
                {"brightness": percent, "backend": backend},
            )
        return self._failed("set the brightness", out)

    def change_brightness(self, delta: int) -> CommandResult:
        """Increase/decrease brightness by ``delta`` percent (clamped 0-100)."""
        backend = self._brightness_backend()
        if backend is None:
            return self._no_tool("brightness", ("brightnessctl", "xbacklight"))

        amount = abs(int(delta))
        if backend == "brightnessctl":
            # brightnessctl uses +/- suffix notation.
            arg = f"{amount}%{'+' if delta >= 0 else '-'}"
            argv = ["brightnessctl", "set", arg]
        else:
            flag = "-inc" if delta >= 0 else "-dec"
            argv = ["xbacklight", flag, str(amount)]

        out = self._run(argv)
        if out.returncode != 0:
            return self._failed("change the brightness", out)
        verb = "increased" if delta >= 0 else "decreased"
        current = self.get_brightness()
        if current.success and "brightness" in current.data:
            return CommandResult(
                True,
                f"Brightness {verb}. It's now at {current.data['brightness']} percent.",
                {"brightness": current.data["brightness"], "delta": delta, "backend": backend},
            )
        return CommandResult(True, f"Brightness {verb}.", {"delta": delta, "backend": backend})

    # ==================================================================
    # Wi-Fi
    # ==================================================================

    def _wifi_available(self) -> bool:
        return bool(self._which("nmcli"))

    def get_wifi_status(self) -> CommandResult:
        """Report whether Wi-Fi is enabled and which network is connected."""
        if not self._wifi_available():
            return self._no_tool("wifi", ("nmcli",))

        radio = self._run(["nmcli", "radio", "wifi"])
        if radio.returncode != 0:
            return self._failed("check Wi-Fi status", radio)
        enabled = radio.stdout.strip().lower().startswith("enabled")
        if not enabled:
            return CommandResult(True, "Wi-Fi is turned off.", {"enabled": False})

        # Find the active connection name, if any.
        conn = self._run(["nmcli", "-t", "-f", "ACTIVE,SSID", "dev", "wifi"])
        ssid = None
        if conn.returncode == 0:
            for line in conn.stdout.splitlines():
                if line.startswith("yes:"):
                    ssid = line.split(":", 1)[1].strip() or None
                    break
        if ssid:
            return CommandResult(
                True, f"Wi-Fi is on and connected to {ssid}.",
                {"enabled": True, "ssid": ssid},
            )
        return CommandResult(True, "Wi-Fi is on but not connected to a network.", {"enabled": True, "ssid": None})

    def set_wifi(self, enabled: bool) -> CommandResult:
        """Turn Wi-Fi radio on or off."""
        if not self._wifi_available():
            return self._no_tool("wifi", ("nmcli",))
        argv = ["nmcli", "radio", "wifi", "on" if enabled else "off"]
        out = self._run(argv)
        if out.returncode == 0:
            word = "on" if enabled else "off"
            return CommandResult(True, f"Wi-Fi turned {word}.", {"enabled": enabled})
        return self._failed("change Wi-Fi", out)

    # ==================================================================
    # Battery
    # ==================================================================

    def get_battery(self) -> CommandResult:
        """Return battery charge percentage and charging state from sysfs.

        Reads ``/sys/class/power_supply/BAT*`` directly (no external tool), so
        it works headless. On desktops without a battery it reports that
        clearly.
        """
        try:
            if not self._power_root.is_dir():
                return CommandResult(
                    False, "I couldn't find any battery information on this system.",
                    {"present": False},
                )
            batteries = sorted(
                p for p in self._power_root.iterdir() if p.name.startswith("BAT")
            )
        except OSError as exc:  # pragma: no cover - unusual FS error
            return CommandResult(False, f"I couldn't read battery information: {exc}.", {"present": False})

        if not batteries:
            return CommandResult(
                False, "This system doesn't appear to have a battery.", {"present": False},
            )

        bat = batteries[0]
        capacity = self._read_sysfs_int(bat / "capacity")
        status = self._read_sysfs_text(bat / "status")
        if capacity is None:
            return CommandResult(False, "I couldn't read the battery level.", {"present": True})

        charging = (status or "").lower() == "charging"
        full = (status or "").lower() == "full"
        if full:
            msg = "The battery is fully charged."
        elif charging:
            msg = f"The battery is at {capacity} percent and charging."
        else:
            msg = f"The battery is at {capacity} percent."
        return CommandResult(
            True,
            msg,
            {"present": True, "percent": capacity, "status": status, "charging": charging},
        )

    @staticmethod
    def _read_sysfs_int(path: Path) -> Optional[int]:
        try:
            return int(path.read_text().strip())
        except (OSError, ValueError):
            return None

    @staticmethod
    def _read_sysfs_text(path: Path) -> Optional[str]:
        try:
            return path.read_text().strip()
        except OSError:
            return None

    # ==================================================================
    # Shared helpers
    # ==================================================================

    def _no_tool(self, what: str, tools: Tuple[str, ...]) -> CommandResult:
        """Build a graceful "no backend installed" result."""
        joined = " or ".join(tools)
        return CommandResult(
            False,
            f"I can't control the {what} because no supported tool "
            f"({joined}) is installed on this system.",
            {"available": False, "needed": list(tools)},
        )

    @staticmethod
    def _failed(action: str, out: RunOutput) -> CommandResult:
        """Build a failure result from a non-zero :class:`RunOutput`."""
        detail = (out.stderr or out.stdout or "").strip().splitlines()
        hint = f" ({detail[0]})" if detail else ""
        return CommandResult(False, f"I tried to {action} but it didn't work{hint}.", {"returncode": out.returncode})

"""KDE Plasma integration for MimOSA (M2.3).

On Kubuntu 26.04 the desktop is KDE Plasma, which exposes a rich set of D-Bus
services for window management (KWin), notifications, virtual desktops,
activities, and device pairing (KDE Connect). This module wraps the handful of
calls MimOSA needs behind a small, defensive :class:`KDEIntegration` API so the
assistant can, for example, pop a desktop notification ("your timer is done")
or report which windows are open -- *when running on KDE* -- and degrade
cleanly to a spoken "that's not available on this system" everywhere else.

Transport
---------
Two backends are supported, chosen automatically:

* **dbus-python** (``import dbus``) when the library is installed -- the fast,
  native path.
* **qdbus** subprocess fallback when only the CLI tool exists.

If neither is available (e.g. this dev VM, or a GNOME box), every method returns
a structured :class:`KDEResult` with ``available=False`` and a friendly message
instead of raising.

Design principles
-----------------
* **Local & private.** Pure local IPC; nothing leaves the machine, no LLM.
* **Graceful degradation.** Non-KDE, no-D-Bus, and missing-service cases all
  return clean results -- never an exception into the voice loop.
* **Bounded & testable.** Subprocess calls are timed out, and both the D-Bus
  client and the command runner are injectable for hermetic tests.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence

logger = logging.getLogger("mimosa.system.kde_integration")

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


@dataclass
class KDEResult:
    """Structured outcome of a KDE integration operation.

    Attributes:
        success: ``True`` if the operation completed.
        available: ``False`` when KDE/D-Bus isn't present (distinct from a
            genuine failure on a KDE box).
        message: Short, speakable description.
        data: Structured values (e.g. the list of virtual desktops).
    """

    success: bool
    available: bool
    message: str
    data: Dict[str, Any] = field(default_factory=dict)


class KDEIntegration:
    """Talk to KDE Plasma over D-Bus, with a non-KDE safe fallback.

    Args:
        dbus_module: The ``dbus`` module (or a fake) for the native path.
            ``"auto"`` (default) imports it if available, else ``None``.
        runner: argv -> :class:`RunOutput` executor for the ``qdbus`` fallback.
        which: tool-name -> path resolver (defaults to :func:`shutil.which`).
        is_kde: Optional explicit override of KDE-session detection (the
            profiler passes ``SystemProfile.is_kde``). When ``None`` the class
            infers availability purely from the presence of a transport.
    """

    def __init__(
        self,
        *,
        dbus_module: Any = "auto",
        runner: Optional[Runner] = None,
        which: Optional[Which] = None,
        is_kde: Optional[bool] = None,
    ) -> None:
        if dbus_module == "auto":
            try:
                import dbus  # type: ignore

                dbus_module = dbus
            except Exception:
                dbus_module = None
        self._dbus = dbus_module
        self._run = runner or _default_runner
        self._which = which or shutil.which
        self._is_kde = is_kde
        self._qdbus_tool: Optional[str] = None  # resolved lazily

    # ------------------------------------------------------------------
    # Availability
    # ------------------------------------------------------------------

    def _resolve_qdbus(self) -> Optional[str]:
        """Return the available qdbus-style CLI tool name, if any."""
        if self._qdbus_tool is not None:
            return self._qdbus_tool or None
        for tool in ("qdbus6", "qdbus-qt6", "qdbus"):
            if self._which(tool):
                self._qdbus_tool = tool
                return tool
        self._qdbus_tool = ""  # sentinel: "checked, none found"
        return None

    @property
    def has_transport(self) -> bool:
        """Whether *some* D-Bus transport (native or qdbus) is usable."""
        return self._dbus is not None or self._resolve_qdbus() is not None

    @property
    def available(self) -> bool:
        """Whether KDE integration can actually do anything here.

        Requires a transport and -- when the session type is known -- a KDE
        session. If ``is_kde`` was not provided we trust the transport alone.
        """
        if not self.has_transport:
            return False
        if self._is_kde is None:
            return True
        return bool(self._is_kde)

    def _unavailable(self, feature: str) -> KDEResult:
        if self._is_kde is False:
            reason = "this isn't a KDE Plasma session"
        elif not self.has_transport:
            reason = "no D-Bus connection to KDE is available"
        else:  # pragma: no cover - defensive
            reason = "KDE integration isn't available"
        return KDEResult(
            success=False,
            available=False,
            message=f"I can't {feature} because {reason}.",
            data={"available": False},
        )

    # ------------------------------------------------------------------
    # qdbus helper
    # ------------------------------------------------------------------

    def _qdbus_call(self, service: str, path: str, method: str, *args: str) -> Optional[str]:
        """Invoke a method via the qdbus CLI; return stdout or ``None``."""
        tool = self._resolve_qdbus()
        if not tool:
            return None
        argv = [tool, service, path, method, *args]
        out = self._run(argv)
        if out.returncode == 0:
            return out.stdout
        logger.debug("qdbus call failed: %s -> rc=%s %s", argv, out.returncode, out.stderr)
        return None

    # ------------------------------------------------------------------
    # Notifications
    # ------------------------------------------------------------------

    def send_notification(
        self,
        title: str,
        body: str = "",
        *,
        app_name: str = "MimOSA",
        timeout_ms: int = 5000,
    ) -> KDEResult:
        """Show a desktop notification via the freedesktop Notifications API.

        Works on KDE (KNotifications) and indeed any freedesktop-compliant
        desktop. Returns ``available=False`` only when there is no transport.
        """
        if not self.has_transport:
            return self._unavailable("show a notification")

        service = "org.freedesktop.Notifications"
        path = "/org/freedesktop/Notifications"
        iface = "org.freedesktop.Notifications"

        # Native dbus-python path.
        if self._dbus is not None:
            try:
                bus = self._dbus.SessionBus()
                proxy = bus.get_object(service, path)
                notify = proxy.get_dbus_method("Notify", iface)
                notify(app_name, 0, "", title, body, [], {}, int(timeout_ms))
                return KDEResult(True, True, "Notification sent.", {"title": title})
            except Exception as exc:  # noqa: BLE001 - D-Bus runtime errors vary
                logger.debug("dbus Notify failed: %s", exc)
                return KDEResult(False, True, "I couldn't show that notification.", {"error": str(exc)})

        # qdbus fallback.
        out = self._qdbus_call(
            service, path, f"{iface}.Notify",
            app_name, "0", "", title, body, "", "", str(int(timeout_ms)),
        )
        if out is not None:
            return KDEResult(True, True, "Notification sent.", {"title": title})
        return KDEResult(False, True, "I couldn't show that notification.", {})

    # ------------------------------------------------------------------
    # Virtual desktops (KWin)
    # ------------------------------------------------------------------

    def get_virtual_desktops(self) -> KDEResult:
        """Report the number/names of KWin virtual desktops."""
        if not self.available:
            return self._unavailable("list your virtual desktops")

        service = "org.kde.KWin"
        path = "/VirtualDesktopManager"
        iface = "org.kde.KWin.VirtualDesktopManager"

        if self._dbus is not None:
            try:
                bus = self._dbus.SessionBus()
                proxy = bus.get_object(service, path)
                props = self._dbus.Interface(proxy, "org.freedesktop.DBus.Properties")
                count = int(props.Get(iface, "count"))
                return KDEResult(
                    True, True,
                    f"You have {count} virtual desktop" + ("s." if count != 1 else "."),
                    {"count": count},
                )
            except Exception as exc:  # noqa: BLE001
                logger.debug("dbus virtual desktop query failed: %s", exc)
                return KDEResult(False, True, "I couldn't read your virtual desktops.", {"error": str(exc)})

        out = self._qdbus_call(service, path, f"{iface}.count")
        if out is not None:
            try:
                count = int(out.strip())
                return KDEResult(
                    True, True,
                    f"You have {count} virtual desktop" + ("s." if count != 1 else "."),
                    {"count": count},
                )
            except ValueError:
                pass
        return KDEResult(False, True, "I couldn't read your virtual desktops.", {})

    # ------------------------------------------------------------------
    # Windows (KWin scripting / tasks)
    # ------------------------------------------------------------------

    def list_windows(self) -> KDEResult:
        """List currently open windows reported by KWin.

        KWin doesn't expose a stable, simple "list windows" D-Bus method across
        versions, so this uses the ``org.kde.KWin`` ``queryWindowInfo``-style
        path when present and otherwise reports gracefully. On systems without
        KDE this returns ``available=False``.
        """
        if not self.available:
            return self._unavailable("list your open windows")

        # Best-effort: many setups expose the Plasma "tasks" via the
        # org.kde.plasma.taskmanager interface only through scripting. We keep
        # this conservative and return a clear, honest message rather than
        # guessing an unstable API.
        out = self._qdbus_call("org.kde.KWin", "/KWin", "org.kde.KWin.supportInformation")
        if self._dbus is None and out is None:
            return KDEResult(
                False, True,
                "I couldn't query the window manager just now.",
                {},
            )
        return KDEResult(
            True, True,
            "Window listing requires a KWin script; I can manage notifications "
            "and virtual desktops directly. Detailed window enumeration isn't "
            "exposed over a stable interface.",
            {"note": "kwin-window-list-unstable"},
        )

    # ------------------------------------------------------------------
    # KDE Connect
    # ------------------------------------------------------------------

    def get_kde_connect_devices(self) -> KDEResult:
        """List paired/reachable KDE Connect devices."""
        if not self.available:
            return self._unavailable("check KDE Connect devices")

        service = "org.kde.kdeconnect"
        path = "/modules/kdeconnect"
        iface = "org.kde.kdeconnect.daemon"

        if self._dbus is not None:
            try:
                bus = self._dbus.SessionBus()
                proxy = bus.get_object(service, path)
                daemon = self._dbus.Interface(proxy, iface)
                ids = list(daemon.devices(True, True))  # onlyReachable, onlyPaired
                names = []
                for dev_id in ids:
                    try:
                        dev_proxy = bus.get_object(service, f"/modules/kdeconnect/devices/{dev_id}")
                        dev_props = self._dbus.Interface(dev_proxy, "org.freedesktop.DBus.Properties")
                        names.append(str(dev_props.Get("org.kde.kdeconnect.device", "name")))
                    except Exception:  # noqa: BLE001
                        names.append(str(dev_id))
                return self._kde_connect_result(names)
            except Exception as exc:  # noqa: BLE001 - daemon may be absent
                logger.debug("KDE Connect query failed: %s", exc)
                return KDEResult(
                    False, True,
                    "I couldn't reach KDE Connect. It may not be running.",
                    {"error": str(exc)},
                )

        out = self._qdbus_call(service, path, f"{iface}.devices", "true", "true")
        if out is not None:
            ids = [l.strip() for l in out.splitlines() if l.strip()]
            return self._kde_connect_result(ids)
        return KDEResult(
            False, True,
            "I couldn't reach KDE Connect. It may not be running.",
            {},
        )

    @staticmethod
    def _kde_connect_result(names: List[str]) -> KDEResult:
        if not names:
            return KDEResult(True, True, "No KDE Connect devices are currently connected.", {"devices": []})
        joined = ", ".join(names)
        return KDEResult(
            True, True,
            f"You have {len(names)} KDE Connect device"
            + (f"s connected: {joined}." if len(names) != 1 else f" connected: {joined}."),
            {"devices": names},
        )

    # ------------------------------------------------------------------
    # Capability report
    # ------------------------------------------------------------------

    def capabilities(self) -> Dict[str, Any]:
        """A small dict describing what this integration can do here."""
        return {
            "transport": (
                "dbus-python" if self._dbus is not None
                else (self._resolve_qdbus() or None)
            ),
            "available": self.available,
            "is_kde_session": self._is_kde,
        }

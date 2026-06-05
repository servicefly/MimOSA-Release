"""Window lifecycle, position persistence & multi-monitor logic (M3.1).

Two concerns live here, deliberately separated so the tricky parts are testable
without a display:

1. **Pure geometry/persistence logic** -- :class:`MonitorInfo`, position
   clamping, monitor selection, and save/restore through
   :class:`~mimosa.ui.ui_config.UIConfig`. No GTK imports; unit-tested directly.
2. **GTK glue** -- :class:`WindowManager` applies that logic to a real
   ``Gtk``/``Gdk`` window when a display is available, querying monitors and
   honoring the saved position. Every GTK call is guarded so a missing/odd
   backend degrades to "center it" rather than crashing.

Wayland note: many Wayland compositors do **not** allow clients to set their own
absolute position. We therefore *persist* position best-effort and *restore* it
where the backend permits (X11, some compositors); on strict Wayland the saved
position is kept for next time but the compositor may place the window itself.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import List, Optional, Tuple

from mimosa.ui.ui_config import UIConfig

logger = logging.getLogger(__name__)


@dataclass
class MonitorInfo:
    """A monitor's geometry in logical pixels."""

    x: int
    y: int
    width: int
    height: int

    @property
    def right(self) -> int:
        return self.x + self.width

    @property
    def bottom(self) -> int:
        return self.y + self.height

    def center(self, size: int) -> Tuple[int, int]:
        """Top-left position that centers a ``size`` x ``size`` window on me."""
        return (
            self.x + (self.width - size) // 2,
            self.y + (self.height - size) // 2,
        )


def select_monitor(monitors: List[MonitorInfo], index: int) -> Optional[MonitorInfo]:
    """Return ``monitors[index]`` if valid, else the first, else ``None``."""
    if not monitors:
        return None
    if 0 <= index < len(monitors):
        return monitors[index]
    return monitors[0]


def clamp_to_monitor(
    x: int, y: int, size: int, monitor: MonitorInfo, margin: int = 0
) -> Tuple[int, int]:
    """Clamp a window's top-left ``(x, y)`` so a ``size`` window stays on ``monitor``.

    Keeps at least ``margin`` px inside each edge. If the window is larger than
    the monitor, it is pinned to the top-left corner.
    """
    max_x = monitor.right - size - margin
    max_y = monitor.bottom - size - margin
    min_x = monitor.x + margin
    min_y = monitor.y + margin
    cx = min(max(x, min_x), max(min_x, max_x))
    cy = min(max(y, min_y), max(min_y, max_y))
    return cx, cy


def resolve_position(
    config: UIConfig, monitors: List[MonitorInfo]
) -> Optional[Tuple[int, int]]:
    """Compute the window's startup position from saved config + monitor layout.

    * No monitors known -> ``None`` (let the backend place it).
    * Saved ``pos_x/pos_y`` -> clamped onto the preferred monitor.
    * No saved position -> centered on the preferred monitor.
    """
    monitor = select_monitor(monitors, config.monitor)
    if monitor is None:
        return None
    if config.pos_x is not None and config.pos_y is not None:
        return clamp_to_monitor(config.pos_x, config.pos_y, config.size, monitor)
    return monitor.center(config.size)


class WindowManager:
    """Applies geometry/persistence logic to a real GTK window when present.

    Args:
        config: The :class:`UIConfig` to read size/monitor from and write
            position back to.
        config_path: Where to persist the config on :meth:`save_position`.
    """

    def __init__(self, config: UIConfig, config_path=None) -> None:
        self.config = config
        self.config_path = config_path

    # -- monitor discovery (GTK-guarded) -----------------------------------

    def query_monitors(self, display=None) -> List[MonitorInfo]:
        """Enumerate monitors via Gdk, returning ``[]`` if unavailable.

        ``display`` may be injected (a Gdk.Display-like object) for tests;
        otherwise the default display is used. Any failure yields ``[]`` so the
        caller falls back to backend placement.
        """
        try:
            if display is None:
                import gi

                gi.require_version("Gdk", "4.0")
                from gi.repository import Gdk

                display = Gdk.Display.get_default()
            if display is None:
                return []
            monitors_model = display.get_monitors()
            out: List[MonitorInfo] = []
            n = monitors_model.get_n_items()
            for i in range(n):
                mon = monitors_model.get_item(i)
                geo = mon.get_geometry()
                out.append(MonitorInfo(geo.x, geo.y, geo.width, geo.height))
            return out
        except Exception as exc:  # pragma: no cover - depends on live display
            logger.debug("Monitor query failed (%s); no monitor info", exc)
            return []

    # -- position persistence ----------------------------------------------

    def save_position(self, x: int, y: int, monitor_index: Optional[int] = None) -> bool:
        """Persist a new window position (and optional monitor) to config.

        Returns the result of :meth:`UIConfig.save` (``True`` on success).
        """
        self.config.pos_x = int(x)
        self.config.pos_y = int(y)
        if monitor_index is not None:
            self.config.monitor = int(monitor_index)
        return self.config.save(self.config_path)

    def startup_position(self, monitors: Optional[List[MonitorInfo]] = None):
        """Return the (x, y) to place the window at startup, or ``None``."""
        mons = monitors if monitors is not None else self.query_monitors()
        return resolve_position(self.config, mons)

    def apply_to_window(self, window, monitors: Optional[List[MonitorInfo]] = None) -> None:
        """Best-effort: size the GTK ``window`` and move it to the saved spot.

        Guards every GTK call. On Wayland, moving may be a no-op (the compositor
        decides) -- that's expected and not an error.
        """
        size = self.config.size
        try:
            window.set_default_size(size, size)
        except Exception as exc:  # pragma: no cover - GTK runtime only
            logger.debug("set_default_size failed: %s", exc)

        pos = self.startup_position(monitors)
        if pos is None:
            return
        # GTK4 has no portable client-side move; X11 backends expose it via the
        # surface. We try, but never treat failure as fatal.
        try:
            surface = window.get_surface()
            if surface is not None and hasattr(surface, "move"):
                surface.move(pos[0], pos[1])  # pragma: no cover - backend specific
        except Exception as exc:  # pragma: no cover - GTK runtime only
            logger.debug("Window move not supported by backend: %s", exc)

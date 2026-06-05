"""System-tray (status icon) shell (M4.3) -- a thin view over TrayController.

All decision-making lives in the GTK-free
:class:`mimosa.ui.tray_logic.TrayController`; this module only builds the actual
``Gtk.PopoverMenu`` / status-icon (preferring AppIndicator when present) and
forwards activations back to the controller.

GTK must exist at *class definition* time, so the import is guarded: on a
headless machine :data:`HAS_GTK` is ``False`` and :data:`SystemTray` is
``None``.  Callers should use :func:`create_system_tray`, which returns ``None``
when no display / tray back-end is available.
"""

from __future__ import annotations

import logging
from typing import Optional

from mimosa.ui.tray_logic import (
    KIND_SEPARATOR,
    KIND_TOGGLE,
    TrayController,
)

logger = logging.getLogger(__name__)

try:  # GTK is required to *define* the widget class.
    import gi

    gi.require_version("Gtk", "4.0")
    from gi.repository import Gio, GLib, Gtk

    HAS_GTK = True
except Exception:  # pragma: no cover - headless import path
    HAS_GTK = False

# AppIndicator is optional; the menu still works without it via a fallback.
HAS_APPINDICATOR = False
if HAS_GTK:  # pragma: no cover - environment dependent
    for _ver, _name in (("0.1", "AyatanaAppIndicator3"), ("0.1", "AppIndicator3")):
        try:
            gi.require_version(_name, _ver)
            from gi.repository import AyatanaAppIndicator3 as AppIndicator3  # type: ignore  # noqa: E501
            HAS_APPINDICATOR = True
            break
        except Exception:
            continue


if HAS_GTK:

    class SystemTray:  # pragma: no cover - exercised only under a desktop session
        """Renders the tray menu from a :class:`TrayController`."""

        def __init__(self, controller: Optional[TrayController] = None) -> None:
            self.controller = controller or TrayController()
            self._indicator = None
            self._build()

        def _build(self) -> None:
            menu = Gio.Menu()
            for item in self.controller.menu_items():
                if item.kind == KIND_SEPARATOR:
                    continue  # Gio.Menu sections handle separation; keep simple
                menu.append(item.label, f"tray.{item.item_id}")
            self._menu_model = menu
            if HAS_APPINDICATOR:
                self._indicator = AppIndicator3.Indicator.new(
                    "mimosa",
                    self.controller.icon_name(),
                    AppIndicator3.IndicatorCategory.APPLICATION_STATUS,
                )
                self._indicator.set_status(AppIndicator3.IndicatorStatus.ACTIVE)
            self.refresh()

        def refresh(self) -> None:
            """Re-sync icon / tooltip with the controller state."""
            if self._indicator is not None:
                self._indicator.set_icon_full(
                    self.controller.icon_name(), self.controller.tooltip()
                )

        def activate(self, item_id: str) -> bool:
            handled = self.controller.activate(item_id)
            self.refresh()
            return handled

else:  # headless
    SystemTray = None  # type: ignore


def create_system_tray(
    controller: Optional[TrayController] = None,
) -> Optional["SystemTray"]:
    """Create a :class:`SystemTray` if a GTK display is available, else ``None``.

    Always safe to call; returns ``None`` on headless machines so callers can
    treat the tray as an optional enhancement.
    """
    if not HAS_GTK:
        logger.info("GTK unavailable; system tray disabled.")
        return None
    try:  # pragma: no cover - GTK-only path
        return SystemTray(controller)
    except Exception:  # pragma: no cover - defensive
        logger.exception("Failed to create system tray; continuing without it.")
        return None

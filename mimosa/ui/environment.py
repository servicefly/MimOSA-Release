"""GUI environment detection & GTK availability (M3.1).

Centralizes the "can we show a GUI?" decision so every UI module agrees. Used by
:mod:`mimosa.ui.app` to decide between launching the GTK avatar and falling back
to headless/CLI mode. Pure and import-safe: it never imports GTK at module load
(only a cheap, guarded probe inside :func:`gtk_available`).
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


def has_display() -> bool:
    """True if an X11 (``DISPLAY``) or Wayland (``WAYLAND_DISPLAY``) server is set."""
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def gtk_available() -> bool:
    """True if PyGObject + GTK 4 can be imported in this interpreter.

    The import is fully guarded so this returns ``False`` (never raises) on a
    machine without PyGObject or the GTK 4 typelib.
    """
    try:
        import gi  # noqa: F401

        gi.require_version("Gtk", "4.0")
        from gi.repository import Gtk  # noqa: F401

        return True
    except Exception:  # pragma: no cover - depends on host packages
        return False


def is_gui_available() -> bool:
    """True only if BOTH a display server is present AND GTK 4 is importable.

    This is the canonical check the application uses to decide whether to start
    the graphical avatar. When it returns ``False``, MimOSA runs headless
    (voice/CLI only) -- no GTK is imported.
    """
    return has_display() and gtk_available()


def describe_environment() -> str:
    """Human-readable one-liner about GUI readiness (for logs / --check)."""
    disp = "yes" if has_display() else "no"
    gtk = "yes" if gtk_available() else "no"
    ready = "GUI available" if is_gui_available() else "headless (CLI/voice only)"
    return f"display={disp} gtk4={gtk} -> {ready}"

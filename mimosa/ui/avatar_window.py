"""GTK4 circular, transparent, always-on-top avatar window (M3.1).

This module subclasses GTK widgets, so GTK must exist at *class definition*
time. To keep the package importable on headless machines, the GTK import is
guarded: when PyGObject/GTK 4 is unavailable, :data:`HAS_GTK` is ``False`` and
:data:`AvatarWindow` is ``None``. Callers must check
:func:`mimosa.ui.environment.is_gui_available` (or :data:`HAS_GTK`) before
constructing a window -- the application entry point does exactly that.

The window:

* is **frameless** and **transparent** (only the circular avatar is painted),
* stays **always-on-top** where the backend allows,
* is **draggable** (drag to move; the new position is persisted),
* shows a **right-click context menu** (Settings, Quit),
* **hides on Escape**,
* drives the :class:`~mimosa.ui.avatar_renderer.AvatarRenderer` from a
  GLib animation timer and repaints each frame.
"""

from __future__ import annotations

import logging
from typing import Callable, Optional

from mimosa.ui.avatar_renderer import AvatarRenderer
from mimosa.ui.state_bridge import UIState
from mimosa.ui.ui_config import UIConfig

logger = logging.getLogger(__name__)

try:  # GTK is required to *define* the widget class.
    import gi

    gi.require_version("Gtk", "4.0")
    from gi.repository import Gdk, GLib, Gtk

    HAS_GTK = True
except Exception:  # pragma: no cover - headless import path
    HAS_GTK = False


_CSS = b"""
.mimosa-avatar, .mimosa-avatar window, window.mimosa-avatar {
    background-color: transparent;
}
"""


if HAS_GTK:

    class AvatarWindow(Gtk.ApplicationWindow):
        """The on-screen avatar window.

        Args:
            application: The owning ``Gtk.Application``.
            config: UI preferences (size, opacity, theme, animation).
            renderer: Optional pre-built renderer (one is created from config
                otherwise) -- injectable for tests.
            on_quit: Callback invoked when the user picks "Quit".
            on_settings: Callback invoked when the user picks "Settings".
            on_move: Callback ``(x, y)`` invoked after a drag, for persistence.
        """

        def __init__(
            self,
            application=None,
            config: Optional[UIConfig] = None,
            renderer: Optional[AvatarRenderer] = None,
            on_quit: Optional[Callable[[], None]] = None,
            on_settings: Optional[Callable[[], None]] = None,
            on_move: Optional[Callable[[int, int], None]] = None,
        ) -> None:
            super().__init__(application=application)
            self.config = config or UIConfig()
            self.on_quit = on_quit
            self.on_settings = on_settings
            self.on_move = on_move

            self.renderer = renderer or AvatarRenderer(
                theme=self.config.theme,
                animation_style=self.config.animation_style,
                animation_speed=self.config.animation_speed,
                animations_enabled=self.config.animations_enabled,
            )

            self._anim_source = None
            self._last_frame_ns = None
            self._drag_start = (0, 0)

            self._build_window()
            self._build_drawing_area()
            self._install_gestures()
            self._install_menu()
            self._apply_css()
            self.start_animation()

        # -- construction --------------------------------------------------

        def _build_window(self) -> None:
            self.set_decorated(False)             # frameless
            self.set_resizable(False)
            self.add_css_class("mimosa-avatar")
            self.set_default_size(self.config.size, self.config.size)
            try:
                self.set_opacity(self.config.opacity)
            except Exception:  # pragma: no cover - backend dependent
                pass
            # Best-effort always-on-top (not all GTK4 backends expose this).
            for attr in ("set_keep_above",):
                fn = getattr(self, attr, None)
                if callable(fn) and self.config.always_on_top:
                    try:
                        fn(True)  # pragma: no cover - X11 only
                    except Exception:
                        pass

        def _build_drawing_area(self) -> None:
            self.area = Gtk.DrawingArea()
            self.area.set_content_width(self.config.size)
            self.area.set_content_height(self.config.size)
            self.area.set_draw_func(self._on_draw)
            self.set_child(self.area)

        def _install_gestures(self) -> None:
            # Drag to move.
            drag = Gtk.GestureDrag()
            drag.connect("drag-begin", self._on_drag_begin)
            drag.connect("drag-update", self._on_drag_update)
            drag.connect("drag-end", self._on_drag_end)
            self.area.add_controller(drag)

            # Right-click -> context menu.
            click = Gtk.GestureClick()
            click.set_button(3)  # right mouse button
            click.connect("pressed", self._on_right_click)
            self.area.add_controller(click)

            # Escape -> hide.
            keys = Gtk.EventControllerKey()
            keys.connect("key-pressed", self._on_key)
            self.add_controller(keys)

        def _install_menu(self) -> None:
            menu = Gtk.PopoverMenu()
            self._menu = menu
            menu.set_parent(self.area)
            from gi.repository import Gio

            model = Gio.Menu()
            model.append("Settings", "win.settings")
            model.append("Hide", "win.hide")
            model.append("Quit", "win.quit")
            menu.set_menu_model(model)

            self._add_action("settings", self._action_settings)
            self._add_action("hide", lambda *a: self.set_visible(False))
            self._add_action("quit", self._action_quit)

        def _add_action(self, name: str, cb) -> None:
            from gi.repository import Gio

            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", cb)
            self.add_action(action)

        def _apply_css(self) -> None:
            try:
                provider = Gtk.CssProvider()
                provider.load_from_data(_CSS)
                Gtk.StyleContext.add_provider_for_display(
                    Gdk.Display.get_default(),
                    provider,
                    Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
                )
            except Exception as exc:  # pragma: no cover - backend dependent
                logger.debug("CSS load failed: %s", exc)

        # -- drawing & animation -------------------------------------------

        def _on_draw(self, area, cr, width, height) -> None:
            try:
                self.renderer.draw(cr, width, height)
            except Exception as exc:  # pragma: no cover - defensive
                logger.error("Avatar draw failed: %s", exc)

        def start_animation(self) -> None:
            """Start the frame timer (idempotent)."""
            if self._anim_source is not None:
                return
            fps = max(5, min(60, self.config.target_fps))
            interval = int(1000 / fps)
            self._last_frame_ns = GLib.get_monotonic_time()
            self._anim_source = GLib.timeout_add(interval, self._on_frame)

        def stop_animation(self) -> None:
            """Stop the frame timer (safe if not running)."""
            if self._anim_source is not None:
                try:
                    GLib.source_remove(self._anim_source)
                except Exception:  # pragma: no cover
                    pass
                self._anim_source = None

        def _on_frame(self) -> bool:
            now = GLib.get_monotonic_time()
            dt = (now - (self._last_frame_ns or now)) / 1_000_000.0
            self._last_frame_ns = now
            self.renderer.tick(dt)
            self.area.queue_draw()
            return True  # keep firing

        # -- public state API ----------------------------------------------

        def set_state(self, ui_state) -> None:
            """Update the avatar's visual state (safe to call from the main thread)."""
            self.renderer.set_state(ui_state)
            self.area.queue_draw()

        def set_audio_level(self, level: float) -> None:
            """Forward an audio level to the renderer for reactive speaking."""
            self.renderer.set_audio_level(level)

        # -- event handlers -------------------------------------------------

        def _on_drag_begin(self, gesture, start_x, start_y) -> None:
            self._drag_start = (start_x, start_y)

        def _on_drag_update(self, gesture, offset_x, offset_y) -> None:
            # Live window movement during drag is backend-specific in GTK4;
            # we record the offset and commit on drag-end via on_move.
            self._drag_offset = (offset_x, offset_y)

        def _on_drag_end(self, gesture, offset_x, offset_y) -> None:
            if self.on_move is None:
                return
            try:
                surface = self.get_surface()
                # Best-effort: report a new position derived from the offset.
                base_x = self.config.pos_x or 0
                base_y = self.config.pos_y or 0
                new_x = int(base_x + offset_x)
                new_y = int(base_y + offset_y)
                self.on_move(new_x, new_y)
            except Exception as exc:  # pragma: no cover - backend dependent
                logger.debug("Drag-end position commit failed: %s", exc)

        def _on_right_click(self, gesture, n_press, x, y) -> None:
            try:
                rect = Gdk.Rectangle()
                rect.x, rect.y, rect.width, rect.height = int(x), int(y), 1, 1
                self._menu.set_pointing_to(rect)
                self._menu.popup()
            except Exception as exc:  # pragma: no cover - backend dependent
                logger.debug("Context menu popup failed: %s", exc)

        def _on_key(self, controller, keyval, keycode, state) -> bool:
            if keyval == Gdk.KEY_Escape:
                self.set_visible(False)
                return True
            return False

        def _action_settings(self, *args) -> None:
            if self.on_settings is not None:
                self.on_settings()

        def _action_quit(self, *args) -> None:
            self.stop_animation()
            if self.on_quit is not None:
                self.on_quit()
            else:
                self.close()

else:  # pragma: no cover - headless: no GTK to subclass
    AvatarWindow = None

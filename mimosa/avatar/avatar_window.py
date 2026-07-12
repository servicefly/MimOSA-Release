"""GTK4 character-avatar window for the v2.0.0 avatar system (Milestone 8.1).

This is the new home of the animated *character* avatar -- the head-and-
shoulders sprite that replaces the classic listening circle when the user opts
in. It lives alongside the legacy :class:`mimosa.ui.avatar_window.AvatarWindow`
(the circle) rather than replacing it, so existing users keep the circle until
they choose an avatar.

Structurally it mirrors the circle window: a frameless, transparent, always-on-
top, draggable GTK4 window driven by a GLib animation timer, with a right-click
context menu and Escape-to-hide. The difference is what it paints -- it drives a
:class:`mimosa.avatar.base_renderer.BaseAvatarRenderer` (a
:class:`~mimosa.avatar.renderer_2d.Sprite2DRenderer` by default) which draws the
placeholder character sprite instead of the circle.

As with the circle window, GTK is imported at *class definition* time, so the
import is guarded: on headless machines :data:`HAS_GTK` is ``False`` and
:data:`AvatarCharacterWindow` is ``None``. Callers must check
:func:`mimosa.ui.environment.is_gui_available` before constructing one.
"""

from __future__ import annotations

import logging
from typing import Callable, Optional

from mimosa.avatar.base_renderer import BaseAvatarRenderer
from mimosa.avatar.renderer_2d import Sprite2DRenderer
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
.mimosa-avatar-character, .mimosa-avatar-character window, window.mimosa-avatar-character {
    background-color: transparent;
}
"""


if HAS_GTK:

    class AvatarCharacterWindow(Gtk.ApplicationWindow):
        """On-screen animated character avatar window.

        Args:
            application: The owning ``Gtk.Application``.
            config: UI preferences (size, opacity, theme, animation).
            renderer: Optional pre-built :class:`BaseAvatarRenderer`. A
                :class:`Sprite2DRenderer` is created from ``config`` otherwise.
                Injectable for tests.
            on_quit: Callback for the "Quit" menu item.
            on_settings: Callback for the "Settings" menu item.
            on_move: Callback ``(x, y)`` invoked after a drag, for persistence.
            on_about: Callback for the "About MimOSA" menu item.
        """

        def __init__(
            self,
            application=None,
            config: Optional[UIConfig] = None,
            renderer: Optional[BaseAvatarRenderer] = None,
            on_quit: Optional[Callable[[], None]] = None,
            on_settings: Optional[Callable[[], None]] = None,
            on_move: Optional[Callable[[int, int], None]] = None,
            on_about: Optional[Callable[[], None]] = None,
        ) -> None:
            super().__init__(application=application)
            self.config = config or UIConfig()
            self.on_quit = on_quit
            self.on_settings = on_settings
            self.on_move = on_move
            self.on_about = on_about

            self.renderer = renderer or Sprite2DRenderer.from_config(self.config)
            # Ensure assets are loaded (placeholder in M8.1). Non-fatal on fail.
            try:
                self.renderer.load()
            except Exception:  # pragma: no cover - defensive
                logger.debug("Renderer load failed", exc_info=True)

            self._anim_source = None
            self._last_frame_ns = None
            self._drag_start = (0, 0)
            self._drag_origin = (int(self.config.pos_x or 0), int(self.config.pos_y or 0))
            self._paused = False

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
            self.add_css_class("mimosa-avatar-character")
            self.set_default_size(self.config.size, self.config.size)
            try:
                self.set_opacity(self.config.opacity)
            except Exception:  # pragma: no cover - backend dependent
                pass
            if self.config.always_on_top:
                fn = getattr(self, "set_keep_above", None)
                if callable(fn):
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
            drag = Gtk.GestureDrag()
            drag.connect("drag-begin", self._on_drag_begin)
            drag.connect("drag-update", self._on_drag_update)
            drag.connect("drag-end", self._on_drag_end)
            self.area.add_controller(drag)

            click = Gtk.GestureClick()
            click.set_button(3)  # right mouse button
            click.connect("pressed", self._on_right_click)
            self.area.add_controller(click)

            keys = Gtk.EventControllerKey()
            keys.connect("key-pressed", self._on_key)
            self.add_controller(keys)

        def _install_menu(self) -> None:
            menu = Gtk.PopoverMenu()
            self._menu = menu
            menu.set_parent(self.area)
            menu.set_has_arrow(True)

            self._add_action("settings", self._action_settings)
            self._add_action("hide", lambda *a: self.set_visible(False))
            self._add_action("quit", self._action_quit)
            self._add_action("about", self._action_about)

        def _build_menu_model(self):
            from gi.repository import Gio

            model = Gio.Menu()
            model.append("Settings", "win.settings")
            model.append("About MimOSA", "win.about")
            model.append("Quit", "win.quit")
            return model

        def _add_action(self, name: str, cb) -> None:
            from gi.repository import Gio

            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", cb)
            self.add_action(action)

        def _show_menu(self, x: Optional[float] = None, y: Optional[float] = None) -> None:
            try:
                self._menu.set_menu_model(self._build_menu_model())
                if x is not None and y is not None:
                    rect = Gdk.Rectangle()
                    rect.x, rect.y, rect.width, rect.height = int(x), int(y), 1, 1
                    self._menu.set_pointing_to(rect)
                self._menu.popup()
            except Exception as exc:  # pragma: no cover - backend dependent
                logger.debug("Context menu popup failed: %s", exc)

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
            self.renderer.update(dt)
            self.area.queue_draw()
            return True  # keep firing

        # -- public state API ----------------------------------------------

        def set_state(self, ui_state) -> None:
            """Update the avatar's visual state (call from the main thread)."""
            try:
                if ui_state is UIState.PAUSED:
                    self._paused = True
                elif ui_state in (UIState.IDLE, UIState.LISTENING):
                    self._paused = False
            except Exception:  # pragma: no cover - defensive
                pass
            self.renderer.set_state(ui_state)
            self.area.queue_draw()

        def set_audio_level(self, level: float) -> None:
            """Forward an audio level to the renderer for reactive speaking."""
            self.renderer.set_audio_level(level)

        # -- event handlers -------------------------------------------------

        def _move_window(self, x: int, y: int) -> bool:
            try:
                surface = self.get_surface()
                if surface is not None and hasattr(surface, "move"):
                    surface.move(int(x), int(y))  # pragma: no cover - backend specific
                    return True
            except Exception as exc:  # pragma: no cover - backend dependent
                logger.debug("Live window move failed: %s", exc)
            return False

        def _on_drag_begin(self, gesture, start_x, start_y) -> None:
            self._drag_start = (start_x, start_y)
            self._drag_origin = (int(self.config.pos_x or 0), int(self.config.pos_y or 0))

        def _on_drag_update(self, gesture, offset_x, offset_y) -> None:
            origin_x, origin_y = self._drag_origin
            self._move_window(int(origin_x + offset_x), int(origin_y + offset_y))

        def _on_drag_end(self, gesture, offset_x, offset_y) -> None:
            origin_x, origin_y = self._drag_origin
            new_x = int(origin_x + offset_x)
            new_y = int(origin_y + offset_y)
            self._move_window(new_x, new_y)
            self.config.pos_x, self.config.pos_y = new_x, new_y
            self._drag_origin = (new_x, new_y)
            if self.on_move is not None:
                try:
                    self.on_move(new_x, new_y)
                except Exception:  # pragma: no cover - defensive
                    logger.debug("on_move callback failed", exc_info=True)

        def _on_right_click(self, gesture, n_press, x, y) -> None:
            self._show_menu(x, y)

        def _on_key(self, controller, keyval, keycode, state) -> bool:
            if keyval == Gdk.KEY_Escape:
                self.set_visible(False)
                return True
            return False

        # -- menu actions --------------------------------------------------

        def _action_settings(self, *args) -> None:
            if self.on_settings is not None:
                self.on_settings()

        def _action_quit(self, *args) -> None:
            if self.on_quit is not None:
                self.on_quit()

        def _action_about(self, *args) -> None:
            if self.on_about is not None:
                self.on_about()

else:  # pragma: no cover - headless import path
    AvatarCharacterWindow = None

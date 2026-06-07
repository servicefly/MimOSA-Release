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
import os
from typing import Callable, Optional

from mimosa.ui.avatar_renderer import AvatarRenderer
from mimosa.ui.state_bridge import UIState
from mimosa.ui.ui_config import UIConfig

logger = logging.getLogger(__name__)

#: Absolute path to the bundled gear icon used for the on-circle menu button.
GEAR_ICON_PATH = os.path.join(os.path.dirname(__file__), "assets", "gear-icon.svg")

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
/* On-circle gear/menu button: hidden until hover, then fades in smoothly. */
.mimosa-gear {
    opacity: 0;
    transition: opacity 200ms ease-in-out;
    background-color: rgba(0, 0, 0, 0.35);
    border: none;
    border-radius: 999px;
    min-width: 24px;
    min-height: 24px;
    padding: 3px;
    color: #ffffff;
}
.mimosa-gear.revealed {
    opacity: 0.7;
}
.mimosa-gear:hover {
    opacity: 1;
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
            on_open_chat: Callback invoked when the user picks "Open/Close Chat".
            on_toggle_pause: Callback invoked to pause/resume listening. May
                return the new paused state (truthy = paused) for label updates.
            on_about: Callback invoked when the user picks "About MimOSA".
            chat_open_provider: Optional ``() -> bool`` used to label the chat
                menu item ("Open" vs "Close Chat").
        """

        def __init__(
            self,
            application=None,
            config: Optional[UIConfig] = None,
            renderer: Optional[AvatarRenderer] = None,
            on_quit: Optional[Callable[[], None]] = None,
            on_settings: Optional[Callable[[], None]] = None,
            on_move: Optional[Callable[[int, int], None]] = None,
            on_open_chat: Optional[Callable[[], None]] = None,
            on_toggle_pause: Optional[Callable[[], object]] = None,
            on_about: Optional[Callable[[], None]] = None,
            chat_open_provider: Optional[Callable[[], bool]] = None,
        ) -> None:
            super().__init__(application=application)
            self.config = config or UIConfig()
            self.on_quit = on_quit
            self.on_settings = on_settings
            self.on_move = on_move
            self.on_open_chat = on_open_chat
            self.on_toggle_pause = on_toggle_pause
            self.on_about = on_about
            self.chat_open_provider = chat_open_provider

            self.renderer = renderer or AvatarRenderer.from_config(self.config)

            self._anim_source = None
            self._last_frame_ns = None
            self._drag_start = (0, 0)
            # Tracks paused state so the context menu can show Pause vs Resume.
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

            # Overlay the gear button on top of the avatar drawing area so it
            # floats in the top-right corner of the circle.
            self._overlay = Gtk.Overlay()
            self._overlay.set_child(self.area)
            self._build_gear_button()
            if self._gear is not None:
                self._overlay.add_overlay(self._gear)
            self.set_child(self._overlay)

        def _build_gear_button(self) -> None:
            """Create the floating gear/menu button (top-right of the circle)."""
            self._gear = None
            try:
                button = Gtk.Button()
                button.add_css_class("mimosa-gear")
                button.set_halign(Gtk.Align.END)
                button.set_valign(Gtk.Align.START)
                button.set_margin_top(10)
                button.set_margin_end(10)
                button.set_can_focus(False)
                button.set_tooltip_text("MimOSA menu")
                button.set_accessible_role(Gtk.AccessibleRole.BUTTON)
                # Prefer the bundled SVG; fall back to a themed system icon.
                image = None
                try:
                    if os.path.exists(GEAR_ICON_PATH):
                        image = Gtk.Image.new_from_file(GEAR_ICON_PATH)
                except Exception:  # pragma: no cover - backend dependent
                    image = None
                if image is None:
                    image = Gtk.Image.new_from_icon_name("emblem-system-symbolic")
                image.set_pixel_size(20)
                button.set_child(image)
                button.connect("clicked", self._on_gear_clicked)
                self._gear = button
            except Exception as exc:  # pragma: no cover - defensive
                logger.debug("Gear button could not be built: %s", exc)
                self._gear = None

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

            # Hover over the avatar -> reveal the gear button (CSS fades it in).
            motion = Gtk.EventControllerMotion()
            motion.connect("enter", self._on_pointer_enter)
            motion.connect("leave", self._on_pointer_leave)
            self._overlay.add_controller(motion)

        def _on_pointer_enter(self, controller, x, y) -> None:
            if self._gear is not None:
                self._gear.add_css_class("revealed")

        def _on_pointer_leave(self, controller) -> None:
            if self._gear is not None:
                self._gear.remove_css_class("revealed")

        def _install_menu(self) -> None:
            # The popover is parented on the overlay so it can anchor to either
            # the cursor (right-click) or the gear button (left-click). The
            # menu *model* is rebuilt on every popup so its labels reflect the
            # current chat-open / paused state.
            menu = Gtk.PopoverMenu()
            self._menu = menu
            menu.set_parent(self._overlay)
            menu.set_has_arrow(True)

            self._add_action("settings", self._action_settings)
            self._add_action("hide", lambda *a: self.set_visible(False))
            self._add_action("quit", self._action_quit)
            self._add_action("openchat", self._action_open_chat)
            self._add_action("pause", self._action_toggle_pause)
            self._add_action("about", self._action_about)

        def _build_menu_model(self):
            """Build a fresh menu model reflecting chat-open / paused state.

            Rebuilding each time keeps labels in sync ("Open Chat Window" vs
            "Close Chat Window", "Pause Listening" vs "Resume Listening") which
            matters for the mic-less, chat-driven accessibility workflow.
            """
            from gi.repository import Gio

            # Determine the live chat-open state (best-effort).
            chat_open = False
            if self.chat_open_provider is not None:
                try:
                    chat_open = bool(self.chat_open_provider())
                except Exception:  # pragma: no cover - defensive
                    chat_open = False

            model = Gio.Menu()
            chat_label = "Close Chat Window" if chat_open else "Open Chat Window"
            model.append(chat_label, "win.openchat")
            model.append("Settings", "win.settings")
            pause_label = "Resume Listening" if self._paused else "Pause Listening"
            model.append(pause_label, "win.pause")
            model.append("About MimOSA", "win.about")
            model.append("Quit", "win.quit")
            return model

        def _show_menu(self, x: Optional[float] = None, y: Optional[float] = None) -> None:
            """Pop up the context menu, anchored at ``(x, y)`` or the gear button.

            GTK automatically constrains popovers to stay on-screen (including
            on multi-monitor setups), so we only need to provide a sensible
            anchor rectangle.
            """
            try:
                # Refresh labels to reflect current state.
                self._menu.set_menu_model(self._build_menu_model())
                if x is not None and y is not None:
                    rect = Gdk.Rectangle()
                    rect.x, rect.y, rect.width, rect.height = int(x), int(y), 1, 1
                    self._menu.set_pointing_to(rect)
                elif self._gear is not None:
                    # Anchor under the gear button (top-right of the circle).
                    alloc = self._gear.get_allocation()
                    rect = Gdk.Rectangle()
                    rect.x, rect.y = alloc.x, alloc.y
                    rect.width, rect.height = alloc.width, alloc.height
                    self._menu.set_pointing_to(rect)
                self._menu.popup()
            except Exception as exc:  # pragma: no cover - backend dependent
                logger.debug("Context menu popup failed: %s", exc)

        def _on_gear_clicked(self, button) -> None:
            self._show_menu()

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
            # Keep the paused flag in sync so the menu shows the right label
            # even if pause/resume is triggered from outside the menu.
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

        def set_viseme_timeline(self, timeline) -> None:
            """Drive lip-sync from a viseme timeline (safe to call any time)."""
            self.renderer.set_viseme_timeline(timeline)
            self.area.queue_draw()

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
            # Anchor the menu at the cursor; GTK keeps it on-screen.
            self._show_menu(x, y)

        def _on_key(self, controller, keyval, keycode, state) -> bool:
            if keyval == Gdk.KEY_Escape:
                self.set_visible(False)
                return True
            # Ctrl+comma -> open Settings (the conventional preferences shortcut).
            ctrl = bool(state & Gdk.ModifierType.CONTROL_MASK)
            if ctrl and keyval in (Gdk.KEY_comma, Gdk.KEY_less):
                self._action_settings()
                return True
            return False

        def apply_config(self, config) -> None:
            """Apply (possibly changed) UI preferences to the live window.

            Called after the Settings dialog applies changes so size/opacity/
            theme/animation update without a restart. Best-effort and safe to
            call from the GTK main thread.
            """
            self.config = config or self.config
            try:
                self.set_opacity(float(self.config.opacity))
            except Exception:  # pragma: no cover - backend dependent
                pass
            try:
                self.set_default_size(self.config.size, self.config.size)
                self.set_size_request(self.config.size, self.config.size)
            except Exception:  # pragma: no cover
                pass
            # Rebuild the renderer from the new config (theme/animation/lip-sync).
            try:
                self.renderer = AvatarRenderer.from_config(self.config)
                self.area.queue_draw()
            except Exception:  # pragma: no cover - defensive
                logger.debug("Renderer rebuild on config apply failed", exc_info=True)

        def _action_settings(self, *args) -> None:
            if self.on_settings is not None:
                self.on_settings()

        def _action_quit(self, *args) -> None:
            self.stop_animation()
            if self.on_quit is not None:
                self.on_quit()
            else:
                self.close()

        def _action_open_chat(self, *args) -> None:
            if self.on_open_chat is not None:
                self.on_open_chat()

        def _action_toggle_pause(self, *args) -> None:
            if self.on_toggle_pause is None:
                # No backend wired; still flip our local label state.
                self._paused = not self._paused
                return
            result = self.on_toggle_pause()
            # Prefer the authoritative paused state returned by the callback;
            # fall back to a local toggle if it returns None.
            if result is None:
                self._paused = not self._paused
            else:
                self._paused = bool(result)

        def _action_about(self, *args) -> None:
            if self.on_about is not None:
                self.on_about()

else:  # pragma: no cover - headless: no GTK to subclass
    AvatarWindow = None

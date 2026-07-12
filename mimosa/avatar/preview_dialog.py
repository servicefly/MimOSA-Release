"""
Avatar Preview Dialog

GTK dialog for previewing generated avatars with Accept/Regenerate options.
"""

from pathlib import Path
from typing import Optional, Callable

try:
    import gi
    gi.require_version('Gtk', '4.0')
    gi.require_version('Gdk', '4.0')
    from gi.repository import Gtk, Gdk, GdkPixbuf
    # A preview dialog needs a real display server, not just importable GTK.
    # Gate on both so headless environments degrade gracefully instead of
    # raising ``RuntimeError: Gtk couldn't be initialized``.
    import os as _os
    GTK_AVAILABLE = bool(
        _os.environ.get("DISPLAY") or _os.environ.get("WAYLAND_DISPLAY")
    )
except (ImportError, ValueError):
    GTK_AVAILABLE = False
    Gtk = None
    Gdk = None
    GdkPixbuf = None


if GTK_AVAILABLE:
    class AvatarPreviewDialog:
        """Dialog for previewing and accepting/rejecting generated avatars."""
        
        def __init__(
            self,
            parent: Optional[Gtk.Window] = None,
            on_accept: Optional[Callable[[Path], None]] = None,
            on_regenerate: Optional[Callable[[], None]] = None,
            on_cancel: Optional[Callable[[], None]] = None
        ):
            """
            Initialize the avatar preview dialog.
            
            Args:
                parent: Parent window
                on_accept: Callback when user accepts avatar (receives sprite path)
                on_regenerate: Callback when user wants to regenerate
                on_cancel: Callback when user cancels
            """
            if not GTK_AVAILABLE:
                raise ImportError("GTK4 is required for avatar preview dialog")
        
            self.on_accept = on_accept
            self.on_regenerate = on_regenerate
            self.on_cancel = on_cancel
        
            self.current_sprite_path: Optional[Path] = None
        
            # Build dialog
            self.dialog = Gtk.Dialog(
                title="Avatar Preview",
                transient_for=parent,
                modal=True
            )
            self.dialog.set_default_size(600, 700)
        
            # Build UI
            self._build_ui()
    
        def _build_ui(self):
            """Build the dialog UI."""
            content = self.dialog.get_content_area()
            content.set_spacing(16)
            content.set_margin_start(24)
            content.set_margin_end(24)
            content.set_margin_top(24)
            content.set_margin_bottom(24)
        
            # Title
            title = Gtk.Label(label="Preview Your Avatar")
            title.add_css_class("title-2")
            content.append(title)
        
            # Description
            desc = Gtk.Label(
                label="This is how your avatar will appear in MimOSA.\n"
                      "You can accept it or generate a new one."
            )
            desc.set_wrap(True)
            desc.set_justify(Gtk.Justification.CENTER)
            content.append(desc)
        
            # Avatar preview area
            self.preview_frame = Gtk.Frame()
            self.preview_frame.set_size_request(400, 400)
            self.preview_frame.set_halign(Gtk.Align.CENTER)
        
            self.preview_image = Gtk.Picture()
            self.preview_image.set_can_shrink(True)
            self.preview_image.set_content_fit(Gtk.ContentFit.CONTAIN)
        
            self.preview_frame.set_child(self.preview_image)
            content.append(self.preview_frame)
        
            # Loading spinner (hidden by default)
            self.spinner = Gtk.Spinner()
            self.spinner.set_halign(Gtk.Align.CENTER)
            self.spinner.set_size_request(64, 64)
            self.spinner.set_visible(False)
            content.append(self.spinner)
        
            # Status label
            self.status_label = Gtk.Label()
            self.status_label.set_wrap(True)
            self.status_label.set_justify(Gtk.Justification.CENTER)
            content.append(self.status_label)
        
            # Buttons
            button_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            button_box.set_halign(Gtk.Align.CENTER)
            button_box.set_margin_top(16)
        
            # Cancel button
            cancel_btn = Gtk.Button(label="Cancel")
            cancel_btn.connect("clicked", self._on_cancel_clicked)
            button_box.append(cancel_btn)
        
            # Regenerate button
            self.regenerate_btn = Gtk.Button(label="Regenerate")
            self.regenerate_btn.connect("clicked", self._on_regenerate_clicked)
            button_box.append(self.regenerate_btn)
        
            # Accept button
            self.accept_btn = Gtk.Button(label="Accept Avatar")
            self.accept_btn.add_css_class("suggested-action")
            self.accept_btn.connect("clicked", self._on_accept_clicked)
            button_box.append(self.accept_btn)
        
            content.append(button_box)
    
        def show_avatar(self, sprite_path: Path):
            """
            Display an avatar sprite in the preview.
        
            Args:
                sprite_path: Path to avatar sprite image
            """
            self.current_sprite_path = sprite_path
        
            if not sprite_path.exists():
                self.status_label.set_label("Error: Avatar file not found")
                self.preview_image.set_paintable(None)
                self.accept_btn.set_sensitive(False)
                return
        
            # Load and display image
            try:
                # Load image using GdkPixbuf for compatibility
                pixbuf = GdkPixbuf.Pixbuf.new_from_file(str(sprite_path))
                texture = Gdk.Texture.new_for_pixbuf(pixbuf)
                self.preview_image.set_paintable(texture)
            
                self.status_label.set_label("Preview ready")
                self.accept_btn.set_sensitive(True)
                self.regenerate_btn.set_sensitive(True)
            
                # Hide spinner
                self.spinner.stop()
                self.spinner.set_visible(False)
                self.preview_frame.set_visible(True)
            
            except Exception as e:
                self.status_label.set_label(f"Error loading avatar: {e}")
                self.preview_image.set_paintable(None)
                self.accept_btn.set_sensitive(False)
    
        def show_generating(self, message: str = "Generating your avatar..."):
            """Show generating state with spinner."""
            self.status_label.set_label(message)
            self.spinner.start()
            self.spinner.set_visible(True)
            self.preview_frame.set_visible(False)
            self.accept_btn.set_sensitive(False)
            self.regenerate_btn.set_sensitive(False)
    
        def show_error(self, message: str):
            """Show error state."""
            self.status_label.set_label(f"Error: {message}")
            self.spinner.stop()
            self.spinner.set_visible(False)
            self.preview_frame.set_visible(True)
            self.accept_btn.set_sensitive(False)
            self.regenerate_btn.set_sensitive(True)
    
        def _on_accept_clicked(self, button):
            """Handle accept button click."""
            if self.on_accept and self.current_sprite_path:
                self.on_accept(self.current_sprite_path)
            self.dialog.close()
    
        def _on_regenerate_clicked(self, button):
            """Handle regenerate button click."""
            if self.on_regenerate:
                self.on_regenerate()
    
        def _on_cancel_clicked(self, button):
            """Handle cancel button click."""
            if self.on_cancel:
                self.on_cancel()
            self.dialog.close()
    
        def run(self) -> Optional[Path]:
            """
            Show the dialog and return the accepted avatar path.
        
            Returns:
                Path to accepted avatar, or None if cancelled
            """
            self.dialog.present()
            return self.current_sprite_path
    
        def close(self):
            """Close the dialog."""
            self.dialog.close()
else:
    # GTK not available - provide stub
    AvatarPreviewDialog = None


def create_preview_dialog(
    parent=None,
    **kwargs
):
    """
    Create an avatar preview dialog.
    
    Args:
        parent: Parent window
        **kwargs: Additional arguments for AvatarPreviewDialog
    
    Returns:
        AvatarPreviewDialog instance, or None if GTK unavailable
    """
    if not GTK_AVAILABLE:
        return None
    
    return AvatarPreviewDialog(parent=parent, **kwargs)

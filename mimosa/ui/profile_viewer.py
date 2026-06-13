"""GTK4 profile review/edit dialog (M3) -- a thin view shell.

Lets the user **review** what MimOSA has learned about them and **edit** any
field (M3 req #11/#12).  All persistence logic lives in
:class:`mimosa.memory.profile_manager.ProfileManager`; this module only renders
the editable fields and hands the edited values back via ``on_save``.

GTK is guarded at class-definition time: on a headless machine
:data:`HAS_GTK` is ``False`` and :data:`ProfileViewer` is ``None``. Callers
should invoke :func:`open_profile_viewer` unconditionally -- it returns
``None`` on headless systems (after invoking ``on_close``).
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)

try:
    import gi

    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk

    HAS_GTK = True
except Exception:  # pragma: no cover - headless import path
    HAS_GTK = False


#: ``on_save(edited_profile_dict)`` / ``on_close()``.
SaveCallback = Callable[[Dict[str, Any]], None]
CloseCallback = Callable[[], None]


# Fields rendered for editing, in display order.
_SCALAR_FIELDS = (("name", "Name"), ("occupation", "Occupation"))
_LIST_FIELDS = (
    ("skills", "Skills"),
    ("interests", "Interests"),
    ("goals", "Goals"),
)
_DICT_FIELDS = (
    ("tools", "Tools"),
    ("preferences", "Preferences"),
    ("schedule", "Schedule"),
    ("relationships", "People"),
)


def profile_to_editable(profile: Dict[str, Any]) -> Dict[str, str]:
    """Flatten a profile dict into ``field -> editable string`` form.

    Lists become comma-separated; dicts become ``key: value`` lines.  Pure and
    unit-testable independent of GTK.
    """

    data = profile.get("user_profile", profile) if isinstance(profile, dict) else {}
    out: Dict[str, str] = {}
    for key, _ in _SCALAR_FIELDS:
        out[key] = str(data.get(key, "") or "")
    for key, _ in _LIST_FIELDS:
        vals = data.get(key, []) or []
        out[key] = ", ".join(str(v) for v in vals)
    for key, _ in _DICT_FIELDS:
        mapping = data.get(key, {}) or {}
        out[key] = "\n".join(f"{k}: {v}" for k, v in mapping.items())
    return out


def editable_to_profile(values: Dict[str, str]) -> Dict[str, Any]:
    """Inverse of :func:`profile_to_editable` -> a ``{"user_profile": {...}}``."""

    profile: Dict[str, Any] = {}
    for key, _ in _SCALAR_FIELDS:
        profile[key] = (values.get(key, "") or "").strip()
    for key, _ in _LIST_FIELDS:
        raw = values.get(key, "") or ""
        profile[key] = [p.strip() for p in raw.split(",") if p.strip()]
    for key, _ in _DICT_FIELDS:
        raw = values.get(key, "") or ""
        mapping: Dict[str, str] = {}
        for line in raw.splitlines():
            if ":" in line:
                k, _, v = line.partition(":")
                if k.strip():
                    mapping[k.strip()] = v.strip()
        profile[key] = mapping
    return {"user_profile": profile}


if HAS_GTK:

    class ProfileViewer(Gtk.Window):  # pragma: no cover - exercised only under GTK
        """A window that shows and edits the user profile."""

        def __init__(
            self,
            profile: Dict[str, Any],
            *,
            transient_for=None,
            on_save: Optional[SaveCallback] = None,
            on_close: Optional[CloseCallback] = None,
            editable: bool = True,
        ) -> None:
            super().__init__(title="What MimOSA knows about you")
            self._on_save = on_save
            self._on_close = on_close
            self._editable = editable
            self._fields: Dict[str, Any] = {}

            self.set_modal(True)
            if transient_for is not None:
                self.set_transient_for(transient_for)
            self.set_default_size(520, 600)

            root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
            for m in ("top", "bottom", "start", "end"):
                getattr(root, f"set_margin_{m}")(16)
            self.set_child(root)

            scroller = Gtk.ScrolledWindow()
            scroller.set_vexpand(True)
            grid = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
            scroller.set_child(grid)
            root.append(scroller)

            values = profile_to_editable(profile)
            for key, label in _SCALAR_FIELDS + _LIST_FIELDS:
                grid.append(self._make_entry_row(key, label, values.get(key, "")))
            for key, label in _DICT_FIELDS:
                grid.append(self._make_text_row(key, label, values.get(key, "")))

            btn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            btn_row.set_halign(Gtk.Align.END)
            if editable:
                save = Gtk.Button(label="Save changes")
                save.connect("clicked", self._on_save_clicked)
                btn_row.append(save)
            close = Gtk.Button(label="Close")
            close.connect("clicked", self._on_close_clicked)
            btn_row.append(close)
            root.append(btn_row)

        def _make_entry_row(self, key, label, value):
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            box.append(Gtk.Label(label=label, xalign=0.0))
            entry = Gtk.Entry()
            entry.set_text(value)
            entry.set_editable(self._editable)
            self._fields[key] = entry
            box.append(entry)
            return box

        def _make_text_row(self, key, label, value):
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            box.append(Gtk.Label(label=label, xalign=0.0))
            view = Gtk.TextView()
            view.set_editable(self._editable)
            view.get_buffer().set_text(value)
            self._fields[key] = view
            box.append(view)
            return box

        def _collect(self) -> Dict[str, str]:
            values: Dict[str, str] = {}
            for key, widget in self._fields.items():
                if isinstance(widget, Gtk.Entry):
                    values[key] = widget.get_text()
                else:  # TextView
                    buf = widget.get_buffer()
                    start, end = buf.get_bounds()
                    values[key] = buf.get_text(start, end, True)
            return values

        def _on_save_clicked(self, *_a) -> None:
            if self._on_save is not None:
                try:
                    self._on_save(editable_to_profile(self._collect()))
                except Exception:
                    logger.debug("profile save failed", exc_info=True)
            self._on_close_clicked()

        def _on_close_clicked(self, *_a) -> None:
            if self._on_close is not None:
                try:
                    self._on_close()
                except Exception:
                    logger.debug("profile on_close failed", exc_info=True)
            self.close()

else:  # headless
    ProfileViewer = None  # type: ignore


def open_profile_viewer(
    profile: Dict[str, Any],
    *,
    transient_for=None,
    on_save: Optional[SaveCallback] = None,
    on_close: Optional[CloseCallback] = None,
    editable: bool = True,
):
    """Show the profile viewer, or no-op gracefully when GTK is unavailable."""

    if not HAS_GTK or ProfileViewer is None:
        logger.info("Profile viewer requested but GTK is unavailable; skipping.")
        if on_close is not None:
            try:
                on_close()
            except Exception:
                logger.debug("profile on_close hook failed", exc_info=True)
        return None

    dialog = ProfileViewer(
        profile,
        transient_for=transient_for,
        on_save=on_save,
        on_close=on_close,
        editable=editable,
    )
    dialog.present()
    return dialog

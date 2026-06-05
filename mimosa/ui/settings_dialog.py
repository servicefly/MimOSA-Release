"""GTK4 multi-page Settings & Preferences dialog (M3.3).

This is the *view* layer for MimOSA's settings. All real behaviour lives in the
GTK-free :class:`mimosa.ui.settings_logic.SettingsController`; this module only
builds widgets, binds them to the controller's working copy, and wires the
Apply / Cancel / OK buttons.

As with :mod:`mimosa.ui.avatar_window`, GTK must exist at *class definition*
time, so the import is guarded: on a headless machine :data:`HAS_GTK` is
``False`` and :data:`SettingsDialog` is ``None``. Callers must check
:func:`mimosa.ui.environment.is_gui_available` (or :data:`HAS_GTK`) before
constructing the dialog.

Layout
------
A ``Gtk.Window`` (modal, transient-for the avatar window) containing:

* a left ``Gtk.StackSidebar`` listing the pages,
* a ``Gtk.Stack`` with one scrollable page per
  :class:`~mimosa.ui.settings_logic.PageSpec`, and
* a bottom action bar with **Cancel**, **Apply**, and **OK**.

Voice/System/Privacy/Appearance pages are generated from the controller's
declarative :class:`FieldSpec` list. The Skills page uses a dedicated
``Gtk.ListBox`` with per-row enable switches and up/down priority buttons. The
About page shows version + a one-line system summary.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, Optional

from mimosa import __author__, __version__
from mimosa.ui.settings_logic import (
    PAGE_ABOUT,
    PAGE_PRIVACY,
    PAGE_SKILLS,
    FieldSpec,
    PageSpec,
    SettingsController,
)
from mimosa.utils.config import AppConfigManager

logger = logging.getLogger(__name__)

try:  # GTK is required to *define* the widget class.
    import gi

    gi.require_version("Gtk", "4.0")
    from gi.repository import GLib, Gtk

    HAS_GTK = True
except Exception:  # pragma: no cover - headless import path
    HAS_GTK = False


if HAS_GTK:

    class SettingsDialog(Gtk.Window):
        """The settings window. Thin view over :class:`SettingsController`.

        Args:
            controller: The pre-built controller (injectable for tests). If
                ``None``, one is created from ``manager``.
            manager: Config manager (required if ``controller`` is ``None``).
            transient_for: The avatar window to anchor/modal against.
            on_close: Optional callback fired after the dialog closes (with a
                bool ``applied`` flag).
            system_summary: Optional one-line system description for About.
        """

        def __init__(
            self,
            *,
            controller: Optional[SettingsController] = None,
            manager: Optional[AppConfigManager] = None,
            transient_for=None,
            on_close: Optional[Callable[[bool], None]] = None,
            system_summary: Optional[str] = None,
        ) -> None:
            super().__init__(title="MimOSA Settings")
            if controller is None:
                if manager is None:
                    raise ValueError("SettingsDialog needs a controller or a manager")
                controller = SettingsController(manager)
            self.controller = controller
            self._on_close = on_close
            self._system_summary = system_summary
            self._applied = False
            # Map (section, name) -> getter returning the widget's current value.
            self._readers: Dict[tuple, Callable[[], Any]] = {}
            # Map (section, name) -> the GTK widget itself (handy for tests).
            self._widgets: Dict[tuple, Any] = {}
            self._restart_banner = None

            self.set_modal(True)
            self.set_default_size(680, 520)
            if transient_for is not None:
                self.set_transient_for(transient_for)

            self._build_ui()

        # -- construction --------------------------------------------------

        def _build_ui(self) -> None:
            root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            self.set_child(root)

            # Restart-required banner (hidden until needed).
            self._restart_banner = Gtk.Label(
                label="Some changes will take effect after restarting MimOSA.",
                xalign=0.0,
            )
            self._restart_banner.add_css_class("dim-label")
            self._restart_banner.set_margin_top(6)
            self._restart_banner.set_margin_start(12)
            self._restart_banner.set_visible(False)
            root.append(self._restart_banner)

            # Sidebar + stack.
            body = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
            body.set_vexpand(True)
            root.append(body)

            self.stack = Gtk.Stack()
            self.stack.set_hexpand(True)
            self.stack.set_vexpand(True)
            sidebar = Gtk.StackSidebar()
            sidebar.set_stack(self.stack)
            body.append(sidebar)
            body.append(self.stack)

            for page in self.controller.pages:
                child = self._build_page(page)
                scroller = Gtk.ScrolledWindow()
                scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
                scroller.set_child(child)
                self.stack.add_titled(scroller, page.page_id, page.title)

            # Action bar.
            actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            actions.set_halign(Gtk.Align.END)
            actions.set_margin_top(8)
            actions.set_margin_bottom(10)
            actions.set_margin_end(12)

            cancel = Gtk.Button(label="Cancel")
            cancel.connect("clicked", self._on_cancel)
            apply_btn = Gtk.Button(label="Apply")
            apply_btn.connect("clicked", self._on_apply)
            ok = Gtk.Button(label="OK")
            ok.add_css_class("suggested-action")
            ok.connect("clicked", self._on_ok)

            actions.append(cancel)
            actions.append(apply_btn)
            actions.append(ok)
            root.append(actions)

        def _build_page(self, page: PageSpec) -> "Gtk.Widget":
            if page.page_id == PAGE_SKILLS:
                return self._build_skills_page()
            if page.page_id == PAGE_ABOUT:
                return self._build_about_page()

            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
            box.set_margin_top(14)
            box.set_margin_bottom(14)
            box.set_margin_start(16)
            box.set_margin_end(16)

            for spec in page.fields:
                box.append(self._build_field_row(spec))

            if page.page_id == PAGE_PRIVACY:
                box.append(self._build_privacy_extras())
            return box

        def _build_field_row(self, spec: FieldSpec) -> "Gtk.Widget":
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            label_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            label_box.set_hexpand(True)
            title = Gtk.Label(label=spec.label, xalign=0.0)
            label_box.append(title)
            if spec.help:
                helper = Gtk.Label(label=spec.help, xalign=0.0)
                helper.add_css_class("dim-label")
                helper.set_wrap(True)
                label_box.append(helper)
            row.append(label_box)

            value = self.controller.get_value(spec.section, spec.name)
            widget = self._make_widget(spec, value)
            widget.set_valign(Gtk.Align.CENTER)
            self._widgets[(spec.section, spec.name)] = widget
            row.append(widget)
            return row

        def _make_widget(self, spec: FieldSpec, value: Any) -> "Gtk.Widget":
            key = (spec.section, spec.name)

            if spec.kind == "bool":
                sw = Gtk.Switch()
                sw.set_active(bool(value))
                sw.connect("notify::active", lambda *_: self._on_field_changed())
                self._readers[key] = lambda w=sw: w.get_active()
                return sw

            if spec.kind == "choice":
                dd_model = list(spec.choices)
                combo = Gtk.DropDown.new_from_strings([str(c) for c in dd_model])
                try:
                    combo.set_selected(dd_model.index(value))
                except ValueError:
                    combo.set_selected(0)
                combo.connect("notify::selected", lambda *_: self._on_field_changed())
                self._readers[key] = lambda w=combo, m=dd_model: (
                    m[w.get_selected()] if 0 <= w.get_selected() < len(m) else m[0]
                )
                return combo

            if spec.kind in ("int", "float"):
                lo = spec.minimum if spec.minimum is not None else 0
                hi = spec.maximum if spec.maximum is not None else 100
                step = spec.step if spec.step is not None else (1 if spec.kind == "int" else 0.1)
                digits = 0 if spec.kind == "int" else 2
                adj = Gtk.Adjustment(value=float(value), lower=float(lo),
                                     upper=float(hi), step_increment=float(step))
                spin = Gtk.SpinButton(adjustment=adj, digits=digits)
                spin.set_numeric(True)
                spin.connect("value-changed", lambda *_: self._on_field_changed())
                if spec.kind == "int":
                    self._readers[key] = lambda w=spin: int(w.get_value())
                else:
                    self._readers[key] = lambda w=spin: round(float(w.get_value()), 3)
                return spin

            # text
            entry = Gtk.Entry()
            entry.set_text(str(value or ""))
            entry.connect("changed", lambda *_: self._on_field_changed())
            self._readers[key] = lambda w=entry: w.get_text()
            return entry

        # -- skills page ---------------------------------------------------

        def _build_skills_page(self) -> "Gtk.Widget":
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            box.set_margin_top(14)
            box.set_margin_bottom(14)
            box.set_margin_start(16)
            box.set_margin_end(16)

            intro = Gtk.Label(
                label="Enable or disable skills and set their priority "
                      "(top = highest). Higher-priority skills are matched first.",
                xalign=0.0,
            )
            intro.add_css_class("dim-label")
            intro.set_wrap(True)
            box.append(intro)

            self._skills_list = Gtk.ListBox()
            self._skills_list.set_selection_mode(Gtk.SelectionMode.NONE)
            self._skills_list.add_css_class("boxed-list")
            box.append(self._skills_list)
            self._refresh_skills_list()
            return box

        def _refresh_skills_list(self) -> None:
            # Clear existing rows.
            child = self._skills_list.get_first_child()
            while child is not None:
                nxt = child.get_next_sibling()
                self._skills_list.remove(child)
                child = nxt

            rows = self.controller.skill_rows()
            for i, srow in enumerate(rows):
                lb_row = Gtk.ListBoxRow()
                hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
                hbox.set_margin_top(6)
                hbox.set_margin_bottom(6)
                hbox.set_margin_start(8)
                hbox.set_margin_end(8)

                info = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
                info.set_hexpand(True)
                name = Gtk.Label(label=srow.label, xalign=0.0)
                info.append(name)
                sub = "Uses LLM" if srow.uses_llm else "Local"
                if srow.description:
                    sub = f"{sub} \u2022 {srow.description}"
                subl = Gtk.Label(label=sub, xalign=0.0)
                subl.add_css_class("dim-label")
                info.append(subl)
                hbox.append(info)

                up = Gtk.Button.new_from_icon_name("go-up-symbolic")
                up.set_sensitive(i > 0)
                up.connect("clicked", self._on_skill_move, srow.skill_id, -1)
                down = Gtk.Button.new_from_icon_name("go-down-symbolic")
                down.set_sensitive(i < len(rows) - 1)
                down.connect("clicked", self._on_skill_move, srow.skill_id, 1)
                hbox.append(up)
                hbox.append(down)

                sw = Gtk.Switch()
                sw.set_active(srow.enabled)
                sw.set_valign(Gtk.Align.CENTER)
                sw.connect("notify::active", self._on_skill_toggle, srow.skill_id)
                hbox.append(sw)

                lb_row.set_child(hbox)
                self._skills_list.append(lb_row)

        def _on_skill_move(self, _btn, skill_id, delta) -> None:
            if self.controller.move_skill(skill_id, delta):
                self._refresh_skills_list()
                self._on_field_changed()

        def _on_skill_toggle(self, switch, _param, skill_id) -> None:
            self.controller.set_skill_enabled(skill_id, switch.get_active())
            self._on_field_changed()

        # -- privacy extras ------------------------------------------------

        def _build_privacy_extras(self) -> "Gtk.Widget":
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            box.set_margin_top(8)

            self._privacy_summary = Gtk.Label(
                label=self.controller.privacy_summary(), xalign=0.0)
            self._privacy_summary.set_wrap(True)
            self._privacy_summary.add_css_class("dim-label")
            box.append(self._privacy_summary)

            clear = Gtk.Button(label="Clear conversation history")
            clear.add_css_class("destructive-action")
            clear.set_halign(Gtk.Align.START)
            clear.connect("clicked", self._on_clear_history)
            box.append(clear)
            self._clear_status = Gtk.Label(label="", xalign=0.0)
            self._clear_status.add_css_class("dim-label")
            box.append(self._clear_status)
            return box

        def _on_clear_history(self, _btn) -> None:
            cleared = self.controller.clear_history()
            self._clear_status.set_text(
                f"Cleared {cleared} conversation turn(s)."
                if cleared else "Conversation history cleared."
            )

        # -- about page ----------------------------------------------------

        def _build_about_page(self) -> "Gtk.Widget":
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
            box.set_margin_top(18)
            box.set_margin_bottom(18)
            box.set_margin_start(18)
            box.set_margin_end(18)

            heading = Gtk.Label(xalign=0.0)
            heading.set_markup(f"<b>MimOSA</b>  v{__version__}")
            box.append(heading)

            box.append(Gtk.Label(
                label="A privacy-first, on-device voice assistant for Kubuntu.",
                xalign=0.0))
            box.append(Gtk.Label(label=f"Author: {__author__}", xalign=0.0))
            box.append(Gtk.Label(label="License: MIT", xalign=0.0))

            if self._system_summary:
                sysl = Gtk.Label(label=f"System: {self._system_summary}", xalign=0.0)
                sysl.set_wrap(True)
                sysl.add_css_class("dim-label")
                box.append(sysl)

            credits = Gtk.Label(
                label="Built with GTK4, Whisper, Piper, Porcupine, and Abacus.AI.",
                xalign=0.0)
            credits.add_css_class("dim-label")
            credits.set_wrap(True)
            box.append(credits)

            updates = Gtk.Label(
                label="Check for updates: not available in this build.", xalign=0.0)
            updates.add_css_class("dim-label")
            box.append(updates)
            return box

        # -- change handling -----------------------------------------------

        def _sync_readers_to_controller(self) -> None:
            """Push every bound widget's value into the controller's working copy."""
            for (section, name), reader in self._readers.items():
                try:
                    self.controller.set_value(section, name, reader())
                except Exception:  # pragma: no cover - defensive
                    logger.exception("failed to read %s.%s", section, name)

        def _on_field_changed(self) -> None:
            self._sync_readers_to_controller()
            # Live-update the privacy summary + restart banner.
            if getattr(self, "_privacy_summary", None) is not None:
                self._privacy_summary.set_text(self.controller.privacy_summary())
            if self._restart_banner is not None:
                self._restart_banner.set_visible(self.controller.restart_required())

        # -- buttons -------------------------------------------------------

        def _on_apply(self, _btn) -> None:
            self._sync_readers_to_controller()
            self.controller.apply()
            self._applied = True
            if self._restart_banner is not None:
                self._restart_banner.set_visible(False)

        def _on_ok(self, _btn) -> None:
            self._on_apply(_btn)
            self._close_dialog()

        def _on_cancel(self, _btn) -> None:
            self.controller.cancel()
            self._close_dialog()

        def _close_dialog(self) -> None:
            applied = self._applied
            if self._on_close is not None:
                try:
                    self._on_close(applied)
                except Exception:  # pragma: no cover
                    logger.exception("on_close callback failed")
            self.close()

else:  # pragma: no cover - headless

    SettingsDialog = None  # type: ignore


def open_settings_dialog(
    manager: AppConfigManager,
    *,
    transient_for=None,
    skills_provider=None,
    on_clear_history=None,
    on_close=None,
    system_summary: Optional[str] = None,
):
    """Construct, show, and return a :class:`SettingsDialog`.

    Returns ``None`` (logging a warning) when GTK is unavailable, so callers can
    invoke this unconditionally. The controller is built here so the
    ``skills_provider`` / ``on_clear_history`` hooks are wired consistently.
    """
    if not HAS_GTK or SettingsDialog is None:
        logger.warning("Settings dialog requested but GTK is unavailable")
        return None

    controller = SettingsController(
        manager,
        skills_provider=skills_provider,
        on_clear_history=on_clear_history,
    )
    dialog = SettingsDialog(
        controller=controller,
        transient_for=transient_for,
        on_close=on_close,
        system_summary=system_summary,
    )
    dialog.present()
    return dialog

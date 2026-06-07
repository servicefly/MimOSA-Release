"""GTK4 first-run setup wizard dialog (M4.2) -- a thin view shell.

All behaviour lives in the GTK-free
:class:`mimosa.ui.setup_wizard.SetupWizardController`; this module only renders
the controller's declarative :class:`~mimosa.ui.setup_wizard.WizardStep`
descriptors as a paged ``Gtk.Window`` with Back / Next / Finish buttons.

As with :mod:`mimosa.ui.settings_dialog`, GTK must exist at *class definition*
time, so the import is guarded: on a headless machine :data:`HAS_GTK` is
``False`` and :data:`SetupWizardDialog` is ``None``. Callers should invoke
:func:`open_setup_wizard` unconditionally -- it returns ``None`` (after marking
the wizard complete) when GTK is unavailable.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Callable, Optional

from mimosa.ui.setup_wizard import (
    STEP_MICROPHONE,
    STEP_SPEAKER,
    SetupWizardController,
    WizardStep,
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


def _coerce_widget_value(spec, widget) -> Any:  # pragma: no cover - GTK-only
    """Read the current value from a rendered field widget."""
    if spec.kind == "bool":
        return widget.get_active()
    if spec.kind in ("int", "float"):
        value = widget.get_value()
        return int(value) if spec.kind == "int" else float(value)
    if spec.kind == "choice":
        idx = widget.get_selected()
        return spec.choices[idx] if 0 <= idx < len(spec.choices) else spec.choices[0]
    return widget.get_text()


if HAS_GTK:

    class SetupWizardDialog(Gtk.Window):  # pragma: no cover - exercised only under GTK
        """A simple paged wizard window driven by a controller."""

        def __init__(
            self,
            controller: SetupWizardController,
            *,
            transient_for=None,
            on_close: Optional[Callable[[bool], None]] = None,
        ) -> None:
            super().__init__(title="MimOSA Setup")
            self._controller = controller
            self._on_close = on_close
            self.set_modal(True)
            if transient_for is not None:
                self.set_transient_for(transient_for)
            self.set_default_size(480, 420)

            root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
            root.set_margin_top(18)
            root.set_margin_bottom(18)
            root.set_margin_start(18)
            root.set_margin_end(18)
            self.set_child(root)

            self._title = Gtk.Label(xalign=0)
            self._title.add_css_class("title-2")
            self._body = Gtk.Label(xalign=0, wrap=True)
            self._fields_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            self._progress = Gtk.ProgressBar(show_text=True)
            root.append(self._title)
            root.append(self._body)
            root.append(self._fields_box)
            root.append(Gtk.Box(vexpand=True))  # spacer
            root.append(self._progress)

            actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            actions.set_halign(Gtk.Align.END)
            self._back_btn = Gtk.Button(label="Back")
            self._next_btn = Gtk.Button(label="Next")
            self._next_btn.add_css_class("suggested-action")
            self._back_btn.connect("clicked", lambda *_: self._go(-1))
            self._next_btn.connect("clicked", lambda *_: self._go(1))
            actions.append(self._back_btn)
            actions.append(self._next_btn)
            root.append(actions)

            self._render()

        def _go(self, direction: int) -> None:
            self._commit_visible_fields()
            if direction > 0 and self._controller.is_last:
                self._controller.finish()
                self._close(applied=True)
                return
            if direction > 0:
                self._controller.next()
            else:
                self._controller.back()
            self._render()

        def _commit_visible_fields(self) -> None:
            for spec, widget in getattr(self, "_widgets", []):
                try:
                    self._controller.set_value(spec.section, spec.name,
                                               _coerce_widget_value(spec, widget))
                except Exception:
                    logger.debug("Could not commit %s.%s", spec.section, spec.name)

        def _render(self) -> None:
            step: WizardStep = self._controller.current_step
            self._title.set_text(step.title)
            self._body.set_text(step.body)
            self._progress.set_fraction(self._controller.progress())
            self._progress.set_text(f"Step {self._controller.index + 1} "
                                    f"of {self._controller.step_count}")
            child = self._fields_box.get_first_child()
            while child is not None:
                self._fields_box.remove(child)
                child = self._fields_box.get_first_child()
            self._widgets = []
            if step.step_id == STEP_MICROPHONE:
                self._render_microphone()
            elif step.step_id == STEP_SPEAKER:
                self._render_speaker()
            else:
                for spec in step.fields:
                    row, widget = self._build_field(spec)
                    self._fields_box.append(row)
                    self._widgets.append((spec, widget))
            self._back_btn.set_sensitive(not self._controller.is_first)
            self._next_btn.set_label("Finish" if self._controller.is_last else "Next")

        # -- microphone step (custom UI) -----------------------------------

        def _render_microphone(self) -> None:
            """Build the device dropdown, Test button, volume meter & status."""
            self._mic_testing = False
            self._mic_choices = self._controller.available_microphones()

            # Device dropdown.
            picker = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            picker.append(Gtk.Label(label="Microphone", xalign=0, hexpand=True))
            self._mic_dropdown = Gtk.DropDown.new_from_strings(
                [c.label for c in self._mic_choices]
            )
            # Pre-select the device already chosen (or the flagged default).
            selected_index = self._controller.get_selected_microphone()
            preselect = 0
            for i, c in enumerate(self._mic_choices):
                if c.index == selected_index and selected_index is not None:
                    preselect = i
                    break
                if selected_index is None and c.is_default:
                    preselect = i
            self._mic_dropdown.set_selected(preselect)
            self._mic_dropdown.connect("notify::selected", self._on_mic_selected)
            picker.append(self._mic_dropdown)
            self._fields_box.append(picker)
            # Commit the pre-selected device immediately.
            self._on_mic_selected(self._mic_dropdown, None)

            # Test button.
            self._mic_test_btn = Gtk.Button(label="Test Microphone")
            self._mic_test_btn.connect("clicked", self._on_test_microphone)
            self._fields_box.append(self._mic_test_btn)

            # Volume meter.
            self._mic_meter = Gtk.ProgressBar(show_text=False)
            self._mic_meter.set_fraction(0.0)
            self._fields_box.append(self._mic_meter)

            # Status label.
            self._mic_status = Gtk.Label(
                label="Select your microphone, then click \u201cTest Microphone\u201d.",
                xalign=0, wrap=True,
            )
            self._fields_box.append(self._mic_status)

        def _on_mic_selected(self, dropdown, _pspec) -> None:
            idx = dropdown.get_selected()
            if 0 <= idx < len(self._mic_choices):
                self._controller.set_microphone(self._mic_choices[idx].index)

        def _on_test_microphone(self, _button) -> None:
            if getattr(self, "_mic_testing", False):
                return
            self._mic_testing = True
            self._mic_test_btn.set_sensitive(False)
            self._mic_test_btn.set_label("Listening… speak now")
            self._mic_meter.set_fraction(0.0)
            self._mic_status.set_text("Listening for 2 seconds — say something!")

            def _level(level: float) -> None:
                GLib.idle_add(self._mic_meter.set_fraction, min(1.0, max(0.0, level)))

            def _worker() -> None:
                peak = self._controller.test_microphone(seconds=2.0, on_level=_level)
                GLib.idle_add(self._finish_mic_test, peak)

            threading.Thread(target=_worker, name="mimosa-mic-test",
                             daemon=True).start()

        def _finish_mic_test(self, peak: Optional[float]) -> bool:
            self._mic_testing = False
            self._mic_test_btn.set_sensitive(True)
            self._mic_test_btn.set_label("Test Microphone")
            if peak is None:
                self._mic_meter.set_fraction(0.0)
                self._mic_status.set_text(
                    "Couldn't access that microphone. Try a different device, or "
                    "continue — you can change this later in Settings."
                )
            elif peak < 0.02:
                self._mic_meter.set_fraction(0.0)
                self._mic_status.set_text(
                    "Didn't hear much. Check the mic is unmuted and try again, or "
                    "pick another device."
                )
            else:
                self._mic_meter.set_fraction(min(1.0, peak))
                self._mic_status.set_text(
                    f"Looks good! Peak level {int(peak * 100)}%. This microphone "
                    "is working."
                )
            return False  # one-shot idle callback

        # -- speaker step (custom UI) --------------------------------------

        def _render_speaker(self) -> None:
            """Build the output-device dropdown, Test Speaker button & status."""
            self._spk_testing = False
            self._spk_choices = self._controller.available_speakers()

            # Device dropdown.
            picker = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            picker.append(Gtk.Label(label="Speaker", xalign=0, hexpand=True))
            self._spk_dropdown = Gtk.DropDown.new_from_strings(
                [c.label for c in self._spk_choices]
            )
            # Pre-select the device already chosen (or the flagged default).
            selected_index = self._controller.get_selected_speaker()
            preselect = 0
            for i, c in enumerate(self._spk_choices):
                if c.index == selected_index and selected_index is not None:
                    preselect = i
                    break
                if selected_index is None and c.is_default:
                    preselect = i
            self._spk_dropdown.set_selected(preselect)
            self._spk_dropdown.connect("notify::selected", self._on_spk_selected)
            picker.append(self._spk_dropdown)
            self._fields_box.append(picker)
            # Commit the pre-selected device immediately.
            self._on_spk_selected(self._spk_dropdown, None)

            # Test button.
            self._spk_test_btn = Gtk.Button(label="Test Speaker")
            self._spk_test_btn.connect("clicked", self._on_test_speaker)
            self._fields_box.append(self._spk_test_btn)

            # Status label.
            self._spk_status = Gtk.Label(
                label="Select your speaker, then click \u201cTest Speaker\u201d to "
                "hear a chime.",
                xalign=0, wrap=True,
            )
            self._fields_box.append(self._spk_status)

        def _on_spk_selected(self, dropdown, _pspec) -> None:
            idx = dropdown.get_selected()
            if 0 <= idx < len(self._spk_choices):
                self._controller.set_speaker(self._spk_choices[idx].index)

        def _on_test_speaker(self, _button) -> None:
            if getattr(self, "_spk_testing", False):
                return
            self._spk_testing = True
            self._spk_test_btn.set_sensitive(False)
            self._spk_test_btn.set_label("Playing chime…")
            self._spk_status.set_text("Playing a short chime — can you hear it?")

            def _worker() -> None:
                ok = self._controller.test_speaker(seconds=1.0)
                GLib.idle_add(self._finish_spk_test, ok)

            threading.Thread(target=_worker, name="mimosa-spk-test",
                             daemon=True).start()

        def _finish_spk_test(self, ok: bool) -> bool:
            self._spk_testing = False
            self._spk_test_btn.set_sensitive(True)
            self._spk_test_btn.set_label("Test Speaker")
            if ok:
                self._spk_status.set_text(
                    "Played a chime. If you heard it, this speaker is working! "
                    "If not, pick another device and test again."
                )
            else:
                self._spk_status.set_text(
                    "Couldn't access that speaker. Try a different device, or "
                    "continue — you can change this later in Settings."
                )
            return False  # one-shot idle callback

        def _build_field(self, spec):
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            label = Gtk.Label(label=spec.label, xalign=0)
            row.append(label)
            # Info icon (ℹ️) with a hover tooltip explaining the option. The
            # help text is authored on each FieldSpec; surfacing it here means
            # newcomers can hover to learn what an option does and why.
            if getattr(spec, "help", ""):
                info = Gtk.Image.new_from_icon_name("help-about-symbolic")
                info.set_tooltip_text(spec.help)
                info.add_css_class("dim-label")
                row.append(info)
                # Also put the tooltip on the label/row so the whole line is
                # discoverable, not just the small icon.
                label.set_tooltip_text(spec.help)
                row.set_tooltip_text(spec.help)
            # Spacer pushes the editing widget to the right edge.
            row.append(Gtk.Box(hexpand=True))
            value = self._controller.get_value(spec.section, spec.name)
            if spec.kind == "bool":
                widget = Gtk.Switch(active=bool(value))
                widget.set_halign(Gtk.Align.END)
            elif spec.kind in ("int", "float"):
                step = spec.step or (1 if spec.kind == "int" else 0.1)
                adj = Gtk.Adjustment(value=float(value or 0),
                                     lower=spec.minimum or 0,
                                     upper=spec.maximum or 100, step_increment=step)
                digits = 0 if spec.kind == "int" else 2
                widget = Gtk.SpinButton(adjustment=adj, digits=digits)
            elif spec.kind == "choice":
                widget = Gtk.DropDown.new_from_strings([str(c) for c in spec.choices])
                if value in spec.choices:
                    widget.set_selected(spec.choices.index(value))
            else:
                widget = Gtk.Entry(text=str(value or ""))
            row.append(widget)
            return row, widget

        def _close(self, applied: bool) -> None:
            if self._on_close is not None:
                try:
                    self._on_close(applied)
                except Exception:
                    logger.debug("on_close hook failed", exc_info=True)
            self.close()

else:  # headless
    SetupWizardDialog = None  # type: ignore


def open_setup_wizard(
    manager: AppConfigManager,
    *,
    transient_for=None,
    on_close: Optional[Callable[[bool], None]] = None,
):
    """Show the setup wizard, or complete it silently when GTK is unavailable.

    Returns the dialog, or ``None`` on headless systems (after marking the
    wizard complete so it does not block startup or reappear).
    """
    if not HAS_GTK or SetupWizardDialog is None:
        logger.info("Setup wizard requested but GTK is unavailable; "
                    "marking first-run complete with defaults.")
        manager.mark_first_run_complete()
        return None

    controller = SetupWizardController(manager)
    dialog = SetupWizardDialog(
        controller, transient_for=transient_for, on_close=on_close,
    )
    dialog.present()
    return dialog

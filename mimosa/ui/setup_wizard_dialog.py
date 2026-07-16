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
    OLLAMA_INSTALL_URL,
    STEP_AVATAR,
    STEP_FINISH,
    STEP_LLM,
    STEP_MICROPHONE,
    STEP_SPEAKER,
    STEP_VOICE,
    STEP_WAKEWORD,
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
            self.set_default_size(720, 460)

            # Outermost layout: a persistent informative sidebar on the left and
            # the step content on the right. The sidebar (M2) replaces the old
            # per-field info-icon tooltips — the "what & why" help for each step
            # is now always visible instead of hidden behind a hover.
            outer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
            self.set_child(outer)

            sidebar = self._build_sidebar()
            outer.append(sidebar)

            root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
            root.set_margin_top(18)
            root.set_margin_bottom(18)
            root.set_margin_start(18)
            root.set_margin_end(18)
            root.set_hexpand(True)
            outer.append(root)

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

            # Bug #10: if the user dismisses the window (X button / Esc) without
            # clicking Finish, still persist a config and mark first-run
            # complete so ~/.config/mimosa/settings.json always exists and the
            # wizard does not reappear on every launch.
            self.connect("close-request", self._on_close_request)

            self._render()

        def _build_sidebar(self):
            """Build the persistent left-hand guidance panel.

            Returns a styled, fixed-width box whose label is refreshed in
            :meth:`_render` with the current step's ``sidebar`` text. This is the
            M2 replacement for the per-field info-icon tooltips.
            """
            panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
            panel.set_size_request(240, -1)
            panel.add_css_class("sidebar")
            panel.set_margin_top(18)
            panel.set_margin_bottom(18)
            panel.set_margin_start(18)
            panel.set_margin_end(18)

            heading = Gtk.Label(label="MimOSA Setup", xalign=0)
            heading.add_css_class("title-4")
            panel.append(heading)

            # Scrollable so longer guidance never clips on small screens.
            scroller = Gtk.ScrolledWindow()
            scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
            scroller.set_vexpand(True)
            self._sidebar_label = Gtk.Label(xalign=0, yalign=0, wrap=True)
            self._sidebar_label.add_css_class("dim-label")
            self._sidebar_label.set_max_width_chars(32)
            scroller.set_child(self._sidebar_label)
            panel.append(scroller)

            return panel

        def _on_close_request(self, *_args) -> bool:
            if not self._controller.finished:
                try:
                    self._controller.cancel()  # marks first-run complete + saves
                except Exception:
                    logger.debug("Wizard cancel-on-close failed", exc_info=True)
                if self._on_close is not None:
                    try:
                        self._on_close(False)
                    except Exception:
                        logger.debug("on_close hook failed", exc_info=True)
            return False  # allow the window to close

        def _go(self, direction: int) -> None:
            self._commit_visible_fields()
            # The "Connect Your AI Brain" step is required: block forward
            # navigation until a usable provider + key (or local Ollama) is set.
            if (direction > 0
                    and self._controller.current_step.step_id == STEP_LLM
                    and not self._controller.llm_step_valid()):
                self._flag_llm_incomplete()
                return
            if direction > 0 and self._controller.is_last:
                self._controller.finish()
                # Honour the optional "Create a desktop shortcut" checkbox
                # shown on the final step.
                check = getattr(self, "_desktop_shortcut_check", None)
                if check is not None and check.get_active():
                    self._controller.create_desktop_shortcut()
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
            # Refresh the persistent guidance panel for this step.
            if getattr(self, "_sidebar_label", None) is not None:
                self._sidebar_label.set_text(step.sidebar or "")
            self._progress.set_fraction(self._controller.progress())
            self._progress.set_text(f"Step {self._controller.index + 1} "
                                    f"of {self._controller.step_count}")
            child = self._fields_box.get_first_child()
            while child is not None:
                self._fields_box.remove(child)
                child = self._fields_box.get_first_child()
            self._widgets = []
            # Reset per-step custom widgets so stale references don't linger.
            self._desktop_shortcut_check = None
            # Default to an enabled Next button; the (required) LLM step may
            # disable it until a valid provider/key is chosen.
            self._next_btn.set_sensitive(True)
            if step.step_id == STEP_MICROPHONE:
                self._render_microphone()
            elif step.step_id == STEP_SPEAKER:
                self._render_speaker()
            elif step.step_id == STEP_LLM:
                self._render_llm()
            elif step.step_id == STEP_VOICE:
                self._render_voice()
            elif step.step_id == STEP_AVATAR:
                self._render_avatar()
            elif step.step_id == STEP_WAKEWORD:
                self._render_wakeword()
            elif step.step_id == STEP_FINISH:
                self._render_finish()
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

        # -- LLM step (custom UI) ------------------------------------------

        def _render_llm(self) -> None:
            """Build the provider radio group, masked key entry & Ollama help."""
            self._llm_options = self._controller.llm_provider_options()
            current = self._controller.get_llm_provider()

            self._llm_radios = {}
            group_leader = None
            for opt in self._llm_options:
                radio = Gtk.CheckButton(label=opt.label)
                if group_leader is None:
                    group_leader = radio
                else:
                    radio.set_group(group_leader)
                if opt.key == current:
                    radio.set_active(True)
                radio.connect("toggled", self._on_llm_provider_toggled, opt.key)
                self._fields_box.append(radio)
                # One-line description under each radio.
                desc = Gtk.Label(label=opt.description, xalign=0, wrap=True)
                desc.add_css_class("dim-label")
                desc.set_margin_start(28)
                self._fields_box.append(desc)
                self._llm_radios[opt.key] = radio

            # Masked API-key entry (shown only for cloud providers).
            self._llm_key_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                                        spacing=10)
            self._llm_key_box.set_margin_top(8)
            self._llm_key_box.append(
                Gtk.Label(label="API key", xalign=0, hexpand=True)
            )
            self._llm_key_entry = Gtk.Entry()
            self._llm_key_entry.set_visibility(False)  # mask the secret
            self._llm_key_entry.set_input_purpose(Gtk.InputPurpose.PASSWORD)
            self._llm_key_entry.set_placeholder_text("Paste your API key")
            self._llm_key_entry.set_hexpand(True)
            self._llm_key_entry.set_text(self._controller.get_api_key() or "")
            self._llm_key_entry.connect("changed", self._on_llm_key_changed)
            # Eye toggle to reveal/hide the key.
            self._llm_key_entry.set_icon_from_icon_name(
                Gtk.EntryIconPosition.SECONDARY, "view-reveal-symbolic"
            )
            self._llm_key_entry.connect("icon-press", self._on_llm_key_reveal)
            self._llm_key_box.append(self._llm_key_entry)
            self._fields_box.append(self._llm_key_box)

            # Ollama detection / install help (shown only for the Ollama option).
            self._llm_ollama_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                                           spacing=4)
            self._llm_ollama_status = Gtk.Label(xalign=0, wrap=True)
            self._llm_ollama_box.append(self._llm_ollama_status)
            self._llm_ollama_link = Gtk.LinkButton.new_with_label(
                OLLAMA_INSTALL_URL, "Get Ollama (opens ollama.com)"
            )
            self._llm_ollama_link.set_halign(Gtk.Align.START)
            self._llm_ollama_box.append(self._llm_ollama_link)
            self._fields_box.append(self._llm_ollama_box)

            # Validation status line.
            self._llm_status = Gtk.Label(xalign=0, wrap=True)
            self._llm_status.add_css_class("dim-label")
            self._fields_box.append(self._llm_status)

            self._refresh_llm_step()

        def _selected_llm_key(self) -> str:
            for key, radio in getattr(self, "_llm_radios", {}).items():
                if radio.get_active():
                    return key
            return self._controller.get_llm_provider()

        def _on_llm_provider_toggled(self, radio, key) -> None:
            if not radio.get_active():
                return  # only react to the newly-selected radio
            self._controller.set_llm_provider(key)
            self._refresh_llm_step()

        def _on_llm_key_changed(self, entry) -> None:
            self._controller.set_api_key(entry.get_text())
            self._refresh_llm_step(probe_ollama=False)

        def _on_llm_key_reveal(self, entry, _icon_pos) -> None:
            visible = not entry.get_visibility()
            entry.set_visibility(visible)
            entry.set_icon_from_icon_name(
                Gtk.EntryIconPosition.SECONDARY,
                "view-conceal-symbolic" if visible else "view-reveal-symbolic",
            )

        def _refresh_llm_step(self, probe_ollama: bool = True) -> None:
            """Show/hide the key entry & Ollama help for the current provider,
            and update the validation status + Next button sensitivity."""
            key = self._selected_llm_key()
            needs_key = self._controller.provider_requires_key(key)
            is_ollama = key == "ollama"

            self._llm_key_box.set_visible(needs_key)
            self._llm_ollama_box.set_visible(is_ollama)

            if is_ollama and probe_ollama:
                # Probe on a worker thread so the UI never blocks.
                self._llm_ollama_status.set_text("Checking for a local Ollama…")

                def _worker():
                    found = self._controller.detect_ollama()
                    GLib.idle_add(self._apply_ollama_result, found)

                threading.Thread(target=_worker, name="mimosa-ollama-probe",
                                 daemon=True).start()
            elif not is_ollama:
                self._update_llm_status()

        def _apply_ollama_result(self, found: bool) -> bool:
            if found:
                self._llm_ollama_status.set_text(
                    "✓ Found a running Ollama. You're all set — no key needed."
                )
                self._llm_ollama_link.set_visible(False)
            else:
                self._llm_ollama_status.set_text(
                    "Ollama isn't running yet. Install it, then run "
                    "\u201collama serve\u201d and pull a model (e.g. "
                    "\u201collama pull llama3.1:8b\u201d)."
                )
                self._llm_ollama_link.set_visible(True)
            self._update_llm_status()
            return False  # one-shot idle callback

        def _update_llm_status(self) -> None:
            valid = self._controller.llm_step_valid()
            self._next_btn.set_sensitive(valid)
            if valid:
                self._llm_status.set_text("Looks good — you can continue.")
            else:
                key = self._selected_llm_key()
                if self._controller.provider_requires_key(key):
                    self._llm_status.set_text(
                        "Enter your API key to continue (this step is required)."
                    )
                else:
                    self._llm_status.set_text(
                        "Start Ollama to continue, or pick a cloud provider "
                        "(this step is required)."
                    )

        def _flag_llm_incomplete(self) -> None:
            """Called when the user tries to advance without a valid choice."""
            self._update_llm_status()

        # -- voice step (custom UI: voice picker + Play Sample) ------------

        def _render_voice(self) -> None:
            """Render the "Your Voice" step: a named voice picker with a
            Play-Sample button, followed by the declarative wake-word /
            sensitivity / STT fields the step declares."""
            self._voice_options = self._controller.voice_options()

            if self._voice_options:
                picker = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
                picker.append(Gtk.Label(label="Voice", xalign=0))
                picker.append(Gtk.Box(hexpand=True))
                labels = [f"{name} — {desc}" if desc else name
                          for _vid, name, desc in self._voice_options]
                self._voice_dropdown = Gtk.DropDown.new_from_strings(labels)
                # Pre-select the currently-chosen voice, if any.
                current = self._controller.get_selected_voice()
                for i, (vid, _n, _d) in enumerate(self._voice_options):
                    if vid == current:
                        self._voice_dropdown.set_selected(i)
                        break
                self._voice_dropdown.connect("notify::selected",
                                             self._on_voice_selected)
                picker.append(self._voice_dropdown)

                # ▶ Play Sample button (best-effort; degrades on no audio).
                self._voice_play_btn = Gtk.Button(label="\u25b6 Play Sample")
                self._voice_play_btn.set_tooltip_text(
                    "Hear a short sample of the selected voice.")
                self._voice_play_btn.connect("clicked", self._on_play_voice_sample)
                picker.append(self._voice_play_btn)
                self._fields_box.append(picker)

                self._voice_status = Gtk.Label(xalign=0, wrap=True)
                self._voice_status.add_css_class("dim-label")
                self._fields_box.append(self._voice_status)
                # Commit the pre-selected voice immediately.
                self._on_voice_selected(self._voice_dropdown, None)

                self._fields_box.append(Gtk.Separator(
                    orientation=Gtk.Orientation.HORIZONTAL))

            # Declarative fields (wake word, sensitivity, STT model).
            for spec in self._controller.current_step.fields:
                row, widget = self._build_field(spec)
                self._fields_box.append(row)
                self._widgets.append((spec, widget))

        def _on_voice_selected(self, dropdown, _pspec) -> None:
            idx = dropdown.get_selected()
            if 0 <= idx < len(self._voice_options):
                self._controller.set_selected_voice(self._voice_options[idx][0])

        def _on_play_voice_sample(self, _button) -> None:
            idx = self._voice_dropdown.get_selected()
            if not (0 <= idx < len(self._voice_options)):
                return
            voice_id = self._voice_options[idx][0]
            self._voice_play_btn.set_sensitive(False)
            self._voice_status.set_text("Playing a sample…")

            def _worker():
                played = False
                try:
                    from mimosa.avatar.voice_library import VoiceAuditioner

                    played = VoiceAuditioner().play_sample(voice_id)
                except Exception:  # pragma: no cover - defensive
                    played = False
                GLib.idle_add(self._apply_voice_sample_result, played)

            threading.Thread(target=_worker, name="mimosa-voice-sample",
                             daemon=True).start()

        def _apply_voice_sample_result(self, played: bool) -> bool:
            self._voice_play_btn.set_sensitive(True)
            if played:
                self._voice_status.set_text("")
            else:
                self._voice_status.set_text(
                    "Voice samples can't be played during setup — your chosen "
                    "voice will work normally once running.")
            return False  # one-shot idle callback

        # -- avatar step (custom UI: one-click preset picker) --------------

        def _render_avatar(self) -> None:
            """Render the "Your Avatar" step: a set of one-click starter
            avatars (Feminine / Masculine / Neutral / classic circle) as a
            radio group, plus a preview placeholder. The optional AI generator
            entry is intentionally left for Settings to keep first-run simple.
            """
            prompt = Gtk.Label(
                label="Pick a starter avatar — you can fine-tune or generate a "
                      "custom one later in Settings.",
                xalign=0, wrap=True)
            self._fields_box.append(prompt)

            self._avatar_radios = {}
            current = self._controller.get_selected_avatar_preset()
            # Ensure the pre-selected default (a friendly character, not the
            # classic circle) is actually applied to the working config, so a
            # user who accepts the default still ends up with a character avatar
            # after finishing setup. `set_active` runs before `connect` below,
            # so the toggle handler wouldn't fire for the default otherwise.
            self._controller.select_avatar_preset(current)
            group_leader = None
            for pid, label, desc in self._controller.avatar_preset_options():
                radio = Gtk.CheckButton(label=label)
                if group_leader is None:
                    group_leader = radio
                else:
                    radio.set_group(group_leader)
                if pid == current:
                    radio.set_active(True)
                radio.connect("toggled", self._on_avatar_preset_toggled, pid)
                self._fields_box.append(radio)
                sub = Gtk.Label(label=desc, xalign=0, wrap=True)
                sub.add_css_class("dim-label")
                sub.set_margin_start(28)
                self._fields_box.append(sub)
                self._avatar_radios[pid] = radio

            # Live preview placeholder. A full sprite preview lands with the
            # renderer work (item #8); for now show a friendly status line so
            # the selection feels acknowledged.
            self._avatar_preview = Gtk.Label(xalign=0, wrap=True)
            self._avatar_preview.add_css_class("dim-label")
            self._avatar_preview.set_margin_top(8)
            self._fields_box.append(self._avatar_preview)
            self._refresh_avatar_preview(current)

        def _on_avatar_preset_toggled(self, radio, preset_id) -> None:
            if not radio.get_active():
                return
            applied = self._controller.select_avatar_preset(preset_id)
            self._refresh_avatar_preview(applied)

        def _refresh_avatar_preview(self, preset_id) -> None:
            if getattr(self, "_avatar_preview", None) is None:
                return
            if preset_id == "circle":
                self._avatar_preview.set_text(
                    "MimOSA will show the classic animated listening circle.")
            else:
                voice = self._controller.get_selected_voice() or "the default voice"
                self._avatar_preview.set_text(
                    f"A {preset_id} character avatar, paired with {voice}.")

        # -- wake-word step (custom UI) ------------------------------------

        def _render_wakeword(self) -> None:
            """Build the custom wake-word name entry, live analysis & choice."""
            self._ww_analysis_seq = 0

            # Name entry.
            entry_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            entry_row.append(Gtk.Label(label="Wake word", xalign=0))
            entry_row.append(Gtk.Box(hexpand=True))
            self._ww_entry = Gtk.Entry()
            self._ww_entry.set_placeholder_text("e.g. Jarvis")
            self._ww_entry.set_hexpand(True)
            self._ww_entry.set_text(self._controller.get_custom_wake_word_name() or "")
            self._ww_entry.connect("changed", self._on_ww_name_changed)
            entry_row.append(self._ww_entry)
            self._fields_box.append(entry_row)

            # Live analysis card.
            self._ww_analysis = Gtk.Label(xalign=0, wrap=True)
            self._ww_analysis.add_css_class("dim-label")
            self._ww_analysis.set_margin_top(4)
            self._fields_box.append(self._ww_analysis)

            self._fields_box.append(Gtk.Separator(
                orientation=Gtk.Orientation.HORIZONTAL))

            # Training-preference radios.
            prompt = Gtk.Label(label="How would you like to set this up?",
                               xalign=0)
            prompt.set_margin_top(4)
            self._fields_box.append(prompt)

            self._ww_pref_radios = {}
            current_pref = self._controller.get_training_preference()
            group_leader = None
            for key, label, desc in self._controller.training_preference_options():
                radio = Gtk.CheckButton(label=label)
                if group_leader is None:
                    group_leader = radio
                else:
                    radio.set_group(group_leader)
                if key == current_pref:
                    radio.set_active(True)
                radio.connect("toggled", self._on_ww_pref_toggled, key)
                self._fields_box.append(radio)
                sub = Gtk.Label(label=desc, xalign=0, wrap=True)
                sub.add_css_class("dim-label")
                sub.set_margin_start(28)
                self._fields_box.append(sub)
                self._ww_pref_radios[key] = radio

            # Render the initial analysis (for any pre-filled name).
            self._refresh_ww_analysis()

        def _on_ww_name_changed(self, entry) -> None:
            name = entry.get_text()
            self._controller.set_custom_wake_word_name(name)
            self._refresh_ww_analysis()
            # Typing a name nudges the choice toward training if still on the
            # default "mimosa" preference, so the entry doesn't feel ignored.

        def _on_ww_pref_toggled(self, radio, key) -> None:
            if not radio.get_active():
                return
            self._controller.set_training_preference(key)

        def _refresh_ww_analysis(self) -> None:
            """Recompute and display the analysis for the current name."""
            name = (self._ww_entry.get_text() or "").strip()
            if not name:
                self._ww_analysis.set_text(
                    "Type a name above and I'll tell you how well it'll work."
                )
                return
            analysis = self._controller.analyze_custom_name(name)
            cap = self._controller.hardware_capability()
            try:
                time_text = analysis.estimated_time_text(cap)
            except Exception:
                time_text = "a little while"
            difficulty = analysis["difficulty"]
            prob = int(round(float(analysis["success_probability"]) * 100))
            lines = [
                f"“{name}” — {difficulty.title()} to train "
                f"(~{prob}% success).",
                analysis["phonetic_analysis"].get("summary", ""),
                f"Estimated training time: {time_text}.",
            ]
            for warning in analysis["warnings"]:
                lines.append(f"⚠ {warning}")
            if analysis.get("recommendation"):
                lines.append(analysis["recommendation"])
            self._ww_analysis.set_text("\n".join(p for p in lines if p))

        # -- finish step (custom UI) ---------------------------------------

        def _render_finish(self) -> None:
            """Render the final step, offering to drop a desktop shortcut."""
            # Keep any FieldSpec-driven fields the finish step may declare.
            for spec in self._controller.current_step.fields:
                row, widget = self._build_field(spec)
                self._fields_box.append(row)
                self._widgets.append((spec, widget))

            check = Gtk.CheckButton(label="Create a desktop shortcut")
            check.set_active(True)
            check.set_tooltip_text(
                "Place a MimOSA launcher on your desktop so you can start it "
                "with a double-click."
            )
            self._desktop_shortcut_check = check
            self._fields_box.append(check)

        def _build_field(self, spec):
            # M2: the per-field info-icon + hover tooltip has been removed in
            # favour of the always-visible guidance sidebar. The field row now
            # carries just its label and editing widget; the "what & why" help
            # for every option lives in the step's sidebar text.
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            label = Gtk.Label(label=spec.label, xalign=0)
            row.append(label)
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

"""User interface package for MimOSA (GTK4 -- Phase 3).

Gives MimOSA a visible, personality-driven presence on the desktop: a small,
circular, always-on-top **avatar window** whose animation mirrors the assistant's
state (idle, listening, processing, speaking).

Modules (M3.1 -- GTK4 window design)
------------------------------------
* :mod:`mimosa.ui.ui_config` -- persistent UI preferences (size, position,
  opacity, theme, animation). Pure, no GTK; fully unit-tested.
* :mod:`mimosa.ui.state_bridge` -- thread-safe bridge mapping ``VoiceState`` to a
  UI :class:`~mimosa.ui.state_bridge.UIState` and marshaling updates onto the GTK
  main loop via ``GLib.idle_add``.
* :mod:`mimosa.ui.avatar_renderer` -- Cairo renderer with per-state animations;
  the animation math is pure and testable independent of any drawing backend.
* :mod:`mimosa.ui.avatar_assets` -- locator for optional SVG/PNG avatar assets.
* :mod:`mimosa.ui.avatar_window` -- the GTK4 frameless, transparent, draggable
  avatar window (defined only when GTK 4 is importable).
* :mod:`mimosa.ui.window_manager` -- window lifecycle, position persistence, and
  multi-monitor logic (pure geometry separated from GTK glue).
* :mod:`mimosa.ui.environment` -- GUI/headless detection (``DISPLAY`` + GTK 4).
* :mod:`mimosa.ui.app` -- application entry point; chooses GUI vs headless and
  runs the voice loop with or without the avatar.

Modules (M3.2 -- enhanced TTS / viseme lip-sync)
------------------------------------------------
* :mod:`mimosa.ui.viseme_mapper` -- canonical :class:`~mimosa.ui.viseme_mapper.Viseme`
  set and a configurable IPA/eSpeak phoneme -> viseme table. Pure data.
* :mod:`mimosa.ui.audio_sync` -- :class:`~mimosa.ui.audio_sync.AudioVisemeSync`
  tracks playback position over a viseme timeline (injectable clock, latency
  compensation, pause/resume, adaptive resync). Pure, testable.
* :mod:`mimosa.ui.mouth_animator` -- Cairo mouth-shape rendering with smooth,
  frame-rate-independent interpolation between visemes (math is pure; drawing
  lazily imports cairo).

  The companion :mod:`mimosa.voice.phoneme_extractor` (voice package) turns text
  + synthesized audio into a :class:`~mimosa.voice.phoneme_extractor.VisemeTimeline`,
  preferring phonemes and falling back to amplitude analysis -- all on-device.

Modules (M3.3 -- settings & preferences UI)
-------------------------------------------
* :mod:`mimosa.ui.settings_logic` -- the GTK-free
  :class:`~mimosa.ui.settings_logic.SettingsController`: holds a working copy of
  the configuration, exposes the declarative page/field descriptors the dialog
  renders, validates edits, manages skill enable/priority, detects when a
  restart is required, and commits changes back to the config manager. Fully
  unit-testable without a display.
* :mod:`mimosa.ui.settings_dialog` -- the GTK4 multi-page Settings window
  (Voice, Skills, System Integration, Privacy & Data, Appearance, About) with
  Apply / Cancel / OK. A thin view over the controller; defined only when GTK 4
  is importable (``SettingsDialog is None`` on headless machines). Backed by
  :mod:`mimosa.utils.config`.

Design guarantees (consistent with the rest of MimOSA)
------------------------------------------------------
* **Optional & graceful.** Importing this package never requires GTK. The
  avatar runs only when both a display server *and* GTK 4 are present; otherwise
  MimOSA runs headless with **no** GTK imported.
* **Privacy-first.** No telemetry, no tracking; preferences stay in a local
  JSON file under ``~/.config/mimosa``.
* **Thread-safe.** The voice loop runs off the GTK main thread; all widget
  updates are marshaled through ``GLib.idle_add``.
"""

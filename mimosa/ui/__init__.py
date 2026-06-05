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

# UI Architecture — GTK4 Desktop Avatar (M3.1 + M3.2 lip-sync)

This document describes MimOSA's desktop presence: a small, circular,
always-on-top **avatar window** whose animation mirrors the assistant's voice
state. It is the first milestone of **Phase 3 — UI & Avatar**.

> **Design principle:** the UI is *entirely optional*. MimOSA runs headless
> (voice/CLI) when GTK 4 or a display server is unavailable, importing **no**
> GTK in that case. No telemetry, no tracking — preferences stay in a local
> JSON file. This mirrors the privacy-first, graceful-degradation philosophy of
> the rest of the project.

---

## 1. Where the UI sits

```
   ┌──────────────────────────────────────────────────────────────┐
   │                       MimOSAApplication (app.py)               │
   │                                                                │
   │   GUI available?  ── no ──►  run_headless()  ── VoiceLoop.run()│
   │        │ yes                                  (main thread)    │
   │        ▼                                                       │
   │   Gtk.Application ──► AvatarWindow (GTK main thread)           │
   │        │                     ▲                                 │
   │        │                     │ set_state(UIState)              │
   │        ▼                     │ (via GLib.idle_add)             │
   │   VoiceLoop.run() ──► StateBridge.notify(VoiceState)           │
   │   (worker thread)            (thread-safe marshaling)          │
   └──────────────────────────────────────────────────────────────┘
```

The voice loop runs on a **worker thread** so the GTK main loop stays
responsive; the bridge marshals state changes back onto the main thread.

---

## 2. Modules

| Module | Responsibility | GTK? |
|--------|----------------|:----:|
| `mimosa/ui/ui_config.py` | Persistent preferences (size, position, opacity, theme, animation). Atomic JSON save, clamping validation. | ❌ pure |
| `mimosa/ui/state_bridge.py` | `UIState` enum + thread-safe `VoiceState → UI` marshaling via `GLib.idle_add`. | lazy |
| `mimosa/ui/avatar_renderer.py` | Cairo renderer; per-state animations. Animation **math is pure** & testable. | lazy cairo |
| `mimosa/ui/avatar_assets.py` | Locator for optional SVG/PNG assets (`data/avatars/`). | ❌ pure |
| `mimosa/ui/window_manager.py` | Window lifecycle, **position persistence**, multi-monitor geometry. Pure geometry separated from Gdk glue. | lazy |
| `mimosa/ui/environment.py` | GUI/headless detection (`DISPLAY`/`WAYLAND_DISPLAY` + GTK 4 probe). | guarded probe |
| `mimosa/ui/avatar_window.py` | The GTK4 frameless, transparent, draggable avatar window. Defined only when GTK 4 is importable (`HAS_GTK`). | ✅ required |
| `mimosa/ui/app.py` | Entry point; chooses GUI vs headless; integrates the voice loop. | deferred |

---

## 3. Visual states & animations

The renderer animates four states (color-coded via the active theme) plus a
disabled state, with smooth eased cross-fades between them:

| State | Animation | Default accent (aurora) |
|-------|-----------|--------------|
| **IDLE** | Gentle breathing/pulsing glow | cool blue |
| **LISTENING** | Expanding concentric rings | teal/green |
| **PROCESSING** | Pulsing "thinking" dots | violet |
| **SPEAKING** | Reactive level bar (audio-reactive via `set_audio_level`, or synthesized) | amber |
| **DISABLED** | Dim static core (loop stopped) | gray |

**Separation of concerns:** all animation values (`breathing_scale`,
`ring_progress`, `thinking_dots`, `speaking_level`, color blending, easing) are
**pure functions of the accumulated phase**, so they're unit-tested without any
drawing backend. Only `AvatarRenderer.draw(cr, w, h)` touches Cairo, and it
imports `cairo` lazily (no-op if unavailable).

Frames are driven by `GLib.timeout_add` at a configurable FPS (default 30);
each tick advances the phase by the real elapsed `dt` (clamped for stability)
and calls `queue_draw`.

### Themes

`aurora` (default), `ember`, and `mono` live in `ui_config.py` as RGB tuples
consumed directly by Cairo — no per-theme image assets needed.

---

## 4. Window behavior

* **Frameless & transparent** — `set_decorated(False)` + transparent CSS
  background; only the circular avatar is painted (compositing required for
  true transparency).
* **Always-on-top** — best-effort (`set_keep_above` where the backend exposes
  it; X11 honors it, strict Wayland may not).
* **Draggable** — `Gtk.GestureDrag`; the new position is persisted via the
  window manager → `UIConfig.save`.
* **Right-click menu** — `Gtk.PopoverMenu` with **Settings**, **Hide**, **Quit**.
* **Escape** — hides the window (`EventControllerKey`).

### Position persistence & multi-monitor

`window_manager.py` keeps the *logic* pure and testable:

* `MonitorInfo`, `select_monitor`, `clamp_to_monitor`, `resolve_position`
  compute startup placement (saved position clamped onto the preferred monitor,
  or centered when no position is saved).
* `WindowManager.query_monitors` parses Gdk's monitor model (injectable for
  tests); failures degrade to "let the backend place it".

### Wayland vs X11

Many Wayland compositors don't let clients set absolute positions. MimOSA
therefore **persists** position always and **restores** it where the backend
permits; on strict Wayland the compositor may place the window itself. The
saved value is retained for environments that do honor it.

---

## 5. Threading & safety

* The voice loop exposes `add_state_listener` / `remove_state_listener`. A
  listener raising an exception is logged and ignored — **the UI can never
  crash the voice loop**.
* `StateBridge.notify` runs on the **worker thread**; it maps the state and
  schedules the UI callback via `GLib.idle_add` so widget mutation happens on
  the **GTK main thread**. With no GLib (headless), it dispatches synchronously.

---

## 6. Headless / CLI fallback

`is_gui_available()` = `has_display()` **and** `gtk_available()`. When false (or
`--no-gui` is passed), `app.py` runs the voice loop directly with **no GTK
import at all**.

```bash
python -m mimosa.ui.app --check     # print environment readiness and exit
python -m mimosa.ui.app --no-gui    # force headless (voice/CLI only)
python -m mimosa.ui.app             # auto: GUI if available, else headless
```

---

## 7. Installing GTK4 on Kubuntu 26.04

```bash
sudo apt install python3-gi gir1.2-gtk-4.0 \
                 libgirepository1.0-dev libcairo2-dev gobject-introspection
pip install -r requirements.txt     # pulls PyGObject + pycairo
```

> On older GLib typelibs (GLib < 2.80, e.g. Debian 12) pin `PyGObject<3.52`
> (already constrained in `requirements.txt`). Kubuntu 26.04 ships a newer GLib
> where current PyGObject works out of the box.

---

## 8. Testing

UI tests are split by dependency so CI stays green with or without GTK:

| Suite | Needs | Notes |
|-------|-------|-------|
| `test_ui_config.py` | — | Validation, persistence, atomic save, env path. |
| `test_state_bridge.py` | — | State mapping, fake-GLib dispatch, headless fallback, real VoiceLoop subscription, exception safety. |
| `test_avatar_renderer.py` | pycairo (drawing tests skip if absent) | Easing/blend/tick math; draw paints pixels for every state. |
| `test_window_manager.py` | — | Monitor selection, clamping, `resolve_position`, persistence, injected Gdk model. |
| `test_ui_environment.py` | — | Display/GTK detection (monkeypatched); asset locator. |
| `test_ui_app.py` | — | VoiceLoop listener hooks, CLI parsing, headless dispatch. |
| `test_avatar_window.py` | GTK 4 **+ display** | Skipped unless `is_gui_available()`; run under `xvfb-run` in CI. |

```bash
# headless-safe subset (always runs):
python -m pytest tests/test_ui_*.py tests/test_state_bridge.py \
                 tests/test_avatar_renderer.py tests/test_window_manager.py

# full UI incl. real GTK window (needs a display / Xvfb):
xvfb-run -a python -m pytest tests/test_avatar_window.py
```

---

## 9. Lip-sync (M3.2)

M3.2 adds an animated mouth that syncs to spoken audio. Phonemes are extracted
from the **local** Piper/eSpeak engine (or, failing that, from the audio's own
energy envelope), mapped to a small set of visemes, placed on a timeline, and
played back against a monotonic clock that drives a Cairo mouth during the
`SPEAKING` state. It is fully on-device and degrades to the M3.1 speaking bar
whenever phonemes, Cairo, or a display are unavailable — a lip-sync fault can
never crash the voice loop.

```
 PiperTTS.synthesize_with_visemes() ─► (wav_bytes, VisemeTimeline)   [worker thread]
                                              │
                       AvatarWindow.set_viseme_timeline(timeline)     [GTK thread]
                                              ▼
              AvatarRenderer ── AudioVisemeSync(clock) ── MouthAnimator ─► Cairo mouth
```

Added modules: `viseme_mapper.py` (pure), `phoneme_extractor.py` (voice,
owns the timeline types), `audio_sync.py`, `mouth_animator.py`; `tts.py` gains
`synthesize_with_visemes()` and `avatar_renderer.py` drives the mouth.
See **[VISEME_SYSTEM.md](VISEME_SYSTEM.md)** for the full design, mapping table,
timing model, fallback chain, and config fields (`lipsync_enabled`,
`viseme_speed`, `mouth_style`, `lipsync_latency`, `lipsync_debug`).

---

## 10. What's next (Phase 3)

* Wire `synthesize_with_visemes()` through the state bridge so live replies
  drive the mouth end-to-end (renderer API is ready).
* Settings dialog (wire the right-click **Settings** action to a live editor),
  including the lip-sync controls.
* Sprite/expression layers on top of the procedural renderer.
* System-tray companion and an optional chat window.

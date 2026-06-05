# Companion UI — Tray, Chat & Expressions (M4.3)

Phase 4 adds three **optional** companion features to MimOSA's desktop presence.
Each follows the project's split-architecture rule: a pure, headless-testable
controller plus a thin GTK shell that returns `None` when no display is present.

---

## System-tray companion

A panel **status icon** with a menu that reflects MimOSA's live state.

| Menu item | Action |
|-----------|--------|
| *(status)* | Non-clickable label: Idle / Listening… / Thinking… / Speaking… / Stopped |
| Show / Hide avatar | Toggle the avatar window |
| Open chat… | Open the text-chat window |
| Mute / Unmute microphone | Toggle microphone capture |
| Settings… | Open the multi-page settings dialog |
| Quit MimOSA | Shut down cleanly |

- **Logic:** `mimosa/ui/tray_logic.py` — `TrayController` exposes
  `menu_items()`, `activate(item_id)`, `status_label()`, `icon_name()` and
  `tooltip()`. Desktop actions are wired via `TrayCallbacks`; callback errors
  are logged and swallowed so a bad handler can't take down the tray.
- **Shell:** `mimosa/ui/tray.py` — GTK + optional AppIndicator.
  `create_system_tray()` returns `None` headlessly.

---

## Optional chat window

Type to MimOSA using the **same brain** as the voice loop.

- **Logic:** `mimosa/ui/chat_logic.py` — `ChatController.send(text)` routes the
  message through the shared `IntentRouter` and records the turn in the shared
  `ConversationManager`, so typed and spoken history stay coherent.
  - Empty input is ignored.
  - Router errors are surfaced as a failed assistant message (never a crash).
  - With no router attached, it replies with a graceful "not connected" notice.
  - `clear()` clears the visible log only; `reset()` clears the conversation
    memory too; `to_transcript()` exports plain text.
- **Shell:** `mimosa/ui/chat_window.py` — scrolling `Gtk.TextView` + entry.
  `open_chat_window()` returns `None` headlessly.

---

## Sprite / expression layers

An expression model and composable layer stack **on top of** the procedural
avatar renderer — no image assets are loaded by the core logic.

- **`Expression`** — `neutral`, `happy`, `thinking`, `listening`, `speaking`,
  `surprised`, `confused`, `sleepy`. Helpers:
  - `Expression.from_state(UIState)` — default expression per assistant state.
  - `Expression.from_sentiment("positive" | "negative" | …)` — emotion hint.
  - `Expression.from_value(...)` — tolerant coercion (unknown → `neutral`).
- **`SpriteSheet`** — describes a grid of equally-sized frames (geometry +
  named frames) and computes per-frame pixel rects. **Metadata only** — it never
  opens an image.
- **`ExpressionLayer` / `LayerFrame` / `compose_layers()`** — declarative layers
  composited by z-order, with hidden/transparent culling and sprite-rect
  resolution.
- **`ExpressionController`** — maps the current `UIState` (or an explicit
  override) to an expression, drives a **deterministic blink** animation via an
  injectable clock, and emits the ordered layer stack for drawing.

A drawing/integration layer (the renderer or a sprite-capable theme) consumes
`LayerFrame` objects and performs the actual blitting; the core stays light and
fully unit-testable.

---

## Integration

`mimosa/ui/app.py` wires all three into the GUI activation path, each guarded so
the headless voice path is unaffected:

- The voice→avatar state bridge also updates the `ExpressionController` and the
  tray icon.
- The tray's *Open chat* and *Settings* items reuse the app's existing handlers;
  the chat controller is wired to the live voice-loop router & conversation.

---

## Testing

| File | Tests |
|------|-------|
| `tests/test_expressions.py` | 54 |
| `tests/test_tray.py` | 23 |
| `tests/test_chat.py` | 22 |

All run fully offline; the GTK shell tests assert graceful headless degradation.

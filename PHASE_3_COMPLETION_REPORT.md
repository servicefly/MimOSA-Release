# Phase 3 Completion Report — UI & Avatar

**Phase:** 3 (UI & Avatar)
**Milestones:** M3.1 (GTK4 Window Design) · M3.2 (Enhanced TTS / Viseme Lip-Sync) · M3.3 (Settings & Preferences UI)
**Final milestone branch:** `milestone/m3.3` → merged `--no-ff` into `develop`
**Status:** ✅ **Phase 3 Complete** — **721 tests passing** (offline, hermetic)
**Tags:** `m3.3-complete`, `phase-3-complete`

---

## 1. Phase 3 at a glance

| Milestone | Theme | New tests | Cumulative | Status |
|-----------|-------|:---------:|:----------:|:------:|
| M3.1 | GTK4 circular always-on-top avatar window | — | 518 | ✅ |
| M3.2 | Enhanced TTS / on-device viseme lip-sync | +164 | 682 | ✅ |
| M3.3 | Settings & preferences UI + unified config | +39 | **721** | ✅ |

Every milestone preserves the project invariants: **privacy-first** (no
telemetry; all preferences and conversation data stay on the device),
**optional & graceful** (the entire UI is skipped on a headless machine — MimOSA
falls back to voice/CLI with **no** GTK imported), and **hermetic tests** (no
display, audio device, network, or model required).

> The GTK-gated tests (`test_avatar_window.py`, `test_settings_dialog.py`) run
> only when both GTK 4 and a display server are present; on headless CI they are
> skipped, and the remainder of the suite passes unchanged.

---

## 2. Scope delivered (M3.3)

| Requirement | Status | Where |
|-------------|:------:|-------|
| Multi-page GTK4 settings dialog (Stack + sidebar, Apply/Cancel/OK, modal) | ✅ | `mimosa/ui/settings_dialog.py` |
| Voice page (wake word + sensitivity, STT model, TTS voice/speed, audio I/O) | ✅ | Voice page / `VoiceSettings` |
| Skills page (enable/disable, priority ordering, future custom skills) | ✅ | Skills page / `SkillsSettings` |
| System Integration page (file/app/system toggles, safe mode, confirmations) | ✅ | System page / `SystemIntegrationSettings` |
| Privacy & Data page (provider abacus/local/**none**, history limit, clear, summary) | ✅ | Privacy page / `PrivacySettings` |
| UI Preferences page (size, opacity, theme, animation, on-top, lip-sync) | ✅ | Appearance page / `UIConfig` |
| About page (version, system summary, license, credits, updates placeholder) | ✅ | About page |
| Unified config manager — load/save/validate/**migration**/**thread-safe** | ✅ | `mimosa/utils/config.py` |
| Settings access — context menu + **Ctrl+,** shortcut + programmatic | ✅ | `avatar_window.py`, `app.py` |
| Comprehensive tests; all prior tests still pass | ✅ | `tests/test_app_config.py`, `test_settings_logic.py`, `test_settings_dialog.py` |
| Docs (`USER_GUIDE.md`, `UI_ARCHITECTURE.md`, README) + dialog screenshots | ✅ | `docs/`, `README.md`, `docs/images/` |

---

## 3. Components (M3.3)

### Unified configuration (`mimosa/utils/config.py`) — pure
- **`AppConfig`** tree with section dataclasses `VoiceSettings`,
  `SkillsSettings`, `SystemIntegrationSettings`, `PrivacySettings`, embedding the
  existing **`UIConfig`** as the `ui` section (no duplication).
- Per-section `validate()` **clamps** numbers and resets unknown enums to
  defaults; a hand-edited file never bricks the app.
- **Versioned** (`CONFIG_VERSION`) with `_migrate()` — e.g. a pre-versioned flat
  `UIConfig` dump is nested under `ui` on load.
- **`AppConfigManager`** — `threading.RLock`-guarded load/save/get/update/reset,
  **atomic** writes (temp + `os.replace`), **observers**, and **mirroring** of
  the `ui` section to the legacy `ui.json`. Path via `MIMOSA_CONFIG` → XDG →
  `~/.config/mimosa/settings.json`. **No GTK / audio / ML imports.**

### Settings controller (`mimosa/ui/settings_logic.py`) — pure
- Declarative `PageSpec`/`FieldSpec` model (`build_page_specs()`): six pages, all
  fields with kind/bounds/choices/`restart`/help — the dialog renders from these.
- Holds a **working copy**; `apply()` commits + persists, `cancel()` reverts,
  `reset_defaults()` restores defaults (working copy only).
- `is_dirty()`, `changed_fields()`, `restart_required()` (wake word, STT model,
  audio devices, LLM provider).
- Skills: `skill_rows()` (priority-ordered), `set_skill_enabled()`,
  `move_skill()`, `set_skill_order()`; **injectable `skills_provider`** so real
  `IntentRouter` skills show in the GUI while tests use defaults.
- `clear_history()` hook (wired to `ConversationManager.clear`) and
  `reset_avatar_position()`.

### Settings dialog (`mimosa/ui/settings_dialog.py`) — GTK (guarded)
- `GtkStack` + `GtkStackSidebar`; Voice/System/Privacy/Appearance pages built
  from field specs (Switch / DropDown / SpinButton / Entry); Skills page is a
  `GtkListBox` with per-row enable switch + ▲/▼ priority buttons; About page
  shows version + system summary.
- Modal, transient-for the avatar; **Apply/OK/Cancel**; live restart banner and
  privacy summary; `open_settings_dialog(...)` helper returns `None` when GTK is
  unavailable. `SettingsDialog is None` on headless machines.

### Integration
- `MimOSAApplication` now owns an `AppConfigManager` (source of truth) and the
  `_on_settings()` hook opens the dialog with `skills_provider`,
  `on_clear_history`, and a system summary wired to the live voice loop;
  on apply it calls `AvatarWindow.apply_config()` for **live preview** of UI
  changes.
- `AvatarWindow` gains **Ctrl+,** (open Settings) and `apply_config()`
  (hot-apply size/opacity/theme/animation without a restart).

---

## 4. Tests

```
tests/test_app_config.py ............. 18 passed   (sections, migration, manager, threads)
tests/test_settings_logic.py ......... 17 passed   (pages, edits, skills, apply/cancel)
tests/test_settings_dialog.py ........  4 passed*   (GTK-gated; skipped on headless)
full suite ........................... 721 passed   (with GTK4 + display)
```

- **+39 new tests** over the M3.2 baseline of 682.
- Fully hermetic: `MIMOSA_CONFIG` / `MIMOSA_UI_CONFIG` redirected to `tmp_path`;
  no real `~/.config` touched; no display required for the config/controller
  tests; the GTK dialog tests build inside a real `Gtk.Application` activation
  with a timer-driven quit (can't hang) and **skip** without GTK 4 + a display.
- Coverage includes clamping/validation, version migration, atomic save +
  `ui.json` mirroring, observers, **concurrent updates** (8 threads), working-copy
  isolation, restart detection, skill enable/priority + provider injection, the
  clear-history hook, and end-to-end Apply persistence through the dialog widget.

---

## 5. Privacy & safety notes

- **100% local.** All settings persist to `~/.config/mimosa/settings.json`; the
  Privacy page can disable the LLM entirely (`provider = none`) for fully
  offline, skills-only operation. **No telemetry, ever.**
- **Hard guardrails.** System-integration toggles and **safe mode** let the user
  switch off file/app/system capabilities and force confirmations; safe mode
  pins the destructive/system confirmations on.
- **Never crashes.** Loads degrade to defaults on a missing/corrupt file; saves
  are atomic; observer and hook failures are caught; the dialog is a no-op when
  GTK is unavailable.
- **Thread-safe by construction.** The config manager is lock-guarded so the GTK
  main loop and the background voice thread can share one instance.

---

## 6. Dependencies

- **No new required dependencies.** The settings stack adds only standard-library
  use (`json`, `threading`, `tempfile`, `dataclasses`). GTK 4 / PyGObject remain
  **optional** — required only to *show* the dialog, exactly like the rest of the
  UI. The config and controller layers import **no** GTK.

---

## 7. Phase 3 deliverables (cumulative)

- **M3.1** — `ui_config.py`, `state_bridge.py`, `avatar_renderer.py`,
  `avatar_assets.py`, `avatar_window.py`, `window_manager.py`, `environment.py`,
  `app.py`; the optional desktop avatar with per-state animation and a headless
  fallback.
- **M3.2** — `viseme_mapper.py`, `phoneme_extractor.py`, `audio_sync.py`,
  `mouth_animator.py` (+ `tts.synthesize_with_visemes()` and renderer mouth
  drive); on-device lip-sync with an amplitude fallback.
- **M3.3** — `utils/config.py`, `ui/settings_logic.py`, `ui/settings_dialog.py`
  (+ `app.py` / `avatar_window.py` integration); the unified config manager and
  the multi-page settings dialog.

---

## 8. What's next (Phase 4)

- Sprite / expression layers atop the procedural renderer; system-tray companion
  and an optional chat window.
- A **first-run setup wizard** (the settings infrastructure is ready) and a
  working **check-for-updates** action on the About page.
- Custom user-defined skills (the `SkillsSettings.custom` slot is reserved).

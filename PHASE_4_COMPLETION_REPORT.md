# Phase 4 Completion Report — Extensibility & Companion UI

**Phase:** 4 (Extensibility & Companion UI)
**Milestones:** M4.1 (Custom user-defined skills) · M4.2 (First-run setup wizard + check-for-updates) · M4.3 (System tray, chat window & expression layers)
**Final milestone branch:** `milestone/m4.3` → merged `--no-ff` into `develop`
**Status:** ✅ **Phase 4 Complete** — **849 tests passing headless** (offline, hermetic; +196 over Phase 3's headless baseline)

---

## 1. Phase 4 at a glance

| Milestone | Theme | New tests | Cumulative (headless) | Status |
|-----------|-------|:---------:|:---------------------:|:------:|
| M4.1 | User-defined custom skills (declarative, no-code) | +48 | 701 | ✅ |
| M4.2 | First-run setup wizard + check-for-updates | +49 | 750 | ✅ |
| M4.3 | System tray, optional chat window, expression layers | +99 | **849** | ✅ |

> **On the test count.** The numbers above are the **headless** suite that runs
> with no display, audio device, network, or model. GTK-gated widget tests
> (`test_avatar_window.py`, `test_settings_dialog.py`, and the GTK paths of
> `test_ui_app.py` / `test_tray.py` / `test_chat.py`) are **skipped** when no
> display server is present (10 skipped here) and run additionally on a desktop
> session. Every milestone preserves the project invariants: **privacy-first**
> (no telemetry; everything stays on-device), **optional & graceful** (all new
> UI degrades to nothing on a headless machine), and **hermetic tests**.

---

## 2. Scope delivered

| Phase 4 requirement (per Phase 3 report §8 / `UI_ARCHITECTURE.md` §11) | Status | Where |
|------------------------------------------------------------------------|:------:|-------|
| Custom user-defined skills (the reserved `SkillsSettings.custom` slot) | ✅ | `mimosa/skills/custom_skill.py`, router Tier 1c |
| First-run setup wizard | ✅ | `mimosa/ui/setup_wizard.py` (+ dialog shell) |
| Check-for-updates action on About page | ✅ | `mimosa/utils/updates.py`, `SettingsController.check_for_updates()` |
| System-tray companion | ✅ | `mimosa/ui/tray_logic.py` (+ `tray.py` shell) |
| Optional chat window | ✅ | `mimosa/ui/chat_logic.py` (+ `chat_window.py` shell) |
| Sprite / expression layers atop the procedural renderer | ✅ | `mimosa/ui/expressions.py` |

See the per-milestone reports (`M4.1_…`, `M4.2_…`, `M4.3_COMPLETION_REPORT.md`)
for full detail.

---

## 3. Architecture continuity

Phase 4 follows the same split established in Phase 3:

- **Pure logic** modules (`custom_skill`, `updates`, `setup_wizard`,
  `tray_logic`, `chat_logic`, `expressions`) import no GTK / audio / network /
  ML at module load and are fully unit-testable headlessly.
- **Thin GTK shells** (`setup_wizard_dialog`, `tray`, `chat_window`) are guarded
  with `HAS_GTK` and define widget classes only when GTK is importable; their
  `open_*` / `create_*` entry points return `None` headlessly.
- Dependencies (LLM provider, router, conversation manager, time source,
  update fetcher) are **injected**, enabling deterministic tests.

A new autouse fixture (`tests/conftest.py`) isolates on-disk configuration to a
temporary directory so no test — including the headless first-run wizard — can
write to the developer's real `~/.config/mimosa`.

---

## 4. Privacy posture (unchanged, reinforced)

- Custom skills are **data, not code** — no `eval`/`exec`/shell-out.
- The update check is the **only** new outbound request: opt-in, public releases
  endpoint, no identifying data, and it never raises on failure.
- The setup wizard makes **fully-offline** (`provider = none`) a one-click
  choice; the chat window reuses existing local memory with no new storage.

---

## 5. Verification

```bash
pytest            # 849 passed, 10 skipped (headless)
```

All work is committed on `develop` via `--no-ff` merges of `milestone/m4.1`,
`milestone/m4.2`, and `milestone/m4.3`.

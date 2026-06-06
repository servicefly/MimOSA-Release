# Phase 8 — Polish & Testing (developer guide)

Phase 8 is the final pre-release milestone. It does not add user-facing
"features" so much as make the whole assistant **production-ready**: graceful
errors, observable logs, runtime wiring of the Phase 7 services, personalization,
accessibility, packaging, and comprehensive end-to-end tests.

This document is for contributors. End users want **[INSTALL.md](../INSTALL.md)**
and the **[User Guide](USER_GUIDE.md)**.

---

## Sub-milestones

| ID | Theme | Key modules |
|----|-------|-------------|
| M8.1 | Graceful error UX | `mimosa/core/error_reporter.py` |
| M8.2 | Logging & rotation | `mimosa/utils/logging_setup.py`, `mimosa/memory/paths.py` |
| M8.3 | Runtime service wiring | `mimosa/core/runtime.py` |
| M8.4a | Personalization | `mimosa/utils/config.py` (`PersonalitySettings`), `mimosa/ui/setup_wizard.py`, `mimosa/skills/greeting_skill.py` |
| M8.4b | Settings UI + accessibility | `mimosa/ui/settings_logic.py` |
| M8.4c | Packaging | `pyproject.toml`, `install.sh`, `uninstall.sh` |
| M8.4d | End-to-end tests | `tests/test_integration_e2e.py` |

---

## M8.1 — Graceful error UX

`mimosa/core/error_reporter.py`:

- `friendly_message(exc)` maps an exception to a calm, spoken-friendly string.
  **Keyword hints in the message are checked first, then the exception type**
  (because broad base classes like `OSError` would otherwise swallow specific
  cases).
- `ErrorReport` is a dataclass with `.spoken()`; `ErrorReporter` (`learner=None`,
  `log=True`) exposes `.report()` and `.record_fix()` and **never raises**.
- The reporter is wired into `IntentRouter` via `_enrich_errors()`: when a skill
  fails (`BaseSkill.run` already converts exceptions into a `success=False`
  result with `metadata['error']`) and the M7.3 `ErrorFixLearner` knows a fix,
  the spoken reply is enriched with that suggestion and tagged with the error
  signature.

**Design rule:** a raw traceback must never reach the user. It goes to the log
only.

---

## M8.2 — Logging & rotation

`mimosa/utils/logging_setup.py`:

- `configure_logging(verbose, to_file, log_path)` installs a console handler plus
  a `RotatingFileHandler` (`MAX_BYTES = 1 MiB`, `BACKUP_COUNT = 3`, `delay=True`)
  at `paths.log_file_path()` → `~/.local/share/mimosa/logs/mimosa.log`.
- Idempotent (guarded by `_MIMOSA_HANDLER_FLAG`) and degrades to console-only if
  the file handler can't be created.
- The format is privacy-safe (no message content). `describe_log_location()`
  returns the resolved path for `mimosa --check`.

`mimosa/memory/paths.py` gained `LOGS_DIR` / `LOG_FILE` constants and
`log_dir()` / `log_file_path()` / `ensure_log_dir()`.

---

## M8.3 — Runtime service wiring

`mimosa/core/runtime.py` is the single place that assembles the optional Phase 7
stack according to the user's `TasksSettings`:

- `AppServices.from_config(config, ...)` builds the `ErrorFixLearner` +
  `ErrorReporter` **always**, the `ResourceMonitor` only when
  `resource_monitoring` is on, and the `TaskQueue` only when
  `background_tasks_enabled` is on (wiring the resource gate between them).
- `start()` / `shutdown()` are idempotent; the class is a context manager.
- `run_maintenance(vacuum=True)` applies `privacy.data_retention_days` (purge)
  then compacts the conversation store. Returns a `MaintenanceReport`. Fully
  defensive — maintenance never raises into the caller.
- **Dependency-injection friendly:** pass `:memory:` DB paths and injected
  `clock` / `sampler` for hermetic tests.

`mimosa/ui/app.py` lazily builds `AppServices` (the `services` property),
attaches the `ConversationStore`, calls `start()` + `run_maintenance()` on
startup (headless and GUI), and shuts services down on exit.

---

## M8.4a — Personalization

`PersonalitySettings` (in `mimosa/utils/config.py`): `user_name`,
`assistant_name`, `user_pronouns`, `verbosity` (`brief`/`balanced`/`detailed`),
`greet_by_name`. `validate()` trims/coerces; `display_user()` and `greeting()`
produce safe strings. Wired into `AppConfig` like any other section.

The setup wizard gains a `STEP_PERSONALIZE` ("Get to Know MimOSA") step inserted
after Welcome. `GreetingSkill` accepts `personality=` and personalizes both the
LLM system prompt and the local fallback greeting.

---

## M8.4b — Settings UI + accessibility

`mimosa/ui/settings_logic.py` adds three pages — **Personalization**,
**Background Tasks**, **Web Research** — each field carrying a label **and** an
inline help string for screen readers. The `SettingsController` resolves
sections generically by `getattr`, so the new config sections need **zero**
controller changes.

---

## M8.4c — Packaging

- `pyproject.toml`: package `mimosa-assistant`, version `1.0.0rc1`, console
  script `mimosa = mimosa.ui.app:main`, extras `[voice]` / `[ui]` / `[semantic]`
  / `[dev]` / `[all]`. **No `[tool.pytest.ini_options]`** — `pytest.ini` remains
  the single source of truth.
- `install.sh`: Python ≥ 3.10 check, venv creation, `pip install -e ".[…]"`,
  `--with-voice` / `--with-ui` / `--with-all` / `--venv`, prints config/data/log
  locations.
- `uninstall.sh`: removes the venv; `--purge` deletes the data + config dirs
  (with confirmation; `--yes` to skip).

---

## M8.4d — End-to-end tests

`tests/test_integration_e2e.py` exercises the **assembled** app hermetically
(no GTK, no audio, no network, `llm_provider=None`): personalized multi-turn
routing, the graceful-error path with learned-fix enrichment, the
`AppServices` maintenance lifecycle, and the `mimosa --check` entry point.

---

## Testing

The full suite is offline and hermetic. Run:

```bash
pip install -e ".[dev]"
python -m pytest -q
```

Conventions to preserve when adding tests:

- Use `:memory:` SQLite or `tmp_path` files — never touch the real data dir.
- Inject `clock` / `sampler` / `gate` / `learner` rather than sleeping or
  hitting real resources.
- The autouse `conftest.py` fixture isolates the **config** dir via
  `XDG_CONFIG_HOME`; isolate the **data** dir yourself with explicit paths.

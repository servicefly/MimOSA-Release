# Phase 7 Completion Report — Advanced Features

**Phase:** 7 (Advanced Features)
**Milestones:** M7.1 (Background task queue) · M7.2 (Resource monitoring & prediction) · M7.3 (Local error-fix learning) · M7.4 (Conversational task control, router & config wiring)
**Milestone branch:** `milestone/m7.1` → merged `--no-ff` into `develop`
**Status:** ✅ **Phase 7 Complete** — **1308 tests passing headless** (offline, hermetic; +138 over Phase 6's headless baseline)

---

## 1. Phase 7 at a glance

| Milestone | Theme | New tests | Cumulative (headless) | Status |
|-----------|-------|:---------:|:---------------------:|:------:|
| M7.1 | SQLite background task queue + cooperative pause/resume/cancel | +42 | 1212 | ✅ |
| M7.2 | `psutil` resource monitoring + pressure prediction + admission gate | +26 | 1238 | ✅ |
| M7.3 | Local error-fix learning (on `PreferenceLearner`) | +26 | 1264 | ✅ |
| M7.4 | Task-control skill, router & config wiring | +44 | **1308** | ✅ |

> **On the test count.** This is the **headless** suite that runs with no
> display, audio device, network, or ML model. `psutil` is treated as
> **optional**: every resource test injects a scripted sampler, and the queue
> tests inject a clock and use `:memory:`/`tmp_path` SQLite, so the suite is
> deterministic with **no threads, sleeps, or hardware sampling**. GTK-gated
> widget tests remain skipped without a display (10 skipped). Every milestone
> preserves the project invariants: **privacy-first** (no telemetry, all state
> on-device), **local-first** (queue, monitoring, and learning are entirely
> in-process), and **graceful degradation** (missing `psutil`, a failing
> resource gate, a crashed run, or a disabled learner all degrade to a safe
> default rather than an error).

---

## 2. Scope delivered

| Phase 7 requirement (per Phase 6 handover §4.1) | Status | Where |
|-------------------------------------------------|:------:|-------|
| Background task queue system | ✅ | `mimosa/tasks/task_queue.py` |
| Concurrency limits (max 2 by default) | ✅ | `task_queue.py` (`_claim_next`, `DEFAULT_MAX_CONCURRENT`) |
| Conversational pause / resume / cancel | ✅ | `task_queue.py` (`TaskControl`, `pause`/`resume`/`cancel`), `skills/task_skill.py` |
| Crash/restart recovery of in-flight tasks | ✅ | `task_queue.py` (`_recover_orphans`) |
| Resource monitoring using `psutil` | ✅ | `mimosa/tasks/resource_monitor.py` |
| Resource **prediction** + admission gate for the queue | ✅ | `resource_monitor.py` (`predict_pressure`, `can_start_task`, `gate`) |
| Local error-fix learning (builds on M5.2) | ✅ | `mimosa/tasks/error_learner.py` |
| User-facing skill + router + config integration | ✅ | `skills/task_skill.py`, `core/intent_router.py`, `utils/config.py` |

See the per-milestone reports (`M7.1_...` through `M7.4_COMPLETION_REPORT.md`)
and `docs/ADVANCED_FEATURES.md` for the full user/developer guide.

---

## 3. Architecture (where M7 sits)

```
          user speech ──► IntentRouter ──► TaskControlSkill ("tasks")
                                                │
                                                ▼
                                          ┌───────────┐
                          enqueue/control │ TaskQueue │  SQLite: tasks.db
                                          └─────┬─────┘
                                  _claim_next │  ▲  resource_gate()
                                               ▼  │
                                        handler(task, control)
                                               │        ▲
                              control.checkpoint()      │ defer under load
                                               │        │
                                               ▼   ┌───────────────┐
                          TaskPaused/Cancelled │   │ResourceMonitor│ psutil*
                                               │   └───────────────┘
                                               ▼
                                   on failure ──► ErrorFixLearner ──► PreferenceLearner
                                                  (suggest known fix next time)

                          * psutil optional — absent ⇒ gate fails open
```

---

## 4. Files

### New
- `mimosa/tasks/__init__.py` — package exports for all M7 symbols
- `mimosa/tasks/task_queue.py` — M7.1
- `mimosa/tasks/resource_monitor.py` — M7.2
- `mimosa/tasks/error_learner.py` — M7.3
- `mimosa/skills/task_skill.py` — M7.4
- `tests/test_task_queue.py`, `tests/test_resource_monitor.py`,
  `tests/test_error_learner.py`, `tests/test_task_skill.py`
- `docs/ADVANCED_FEATURES.md`
- `M7.1`–`M7.4` + this `PHASE_7_COMPLETION_REPORT.md`, `PHASE_7_HANDOVER.md`

### Modified
- `mimosa/memory/paths.py` — `TASKS_DB` + `tasks_db_path()`
- `mimosa/core/intent_router.py` — `INTENT_TASK`, `_TASK_PATTERNS`, classify +
  registration
- `mimosa/utils/config.py` — `TasksSettings` + constants + skill-order + wiring
- `tests/test_intent_router.py`, `tests/test_app_config.py` — new coverage

---

## 5. Invariants preserved

- **Privacy-first / local-first.** Queue, monitoring, and learning are all
  in-process; nothing is transmitted and there is no telemetry.
- **Graceful degradation.** Optional `psutil`, failing gate, crashed run, and
  disabled learner all resolve to safe defaults.
- **Hermetic tests.** Injected clock/sampler/gate/learner; `:memory:` SQLite;
  no threads/sleeps/network/hardware.
- **Settings-driven.** Everything user-configurable lives in `TasksSettings`
  with validation, clamping, and backward-compatible loading.

---

## 6. What's next — Phase 8 (Polish & Testing)

See `PHASE_7_HANDOVER.md` for the detailed brief. Headline items: end-to-end
integration testing, graceful error UX, performance/memory tuning,
accessibility, log rotation, clean install/uninstall, plus the cross-cutting
wiring of the task queue into the host application and exposing the new
`TasksSettings` toggles in the settings UI.

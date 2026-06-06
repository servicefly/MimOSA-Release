# MimOSA — Project Handover (Post Phase 7)

**Date:** June 6, 2026
**Repository:** GitHub `servicefly/MimOSA` (private)
**Branch:** `develop` · **Latest tag:** `phase-7-complete`
**Test status:** **1308 passed, 10 skipped** — fully offline / hermetic (headless)

---

## 0. TL;DR

**Phase 7 (Advanced Features) is complete** and merged into `develop`
(`--no-ff` merge of `milestone/m7.1`, tags `m7.1-complete` … `m7.4-complete`,
`phase-7-complete`). It delivered the spec's **Milestone 7** in four parts: a
SQLite-backed background task queue with cooperative pause/resume/cancel,
`psutil` resource monitoring with pressure prediction and an admission gate,
local error-fix learning on top of the M5.2 preference learner, and the
conversational task-control skill plus router/config wiring — all privacy-first,
all degrading gracefully on a bare Python install.

The next natural body of work is the spec's **Milestone 8 — Polish & Testing**.

---

## 1. Phase 7 — COMPLETE ✅

| Milestone | Tag | Delivered |
|-----------|-----|-----------|
| **M7.1 — Background task queue** | `m7.1-complete` | `TaskQueue` (SQLite `tasks.db`), `Task`/`TaskStatus`/`TaskControl`, cooperative `checkpoint()` → `TaskPaused`/`TaskCancelled`, `≤ max_concurrent` (2) gating, global pause, optional resource gate (fail-open), orphan recovery on reopen. `paths.tasks_db_path()`. |
| **M7.2 — Resource monitoring** | `m7.2-complete` | `ResourceMonitor` over `psutil` (optional, `HAS_PSUTIL`), `ResourceSnapshot`, rolling history, `predict_pressure()` (linear extrapolation, clamped), `can_start_task()` (fails open), `gate()` for the queue. |
| **M7.3 — Local error-fix learning** | `m7.3-complete` | `normalize_error()` (hex/hash/path/quote/number → signature), `ErrorFixLearner` on M5.2 `PreferenceLearner` (`record_fix`/`suggest_fix`/`candidates`/`explain`/`forget`), confidence-gated, safe no-op when disabled. |
| **M7.4 — Task control, router & config** | `m7.4-complete` | `TaskControlSkill` (intent `task_control`, no LLM), `INTENT_TASK` + scoped `_TASK_PATTERNS` (won't hijack "stop the music"), `TasksSettings` config (validate/clamp/roundtrip), default-skill-order entry. |

**Architecture continuity:** the new `mimosa/tasks/` package imports no
GTK/audio/ML at load; `psutil` is optional and capability-guarded
(`HAS_PSUTIL`); the queue's clock and resource gate, the monitor's sampler, and
the learner are all injected; error learning reuses the M5.2 PreferenceLearner.
Tests inject a clock/sampler/gate, use `:memory:`/`tmp_path` SQLite and
in-memory learners — nothing touches threads-in-anger, the network, real
hardware, or real user data.

---

## 2. The numbering: spec milestones vs. our "phases"

The product spec counts **Milestones 1–8**. Internally we have been shipping one
"phase" per spec milestone, so **Phase N == spec Milestone N**. Phase 7 == spec
Milestone 7 (Advanced Features). The next is **spec Milestone 8 — Polish &
Testing** (referred to below as Phase 8).

---

## 3. Acceptance criteria met

- ✅ Background task queue system (SQLite-durable, restart-recoverable).
- ✅ Concurrency limit (default 2, configurable 1–8).
- ✅ Conversational pause / resume / cancel (cooperative, by-name/all/single).
- ✅ Resource monitoring using `psutil` + **prediction** + queue admission gate.
- ✅ Local error-fix learning (on-device, generalising, confidence-gated).
- ✅ User-facing skill + router intent + config section, fully wired.
- ✅ 1308 tests green, offline & hermetic; invariants preserved.

---

## 4. What needs to happen now (recommended roadmap)

### 4.1 NEXT PHASE (recommended) — Polish & Testing  *(spec Milestone 8)*
- **End-to-end integration testing** across milestones (voice → router → skill →
  queue/research/memory), ideally a few scripted multi-turn scenarios.
- **Graceful error UX:** consistent, friendly spoken/printed messages on every
  failure path; ensure no traceback ever reaches the user.
- **Performance/memory tuning:** queue polling interval, history sizes, DB
  vacuum/`purge` cadence; profile the hot paths.
- **Accessibility:** keyboard navigation, screen-reader labels, high-contrast in
  the companion UI.
- **Log rotation** and a single, documented log location.
- **Clean install / uninstall:** package metadata, data-dir creation/teardown,
  first-run vs. upgrade behaviour.

### 4.2 Cross-cutting / parallelizable (do alongside M8)
- **Wire the task queue into the host app.** Construct a real `TaskQueue` at
  startup (using `tasks_db_path()`), register the app's long-running handlers
  (indexing, research, file ops), inject it into `TaskControlSkill`, and start
  the worker **only when `TasksSettings.background_tasks_enabled`** — mirroring
  how M5 memory / M6 research are injected rather than hard-wired. Build the
  `ResourceMonitor.gate()` and pass it as the queue's `resource_gate` when
  `resource_monitoring` is on.
- **Hook error-fix learning into the failure path.** When a handler fails and is
  later retried successfully, call `ErrorFixLearner.record_fix()`; surface
  `suggest_fix()` in the error UX ("last time this was fixed by …").
- **Settings-UI toggles** for the new `TasksSettings` (enable, max-concurrent,
  monitoring + thresholds, error-learning), plus the still-pending M6 web-search
  toggle.
- **Wire research into the app** (carried over from Phase 6 §4.3): construct an
  online `ResearchEngine` and inject into `ResearchSkill` when web search is on.

### 4.3 Optional follow-ups within Advanced Features
- **Priority scheduling / dependencies** between tasks (the `priority` field is
  already stored; add ordering policies and simple "after X" dependencies).
- **Task progress in the companion UI** (a live list with per-task progress bars
  driven by `queue.list_tasks()`/`active_tasks()`).
- **Richer prediction:** per-resource thresholds, EWMA instead of linear
  extrapolation, disk/IO sampling.
- **Persisted error-fix store** browsing/editing in the UI (`candidates()` /
  `explain()` / `forget()` already provide the data).

---

## 5. How to start the next session (verbatim-ready)

```
We're continuing MimOSA — a privacy-first, voice-controlled Linux AI assistant.
Project: /home/ubuntu/osa_project/   GitHub: servicefly/MimOSA (branch develop)

1. Verify state:
   cd /home/ubuntu/osa_project && git checkout develop && git pull
   git tag                 # expect phase-1..7-complete
   python3 -m pytest -q    # expect 1308 passed, 10 skipped

2. Begin the next phase — Polish & Testing (spec Milestone 8):
   create branch milestone/m8.1, then: end-to-end integration tests, graceful
   error UX, performance/memory tuning, accessibility, log rotation, clean
   install/uninstall. In parallel, wire the M7 task queue + M6 research engine
   into the host app behind their settings toggles and add the settings-UI
   controls. Keep the suite green & offline. Merge --no-ff into develop with
   tags. Do NOT auto-merge to main.

Conventions: BaseSkill/SkillResult, BaseLLMProvider, local-first privacy, graceful
degradation, Conventional Commits, milestone branch → --no-ff merge into develop.
```

### Standing reminders
- 🔒 **Privacy:** actions run locally; only text (never file contents/audio) may
  reach the LLM, and only when allowed. The task queue, resource monitor, and
  error learner are entirely on-device with no telemetry.
- 🧪 **Tests** stay **offline & hermetic** on a headless VM (no audio/display/
  network; LLM mocked via `FakeLLM`; search via `StaticBackend`; `psutil` mocked
  via injected sampler; DBs/config in `tmp_path` / `:memory:`).
- 🌳 **Git:** Conventional Commits; push with the token masked; don't commit
  `.abacus.donotdelete` or auto-generated `.pdf`/`.docx`; never force-push
  `main`/`develop`; never auto-merge PRs. `main` is left for the user's
  develop→main release PR.
- 🔑 **API key** lives in `osa_project/.env` (git-ignored); needed only for live scripts.
- 💻 The VM is ephemeral — **GitHub is the durable source of truth.**
- 🔗 Private-repo access for the Abacus.AI GitHub App:
  <https://github.com/apps/abacusai/installations/select_target>.

---

## 6. Bottom line

- **Phase 7 (spec Milestone 7 — Advanced Features) is complete and on GitHub**
  (`develop`, tag `phase-7-complete`, 1308 tests green): background task queue,
  resource monitoring & prediction, and local error-fix learning, all driven by
  a conversational task-control skill.
- Everything is **privacy-first** (on-device, no telemetry) and **degrades
  gracefully** without optional deps (`psutil`), under load, across restarts,
  and when features are disabled.
- **The clear next body of work is the spec's Milestone 8 — Polish & Testing**,
  with host-app wiring of the task queue + research engine and settings-UI
  toggles runnable in parallel.
```

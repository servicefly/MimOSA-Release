# MimOSA Advanced Features (Milestone 7)

A user & developer guide to MimOSA's advanced runtime features: a **background
task queue**, **resource monitoring & prediction**, and **local error-fix
learning** — all on-device, all degradable.

> **TL;DR** — MimOSA can run long jobs in the background while staying
> conversational. Ask *"what are you working on?"*, say *"pause that"* or
> *"carry on"*, and it manages a local queue that respects your machine's load.
> It also quietly learns which fixes resolved which errors so it can suggest
> them next time. **Nothing leaves your machine and there is no telemetry.**

---

## 1. Architecture

```
          user speech ──► IntentRouter ──► TaskControlSkill ("tasks")
                                                │  enqueue / pause / resume / cancel / status
                                                ▼
                                          ┌───────────┐
                                          │ TaskQueue │   SQLite: tasks.db   (durable, local)
                                          └─────┬─────┘
                                  _claim_next │  ▲  resource_gate()  (optional)
               (≤ max_concurrent, not paused) ▼  │
                                        handler(task, control)
                                               │        ▲
                              control.checkpoint()      │ "defer: machine busy"
                          control.set_progress(0.5)     │
                                               │   ┌───────────────┐
                          TaskPaused/Cancelled │   │ResourceMonitor│  psutil* → CPU/mem/load
                                               │   │ predict_pressure()  can_start_task()
                                               ▼   └───────────────┘
                                   on failure ──► ErrorFixLearner ──► PreferenceLearner (M5.2)
                                                  normalize_error() → signature → known fix

                          * psutil is OPTIONAL — if absent the gate fails OPEN (work always allowed)
```

---

## 2. Background task queue (`mimosa/tasks/task_queue.py`)

A persistent, local scheduler. Tasks are stored in `tasks.db` (under the app
data dir, via `mimosa.memory.paths.tasks_db_path()`), so they survive restarts.

### Concepts

| Thing | What it is |
|-------|-----------|
| **Task** | a unit of work: `id`, `name`, `kind`, `payload`, `status`, `priority`, `progress`, `message`, `result`, `error`, timestamps |
| **handler** | a function `handler(task, control) -> result` registered per `kind` |
| **TaskControl** | passed to handlers: `set_progress()`, `set_message()`, `checkpoint()` |
| **TaskStatus** | `QUEUED / RUNNING / PAUSED / COMPLETED / FAILED / CANCELLED` |

### Writing a handler

```python
from mimosa.tasks import TaskQueue

queue = TaskQueue()                      # uses tasks.db; or TaskQueue(db_path=":memory:")

def index_mail(task, control):
    for i, batch in enumerate(load_batches(task.payload["folder"])):
        control.checkpoint()             # cooperatively pause/cancel here
        process(batch)
        control.set_progress((i + 1) / total)
    return {"indexed": total}

queue.register_handler("index_mail", index_mail)
task = queue.enqueue("Index Mail", "index_mail", payload={"folder": "Inbox"})

queue.start()        # background worker thread (or call queue.drain() synchronously in tests)
```

- **Cooperative control.** Call `control.checkpoint()` at safe points. When the
  user pauses or cancels, the next checkpoint raises `TaskPaused` /
  `TaskCancelled`, so work stops cleanly without corrupting partial state.
- **Concurrency.** At most `max_concurrent` (default **2**) tasks run at once.
- **Recovery.** If MimOSA restarts while a task was `RUNNING`, it is re-queued
  on reopen (`_recover_orphans`).

### Programmatic control

`pause(id)`, `resume(id)`, `cancel(id)`, `pause_all()`, `resume_all()`,
`list_tasks(status=...)`, `active_tasks()`, `counts()`, `running_count()`,
`purge()`, `clear_all()`, `close()`.

---

## 3. Resource monitoring (`mimosa/tasks/resource_monitor.py`)

Samples CPU / memory / load via `psutil`, keeps a short rolling history, and
**predicts** near-term pressure so the queue can avoid starting work that would
overload the machine.

```python
from mimosa.tasks import ResourceMonitor, TaskQueue

monitor = ResourceMonitor(cpu_threshold=85.0, mem_threshold=85.0)
queue = TaskQueue(resource_gate=monitor.gate())   # defer new work under load
```

- `sample()` / `latest()` / `history()` — current and recent snapshots.
- `is_busy()` — True if the latest sample is over threshold.
- `predict_pressure()` — linear extrapolation of the recent trend (clamped 0–100).
- `can_start_task(predicted=True)` — **fails open**: returns True whenever
  resources are unknown (e.g. no `psutil`), so monitoring can only *defer*, never
  deadlock.
- `gate()` — a zero-arg callable suitable for `TaskQueue(resource_gate=...)`.

> **`psutil` is optional.** Without it, `HAS_PSUTIL` is `False`, snapshots report
> `available = False`, and the gate allows all work.

---

## 4. Local error-fix learning (`mimosa/tasks/error_learner.py`)

Learns which fix resolved which error — entirely on-device, on top of the M5.2
`PreferenceLearner`.

```python
from mimosa.tasks import ErrorFixLearner
from mimosa.memory.preference_learner import PreferenceLearner

learner = ErrorFixLearner(PreferenceLearner(db_path=":memory:"))

learner.record_fix("FileNotFoundError: /home/me/x.txt", "create the missing file")
# ... a similar error happens later (different path) ...
sug = learner.suggest_fix("FileNotFoundError: /tmp/other.log")
if sug:
    print(sug.fix, sug.confidence)     # -> "create the missing file", 0.6+
```

- `normalize_error()` strips the volatile bits (hex, hashes, **paths**, quoted
  strings, numbers) so similar errors share one learnable **signature**.
- Suggestions are only returned above `confidence_threshold` (default **0.6**).
- `candidates()`, `explain()`, and `forget()` let you inspect or erase learned
  pairings. Disabled / no learner ⇒ every call is a safe no-op.

---

## 5. Talking to the queue (`mimosa/skills/task_skill.py`)

The `tasks` skill (intent `task_control`, no LLM) understands:

| Say... | Effect |
|--------|--------|
| "what are you working on?" / "what's in the queue?" | status of active tasks |
| "pause that" / "pause everything" / "pause the indexing" | pause one / all / by-name |
| "carry on" / "continue" / "resume everything" | resume (generic ⇒ resume all) |
| "cancel that" / "cancel everything" / "abort the download" | cancel one / all / by-name |

Ambiguous requests (e.g. *"pause it"* with several tasks running) get a
clarifying question rather than a guess. Note the router is scoped so
system/media commands like *"stop the music"* are **not** captured here.

---

## 6. Configuration quick-reference (`TasksSettings`)

`config.tasks.*` (all validated & clamped on load):

| Setting | Default | Range / notes |
|---------|---------|---------------|
| `background_tasks_enabled` | `True` | master switch for the queue |
| `max_concurrent` | `2` | clamped to **1–8** |
| `resource_monitoring` | `True` | use `psutil` gate when available |
| `cpu_threshold` | `85.0` | clamped to **10–100** (%) |
| `mem_threshold` | `85.0` | clamped to **10–100** (%) |
| `learn_error_fixes` | `True` | enable error-fix learning |

Old config files (pre-M7, no `tasks` key) load cleanly with these defaults.

---

## 7. Privacy & degradation summary

- **No network, no telemetry.** Queue, monitoring, and learning are all
  in-process and on-device.
- **Everything degrades safely.** Missing `psutil` ⇒ gate fails open; a crashed
  run ⇒ re-queued; a handler exception ⇒ captured as `FAILED` (worker keeps
  going); a disabled/absent learner ⇒ no-op.
- **User in control.** Disable any of the three features via `TasksSettings`, and
  use `forget()` / `cancel` / `clear_all` to erase state at any time.

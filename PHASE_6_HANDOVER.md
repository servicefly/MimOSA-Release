# MimOSA — Project Handover (Post Phase 6)

**Date:** June 5, 2026
**Repository:** GitHub `servicefly/MimOSA` (private)
**Branch:** `develop` · **Latest tag:** `phase-6-complete`
**Test status:** **1170 passed, 10 skipped** — fully offline / hermetic (headless)

---

## 0. TL;DR

**Phase 6 (Research Capabilities) is complete** and merged into `develop`
(`--no-ff` merge of `milestone/m6.1`, tags `m6.1-complete` … `m6.4-complete`,
`phase-6-complete`). It delivered the spec's **Milestone 6** in four parts:
web search & balanced source aggregation, multi-source synthesis with
perspective labeling, token-budget negotiation with cost-pattern learning, and
the orchestrator/skill/router/config wiring — all privacy-first, all degrading
gracefully on a bare Python install.

The next natural body of work is the spec's **Milestone 7 — Advanced Features**,
followed by Polish & Testing (M8).

---

## 1. Phase 6 — COMPLETE ✅

| Milestone | Tag | Delivered |
|-----------|-----|-----------|
| **M6.1 — Web search & source aggregation** | `m6.1-complete` | `Source`/`SourceCategory` model, offline domain→perspective classification (curated table + `.gov`/`.edu` suffix rules), `summarize_perspectives` (gap reporting), `StaticBackend` + key-free `DuckDuckGoBackend`, `SearchClient` (dedupe + per-category cap). Offline by default. |
| **M6.2 — Multi-source synthesis** | `m6.2-complete` | `ResearchSynthesizer`: groups sources by perspective, LLM path (balance-enforcing prompt) + deterministic extractive fallback, names missing perspectives, cites sources. |
| **M6.3 — Token-budget negotiation** | `m6.3-complete` | `count_tokens` (tiktoken→heuristic), `TokenBudget`, `BudgetNegotiator.plan()` (trim→drop preserving perspective diversity), cost-pattern learning via M5.2 (`record_usage`/`suggest_budget`). |
| **M6.4 — Orchestrator, skill & wiring** | `m6.4-complete` | `ResearchEngine` (search→budget→synthesize + privacy routing), `ResearchSkill`, `INTENT_RESEARCH` + router patterns, `ResearchSettings` config. |

**Architecture continuity:** the new `mimosa/research/` package imports no
GTK/audio/ML at load; `requests`/`bs4`/`tiktoken` are optional and
capability-guarded (`HAS_REQUESTS`, `HAS_BS4`, `HAS_TIKTOKEN`); search backends
and the LLM provider are injected; the engine reuses the M5.4 Privacy Guard for
local routing and the M5.2 PreferenceLearner for cost learning. Tests use
`StaticBackend`, a `FakeLLM`, and `:memory:` learners — nothing touches the
network or real user data.

**Docs:** `docs/RESEARCH_SYSTEM.md` (user/developer guide); per-milestone reports
`M6.1_…`–`M6.4_COMPLETION_REPORT.md`; `PHASE_6_COMPLETION_REPORT.md`.

**Verification:** `pytest` → **1170 passed, 10 skipped** (headless).

---

## 2. The numbering: spec milestones vs. our "phases"

The spec uses **Milestones 1–8**; our phase labels are an internal grouping.

| Our label | Git tag | What it delivered | Spec milestone(s) |
|-----------|---------|-------------------|-------------------|
| **Phase 1** | `phase-1-complete` | setup + LLM abstraction · voice pipeline · intent router + 5 skills | Spec **M1** (partial) |
| **Phase 2** | `phase-2-complete` | file ops · app launch/control · Kubuntu integration | Spec **M4** (Actions) |
| **Phase 3** | `phase-3-complete` | GTK4 avatar · viseme lip-sync · settings UI | Spec **M1** (UI) + M3 (partial) |
| **Phase 4** | `phase-4-complete` | custom skills · setup wizard + updates · tray/chat/expressions | UI/skills polish |
| **Phase 5** | `phase-5-complete` | conversation persistence · preference learning · semantic memory · privacy guard | Spec **M5** (Memory & Context) |
| **Phase 6** | `phase-6-complete` | web search · multi-source synthesis · token-budget negotiation · cost learning | Spec **M6** (Research Capabilities) |

---

## 3. Acceptance criteria met

- ✅ Web search across source types (mainstream/alternative/social/video/think-tank) with balanced perspective labeling.
- ✅ Multi-source synthesis that attributes claims and **names gaps**.
- ✅ **Token-budget negotiation** with a speakable negotiation message.
- ✅ Cost-pattern learning building on the M5.2 preference learner.
- ✅ Privacy-first: web search **off by default**, sensitive queries routed local (M5.4), no telemetry.
- ✅ Graceful degradation & headless operation; suite stays offline/hermetic.

---

## 4. What needs to happen now (recommended roadmap)

### 4.1 NEXT PHASE (recommended) — Advanced Features  *(spec Milestone 7)*
SQLite-backed **background task queue** (max 2 concurrent), **resource
prediction/monitoring** (`psutil` — already a dep used by `system_profiler`),
conversational **pause/resume**, and **local error-fix learning** (reuse the
M5.2 preference learner). Partially scaffolded by `system_optimizer` /
`system_profiler`. The M6 `BudgetNegotiator`/`ResearchEngine` are good models
for the "estimate → negotiate → record" loop. Use the established
`milestone/m7.x` → `--no-ff` into `develop` flow.

### 4.2 Then — Polish & Testing (spec M8)
End-to-end testing across milestones, graceful error UX, performance/memory
tuning, accessibility, log rotation, clean install/uninstall.

### 4.3 Cross-cutting / parallelizable
- **Wire research into the app:** the host app should construct an online
  `ResearchEngine` (with `DuckDuckGoBackend`, the shared provider, the Privacy
  Guard, and the PreferenceLearner) and inject it into `ResearchSkill` **when
  `ResearchSettings.web_search_enabled` is true** — mirroring how M5 memory is
  injected rather than hard-wired. Add a settings-UI toggle for web search.
- Conversational onboarding & personality (spec M2); file-system indexer.

### 4.4 Optional follow-ups within Research
- **More backends:** add a Brave/SearXNG/Bing backend behind the same
  `SearchBackend` contract; `RESEARCH_BACKENDS` + `ResearchSettings.backend`
  already anticipate this.
- **Full-text fetch:** optionally fetch & summarize the linked page body (behind
  the same privacy switch) for deeper synthesis instead of snippets only.
- **Per-perspective citation rendering** in the chat UI.

---

## 5. How to start the next session (verbatim-ready)

```
We're continuing MimOSA — a privacy-first, voice-controlled Linux AI assistant.
Project: /home/ubuntu/osa_project/   GitHub: servicefly/MimOSA (branch develop)

1. Verify state:
   cd /home/ubuntu/osa_project && git checkout develop && git pull
   git tag                 # expect phase-1..6-complete
   python3 -m pytest -q    # expect 1170 passed, 10 skipped

2. Begin the next phase — Advanced Features (spec Milestone 7):
   create branch milestone/m7.1, implement the SQLite-backed background task
   queue (max 2 concurrent) + resource prediction/monitoring (psutil) +
   conversational pause/resume + local error-fix learning (reuse the M5.2
   preference learner). Keep the suite green & offline (mock psutil/clock,
   tmp_path for DBs), document it, merge --no-ff into develop with tags.
   Do NOT auto-merge to main.

Conventions: BaseSkill/SkillResult, BaseLLMProvider, local-first privacy, graceful
degradation, Conventional Commits, milestone branch → --no-ff merge into develop.
```

### Standing reminders
- 🔒 **Privacy:** actions run locally; only text (never file contents/audio) may
  reach the LLM, and only when allowed. Private conversations route local (M5.4).
  Research web search is **off by default** and only the query/snippets ever
  leave the device.
- 🧪 **Tests** stay **offline & hermetic** on a headless VM (no audio/display/
  network; LLM mocked via `FakeLLM`; search via `StaticBackend`; DBs/config in
  `tmp_path` / `:memory:`).
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

- **Phase 6 (spec Milestone 6 — Research Capabilities) is complete and on
  GitHub** (`develop`, tag `phase-6-complete`, 1170 tests green): balanced web
  search, multi-source synthesis, token-budget negotiation, and cost learning.
- Everything is **privacy-first** (web search off by default, sensitive queries
  local) and **degrades gracefully** without optional deps or a network.
- **The clear next body of work is the spec's Milestone 7 — Advanced
  Features**, followed by Polish & Testing (M8), with app wiring of the research
  engine and onboarding runnable in parallel.
```

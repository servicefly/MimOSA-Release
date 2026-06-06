# Phase 6 Completion Report — Research Capabilities

**Phase:** 6 (Research Capabilities)
**Milestones:** M6.1 (Web search & source aggregation) · M6.2 (Multi-source synthesis & balanced perspective labeling) · M6.3 (Token-budget negotiation & cost-pattern learning) · M6.4 (Orchestrator, skill & wiring)
**Milestone branch:** `milestone/m6.1` → merged `--no-ff` into `develop`
**Status:** ✅ **Phase 6 Complete** — **1170 tests passing headless** (offline, hermetic; +145 over Phase 5's headless baseline)

---

## 1. Phase 6 at a glance

| Milestone | Theme | New tests | Cumulative (headless) | Status |
|-----------|-------|:---------:|:---------------------:|:------:|
| M6.1 | Web search & balanced source aggregation | +58 | 1083 | ✅ |
| M6.2 | Multi-source synthesis & perspective labeling | +12 | 1095 | ✅ |
| M6.3 | Token-budget negotiation & cost-pattern learning | +22 | 1117 | ✅ |
| M6.4 | Orchestrator, skill, router & config wiring | +53 | **1170** | ✅ |

> **On the test count.** This is the **headless** suite that runs with no
> display, audio device, network, or ML model. Research deliberately runs on a
> bare Python install: `tiktoken`, `requests`, and `bs4` are all **optional** —
> every test exercises the fallback/offline path (the DuckDuckGo backend is
> tested against a static HTML fixture via an injected fake session; the LLM via
> a `FakeLLM`). GTK-gated widget tests remain skipped without a display
> (10 skipped). Every milestone preserves the project invariants:
> **privacy-first** (web search off by default, no telemetry), **local-first**
> (sensitive queries synthesized on-device; extractive fallback needs no model),
> and **graceful degradation** (each feature works, or cleanly no-ops, without
> its optional deps or network).

---

## 2. Scope delivered

| Phase 6 requirement (per Phase 5 handover §4.1) | Status | Where |
|--------------------------------------------------|:------:|-------|
| Web search across source types (mainstream/alternative/social/video/think-tank) | ✅ | `mimosa/research/sources.py`, `search.py` |
| Multi-source synthesis | ✅ | `mimosa/research/synthesizer.py` |
| Balanced perspective labeling + gap reporting | ✅ | `sources.py` (`summarize_perspectives`), `synthesizer.py` |
| Token-budget **negotiation** | ✅ | `mimosa/research/token_budget.py` |
| Cost-pattern learning (builds on M5.2) | ✅ | `token_budget.py` (`record_usage`/`suggest_budget`) |
| Privacy-aware routing for sensitive research (builds on M5.4) | ✅ | `mimosa/research/research_engine.py` |
| User-facing skill + router + config integration | ✅ | `mimosa/skills/research_skill.py`, `core/intent_router.py`, `utils/config.py` |

See the per-milestone reports (`M6.1_…` through `M6.4_COMPLETION_REPORT.md`) and
`docs/RESEARCH_SYSTEM.md` for full detail.

---

## 3. Architecture (one call, three stages)

```
ResearchSkill.handle("research electric cars")
        │  extract_topic()
        ▼
ResearchEngine.research(topic)
        │
        ├─ 1. SearchClient.search()        (M6.1)  → balanced, deduped Sources
        │       └─ DuckDuckGoBackend / StaticBackend  (offline by default)
        │
        ├─ 2. BudgetNegotiator.plan()      (M6.3)  → trims/drops to fit budget
        │       └─ records cost pattern via PreferenceLearner (M5.2)
        │
        ├─ privacy routing                 (M5.4)  → local provider if sensitive
        │
        └─ 3. ResearchSynthesizer.synthesize()  (M6.2) → balanced answer
                └─ LLM path  OR  deterministic extractive fallback
        ▼
ResearchReport (answer + perspectives + citations + plan)
```

---

## 4. Files

### New modules
| File | Purpose |
|------|---------|
| `mimosa/research/__init__.py` | Package exports |
| `mimosa/research/sources.py` | Source model, category classification, perspective summary |
| `mimosa/research/search.py` | Search backends + `SearchClient` |
| `mimosa/research/synthesizer.py` | Balanced multi-source synthesis |
| `mimosa/research/token_budget.py` | Token counting, budget negotiation, cost learning |
| `mimosa/research/research_engine.py` | Orchestrator |
| `mimosa/skills/research_skill.py` | User-facing research skill |

### Changed modules
| File | Change |
|------|--------|
| `mimosa/core/intent_router.py` | Research intent, patterns, classification, default-skill registration |
| `mimosa/utils/config.py` | `ResearchSettings`, `DEFAULT_SKILL_ORDER` entry |

### New / extended tests
`tests/test_research_sources.py` (39), `tests/test_token_budget.py` (22),
`tests/test_research_search.py` (19), `tests/test_synthesizer.py` (12),
`tests/test_research_engine.py` (11), `tests/test_research_skill.py` (19),
plus additions to `tests/test_intent_router.py` (16) and
`tests/test_app_config.py` (7) — **145 new**, total **1170 passing, 10 skipped**.

### Docs
`docs/RESEARCH_SYSTEM.md`, `M6.1`–`M6.4` + this report, `PHASE_6_HANDOVER.md`.

---

## 5. Privacy & resource posture

- **Web search is off by default** (`ResearchSettings.web_search_enabled =
  False`). A fresh install makes **no** research network calls until the user
  opts in. No telemetry.
- **Sensitive research stays on-device** — the engine routes flagged queries to
  a local provider via the Privacy Guard, and fails *safe* (local extractive) if
  routing errors.
- **Minimal egress** — only the query reaches the search backend; only query +
  snippets reach the (possibly local) model.
- **Bare-install friendly** — `tiktoken`/`requests`/`bs4` optional; cost is
  negotiated and reported; everything degrades gracefully and never raises.

---

## 6. Verification

```bash
cd MimOSA && python3 -m pytest -q
# 1170 passed, 10 skipped
```

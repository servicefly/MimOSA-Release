# MimOSA — Project Handover (Post Phase 4)

**Date:** June 5, 2026
**Repository:** GitHub `servicefly/MimOSA` (private)
**Branch:** `develop` · **Latest tag:** `phase-4-complete`
**Test status:** **849 passed, 10 skipped** — fully offline / hermetic (headless)

---

## 0. TL;DR — answering the two questions you asked

1. **"Do we have a Phase 5 in a planning document?"**
   **No.** The build specification (`MimOSA-Build-Specification.md`) does **not** define
   numbered "Phases" at all. It is organized as **8 Milestones (Milestone 1 → Milestone 8)**,
   followed by a post‑MVP wishlist it happens to call *"Phase 2 Features (After MVP
   Completion)."* There is **no document section named "Phase 4" or "Phase 5."** The
   `Phase 1/2/3/4` labels are an **internal project grouping** layered on top of the spec's
   milestones — they are not in the spec.

2. **"If not, give me a handover acknowledging Phase 4 is complete and what happens next."**
   Done — this document. **Phase 4 is complete and on GitHub** (`develop`, tag
   `phase-4-complete`, 849 tests green). Below: what Phase 4 delivered, the milestone‑vs‑phase
   numbering, and a concrete, spec‑backed roadmap for the next session.

---

## 1. Phase 4 — COMPLETE ✅ (verified on GitHub)

Phase 4 is merged into `develop` and tagged **`phase-4-complete`**. It delivered the
UI/skills polish roadmap that was flagged as "what's next" at the end of Phase 3:

| Milestone | Tag | Delivered |
|-----------|-----|-----------|
| **M4.1 — Custom skills** | `m4.1-complete` | User‑defined custom skills (**data, not code** — no `eval`/`exec`/shell‑out), filling the reserved `SkillsSettings.custom` slot. |
| **M4.2 — Setup wizard & updates** | `m4.2-complete` | First‑run setup wizard (one‑click fully‑offline `provider = none`) + opt‑in **check‑for‑updates** (the only new outbound request; public releases endpoint, no identifying data, never raises on failure). |
| **M4.3 — Companion UI** | `m4.3-complete` | System‑tray companion, optional chat window (reuses existing local memory, no new storage), and sprite/expression layers atop the procedural avatar renderer. |

**Architecture continuity:** pure‑logic modules (`custom_skill`, `updates`, `setup_wizard`,
`tray_logic`, `chat_logic`, `expressions`) import no GTK/audio/network/ML at load and are
fully unit‑testable headlessly; thin GTK shells are `HAS_GTK`‑guarded and return `None`
headlessly; all collaborators are injected. A new autouse `tests/conftest.py` fixture isolates
on‑disk config to a temp dir so no test (incl. the first‑run wizard) touches the real
`~/.config/mimosa`.

**Verification:** `pytest` → **849 passed, 10 skipped** (headless). All work committed on
`develop` via `--no-ff` merges of `milestone/m4.1`, `m4.2`, `m4.3`.

---

## 2. The numbering: spec milestones vs. our "phases"

The spec never says "Phase." It says **Milestone 1…8**. We grouped delivered work into
"phases" for reporting. Exact mapping:

| Our label | Git tag | What it delivered | Spec milestone(s) |
|-----------|---------|-------------------|-------------------|
| **Phase 1** | `phase-1-complete` | M1.1 setup + LLM abstraction · M1.2 voice pipeline · M1.3 intent router + 5 skills | Spec **M1** (Voice + UI, partial) |
| **Phase 2** | `phase-2-complete` | File operations · app launch/control · Kubuntu 26.04 integration | Spec **M4** (Actions) — front‑loaded early |
| **Phase 3** | `phase-3-complete` | GTK4 avatar window · viseme lip‑sync · settings/config UI | Spec **M1** (UI half) + M3 (partial) |
| **Phase 4** | `phase-4-complete` | Custom skills · setup wizard + updates · tray/chat/expressions | UI/skills **polish** (spec M8‑style + Phase‑3 roadmap) |

**Important consequence:** because the spec's **Milestone 4 ("Actions")** was front‑loaded into
our **Phase 2**, and Phase 4 was UI/skills polish, the spec's **Milestone 5 (Memory & Context)**
remains the natural next body of work — whatever number we give the next phase.

---

## 3. What the spec asked for but is NOT yet built

- **Persistent memory & context** (spec M5): conversation‑history DB, preference learning,
  semantic/vector recall — `mimosa/memory/` is still essentially empty. **← biggest gap.**
- **Privacy detector & encryption** (spec M5): hybrid keyword/LLM private‑topic detection,
  SQLCipher private store, context sanitization before API calls.
- **Research capabilities** (spec M6): web search, multi‑source synthesis, token‑budget
  management, cost‑pattern learning.
- **Onboarding & personality** (spec M2): conversational onboarding, persona (partially
  addressed by the M4.2 setup wizard, but not the conversational persona layer).
- **File‑system indexer** (spec M3 / §6): lazy, smart, markdown‑backed index (current
  `file_ops` searches live, no persistent index).
- **Advanced/background features** (spec M7): SQLite task queue, resource prediction,
  conversational pause/resume, error‑fix learning.

---

## 4. What needs to happen now (recommended roadmap)

There is **no pre‑written Phase 5**, so here is the recommended next phase, drawn from the
spec's remaining milestones and ordered by dependency/value.

### 4.1 NEXT PHASE (recommended) — Memory & Context  *(spec Milestone 5)*

Most foundational gap; nearly every higher‑value feature depends on it. Suggested split using
the established `milestone/mX.Y → PR into develop` flow:

- **MX.1 — Conversation persistence:** SQLite `conversations.db` (schema in spec §6: sessions,
  roles, timestamps, `is_private`); retrieve recent context for the LLM; search/recall. Wire
  into `core/conversation_manager.py` (currently in‑session only — note its existing
  `to_memory_records()` seam).
- **MX.2 — Preference learning (silent background):** `learned_preferences` table
  (app/file/conversation patterns) with confidence scores; e.g. "always open PDFs in Okular."
- **MX.3 — Semantic memory:** local embeddings (Chroma + sentence‑transformers, on‑device);
  "have we discussed this before?" retrieval feeding LLM context.
- **MX.4 — Privacy detector & encryption:** hybrid detector (keyword → user‑pattern → LLM) per
  spec §6; encrypted `private.db` (SQLCipher); context sanitization before any API call. Ties
  into the existing Settings Privacy page and `provider = none` switch.

**Acceptance (from spec):** reopen MimOSA → context remembered; "what were we talking about
yesterday?" recalls accurately; medical/financial topics auto‑suggest private mode; private
conversations never leave the device.

### 4.2 Then — Research Capabilities (spec M6)
Web search + multi‑source synthesis (mainstream/alternative/social/video/think‑tank), balanced
perspective labeling, **token‑budget negotiation**, cost‑pattern learning.

### 4.3 Then — Advanced Features (spec M7)
SQLite‑backed background task queue (max 2 concurrent), resource prediction/monitoring
(`psutil`), conversational **pause/resume**, local error‑fix learning. Partially scaffolded by
`system_optimizer`/`system_profiler`.

### 4.4 Cross‑cutting / parallelizable
Conversational onboarding & personality (spec M2); file‑system indexer (lazy markdown index per
spec §6) to make `file_ops` instant & smart.

### 4.5 Finally — Polish & Testing (spec M8)
End‑to‑end testing across milestones, graceful error UX, performance/memory tuning,
accessibility, log rotation, clean install/uninstall.

### 4.6 Post‑MVP (the spec's actual "Phase 2 Features" — long‑term)
3D avatar (GPU), multi‑device sync, Docker code sandboxing, email/calendar integration,
platform expansion (Fedora/Arch/Debian), community skill sharing, advanced research
(video/PDF/repo analysis), automation/workflow builder.

---

## 5. How to start the next session (verbatim‑ready)

```
We're continuing MimOSA — a privacy-first, voice-controlled Linux AI assistant.
Project: /home/ubuntu/osa_project/   GitHub: servicefly/MimOSA (branch develop)

1. Verify state:
   cd /home/ubuntu/osa_project && git checkout develop && git pull
   git tag                 # expect phase-1..4-complete
   python3 -m pytest -q    # expect 849 passed, 10 skipped

2. Begin the next phase — Memory & Context (spec Milestone 5):
   create branch milestone/m5.1, implement conversation persistence
   (SQLite conversations.db per spec §6), wire it into core/conversation_manager
   (use its to_memory_records() seam), keep the suite green & offline (mock the LLM,
   use tmp_path for DBs), document it, and open a PR into develop. Do NOT auto-merge.

Conventions: BaseSkill/SkillResult, BaseLLMProvider, local-first privacy, graceful
degradation, Conventional Commits, milestone branch → PR-into-develop workflow.
```

### Standing reminders
- 🔒 **Privacy:** actions run locally; only text (never file contents) may reach the LLM, and
  only when the user allows. Private conversations must never be sent to the cloud.
- 🧪 **Tests** stay **offline & hermetic** on a headless VM (no audio/display, LLM mocked,
  DBs/config redirected to `tmp_path`).
- 🌳 **Git:** Conventional Commits; push with the token masked; don't commit
  `.abacus.donotdelete` or auto‑generated `.pdf`/`.docx`; never force‑push `main`/`develop`;
  never auto‑merge PRs.
- 🔑 **API key** lives in `osa_project/.env` (git‑ignored); needed only for live scripts.
- 💻 The VM is ephemeral — **GitHub is the durable source of truth.**

---

## 6. Bottom line

- **There is no "Phase 5" (or "Phase 4") in any planning document** — the spec uses Milestones
  1–8 plus a post‑MVP wishlist. Our phase numbers are an internal convenience.
- **Phase 4 is complete and on GitHub** (`develop`, tag `phase-4-complete`, 849 tests green):
  custom skills, setup wizard + check‑for‑updates, and the tray/chat/expression companion UI.
- **The clear next body of work is the spec's Milestone 5 — Memory & Context**, followed by
  Research (M6), Advanced Features (M7), and Polish (M8), with onboarding and the file indexer
  runnable in parallel.

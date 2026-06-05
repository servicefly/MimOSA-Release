# MimOSA — Project Handover (Post Phase 5)

**Date:** June 5, 2026
**Repository:** GitHub `servicefly/MimOSA` (private)
**Branch:** `develop` · **Latest tag:** `phase-5-complete`
**Test status:** **1025 passed, 10 skipped** — fully offline / hermetic (headless)

---

## 0. TL;DR

**Phase 5 (Memory & Context) is complete** and merged into `develop`
(`--no-ff` merge of `milestone/m5.1`, tags `m5.1-complete` … `m5.4-complete`,
`phase-5-complete`). It delivered the spec's **Milestone 5** in four parts:
conversation persistence, preference learning, semantic memory, and a privacy
guard — all local-first, all degrading gracefully on a bare Python install.

The next natural body of work is the spec's **Milestone 6 — Research
Capabilities**, followed by Advanced Features (M7) and Polish (M8).

---

## 1. Phase 5 — COMPLETE ✅

| Milestone | Tag | Delivered |
|-----------|-----|-----------|
| **M5.1 — Conversation persistence** | `m5.1-complete` | Local SQLite `conversations.db` (`sessions` + `messages`, `is_private` flag) wired into `core/conversation_manager.py` by injection; rehydrate on launch; search/purge. |
| **M5.2 — Preference learning** | `m5.2-complete` | `learned_preferences` table with evidence-weighted confidence (dominance × saturation); predict / explain / forget. |
| **M5.3 — Semantic memory** | `m5.3-complete` | Local embeddings with 3-layer degradation (Chroma → fallback cosine store; sentence-transformers → HashingEmbedder); `recall()` for "have we discussed this?". |
| **M5.4 — Privacy guard** | `m5.4-complete` | Hybrid tiered detector (regex → keywords → learned terms → optional local LLM) auto-routing sensitive queries to a local provider; redaction; fail-safe. |

**Architecture continuity:** the new `mimosa/memory/` package imports no
GTK/audio/network/ML at load; heavy back-ends are lazy + capability-guarded
(`HAS_CHROMA`, `HAS_SENTENCE_TRANSFORMERS`); the data dir is resolved once in
`paths.py` (`MIMOSA_DATA → XDG_DATA_HOME → ~/.local/share/mimosa`); persistence
and providers are injected. Tests use `:memory:` / `tmp_path` and the Phase 4
autouse config-isolation fixture, so nothing touches real user data.

**Docs:** `docs/MEMORY_SYSTEM.md` (user/developer guide); per-milestone reports
`M5.1_…`–`M5.4_COMPLETION_REPORT.md`; `PHASE_5_COMPLETION_REPORT.md`.

**Verification:** `pytest` → **1025 passed, 10 skipped** (headless).

---

## 2. The numbering: spec milestones vs. our "phases"

The spec uses **Milestones 1–8** (no "Phases"); our phase labels are an internal
grouping. Updated mapping:

| Our label | Git tag | What it delivered | Spec milestone(s) |
|-----------|---------|-------------------|-------------------|
| **Phase 1** | `phase-1-complete` | setup + LLM abstraction · voice pipeline · intent router + 5 skills | Spec **M1** (partial) |
| **Phase 2** | `phase-2-complete` | file ops · app launch/control · Kubuntu integration | Spec **M4** (Actions) |
| **Phase 3** | `phase-3-complete` | GTK4 avatar · viseme lip-sync · settings UI | Spec **M1** (UI) + M3 (partial) |
| **Phase 4** | `phase-4-complete` | custom skills · setup wizard + updates · tray/chat/expressions | UI/skills polish |
| **Phase 5** | `phase-5-complete` | conversation persistence · preference learning · semantic memory · privacy guard | Spec **M5** (Memory & Context) |

---

## 3. Acceptance criteria met (spec §6)

- ✅ Reopen MimOSA → conversation context remembered (M5.1 rehydrate).
- ✅ "What were we talking about yesterday?" recalls accurately (M5.3 recall).
- ✅ Medical/financial topics auto-suggest private mode (M5.4 + `auto_private_mode`).
- ✅ Private conversations never leave the device (local storage + local routing;
  no new network surface).

---

## 4. What needs to happen now (recommended roadmap)

### 4.1 NEXT PHASE (recommended) — Research Capabilities  *(spec Milestone 6)*

Web search + multi-source synthesis (mainstream / alternative / social / video /
think-tank), balanced perspective labeling, **token-budget negotiation**, and
cost-pattern learning (which can build on the M5.2 preference learner). Use the
established `milestone/m6.x → PR into develop` flow. Keep outbound calls behind
the existing privacy/provider switches; only text leaves the device, and only
when allowed.

### 4.2 Then — Advanced Features (spec M7)
SQLite-backed background task queue (max 2 concurrent), resource
prediction/monitoring (`psutil`), conversational **pause/resume**, local
error-fix learning. Partially scaffolded by `system_optimizer` / `system_profiler`.

### 4.3 Cross-cutting / parallelizable
Conversational onboarding & personality (spec M2); file-system indexer (lazy
markdown index per spec §6) to make `file_ops` instant & smart. The M5.3
semantic store can back "have we touched this file/topic before?".

### 4.4 Finally — Polish & Testing (spec M8)
End-to-end testing across milestones, graceful error UX, performance/memory
tuning, accessibility, log rotation, clean install/uninstall.

### 4.5 Optional follow-ups within Memory
- **Encrypted `private.db` (SQLCipher):** `private_db_path()` and the
  commented `sqlcipher3-binary` dep are already in place; M5.4 flags private
  turns. A future milestone can encrypt at rest.
- **Upgrade embeddings:** install `sentence-transformers` / `chromadb` to turn
  the lexical fallback into true semantic recall — no code change required.

---

## 5. How to start the next session (verbatim-ready)

```
We're continuing MimOSA — a privacy-first, voice-controlled Linux AI assistant.
Project: /home/ubuntu/osa_project/   GitHub: servicefly/MimOSA (branch develop)

1. Verify state:
   cd /home/ubuntu/osa_project && git checkout develop && git pull
   git tag                 # expect phase-1..5-complete
   python3 -m pytest -q    # expect 1025 passed, 10 skipped

2. Begin the next phase — Research Capabilities (spec Milestone 6):
   create branch milestone/m6.1, implement web search + multi-source synthesis
   with balanced perspective labeling and token-budget negotiation; keep outbound
   calls behind the privacy/provider switches; keep the suite green & offline
   (mock the network/LLM, tmp_path for any DBs), document it, open a PR into
   develop. Do NOT auto-merge.

Conventions: BaseSkill/SkillResult, BaseLLMProvider, local-first privacy, graceful
degradation, Conventional Commits, milestone branch → PR-into-develop workflow.
```

### Standing reminders
- 🔒 **Privacy:** actions run locally; only text (never file contents) may reach
  the LLM, and only when allowed. Private conversations must never be sent to the
  cloud — M5.4 enforces local routing for sensitive topics.
- 🧪 **Tests** stay **offline & hermetic** on a headless VM (no audio/display, LLM
  mocked, DBs/config redirected to `tmp_path` / `:memory:`).
- 🌳 **Git:** Conventional Commits; push with the token masked; don't commit
  `.abacus.donotdelete` or auto-generated `.pdf`/`.docx`; never force-push
  `main`/`develop`; never auto-merge PRs.
- 🔑 **API key** lives in `osa_project/.env` (git-ignored); needed only for live scripts.
- 💻 The VM is ephemeral — **GitHub is the durable source of truth.**
- 🔗 Private-repo access for the Abacus.AI GitHub App:
  <https://github.com/apps/abacusai/installations/select_target>.

---

## 6. Bottom line

- **Phase 5 (spec Milestone 5 — Memory & Context) is complete and on GitHub**
  (`develop`, tag `phase-5-complete`, 1025 tests green): conversation
  persistence, preference learning, semantic memory, and the privacy guard.
- Everything is **local-first** and degrades gracefully without optional ML deps.
- **The clear next body of work is the spec's Milestone 6 — Research
  Capabilities**, followed by Advanced Features (M7) and Polish (M8), with
  onboarding and the file indexer runnable in parallel.

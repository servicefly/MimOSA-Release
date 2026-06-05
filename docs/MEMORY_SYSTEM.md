# MimOSA Memory & Context (Phase 5)

This document describes how MimOSA remembers things across time:
**conversation persistence**, **preference learning**, **semantic recall**, and
the **privacy guard** that keeps sensitive topics on-device.

It complements [`INTENT_SYSTEM.md`](INTENT_SYSTEM.md) (how an utterance becomes
an action) and [`ARCHITECTURE.md`](ARCHITECTURE.md) (the LLM abstraction layer).

---

## 1. Where it fits

```
   ┌──────────────┐      ┌──────────────────────────────────────────┐
   │ IntentRouter │◀────▶│            mimosa/memory/                 │
   └──────┬───────┘      │                                          │
          │              │  ConversationStore   (M5.1, SQLite)      │
   ┌──────▼───────┐      │  PreferenceLearner    (M5.2, SQLite)     │
   │ Conversation │◀────▶│  SemanticMemory       (M5.3, embeddings) │
   │   Manager    │      │  PrivacyGuard         (M5.4, routing)    │
   └──────────────┘      └──────────────────────────────────────────┘
```

Everything here is **local-first**: SQLite files and a local vector store under
the user's data directory. No memory feature makes a network call.

**Data directory** is resolved by `mimosa/memory/paths.py`:

```
$MIMOSA_DATA  →  $XDG_DATA_HOME/mimosa  →  ~/.local/share/mimosa
```

Helpers: `conversations_db_path()`, `preferences_db_path()`,
`private_db_path()`, `semantic_store_dir()`, `default_data_dir()`,
`ensure_data_dir()`.

---

## 2. Conversation persistence (M5.1)

`ConversationStore` (`mimosa/memory/conversation_store.py`) is a thread-safe
SQLite store with two tables — `sessions` and `messages` — where every message
carries an `is_private` flag.

It plugs into `ConversationManager` by **injection**:

```python
from mimosa.memory import ConversationStore
from mimosa.core.conversation_manager import ConversationManager

store = ConversationStore(db_path)            # or ":memory:"
mgr = ConversationManager(store=store)        # persistence on
mgr.load_from_store()                         # rehydrate recent context
```

- Complete turns (user + assistant) persist immediately.
- A user-only turn is held until the reply arrives (or `flush()`), so no
  duplicate rows are written.
- Lifecycle calls (`add_turn`, `clear`, `reset_session`) auto-flush.
- With **no** `store=`, the manager behaves exactly as before (in-memory only).

Read/maintenance API: `get_messages`, `get_recent_messages`,
`get_context_messages`, `search`, `count_messages`, `purge_older_than`,
`delete_session`, `clear_all`.

Toggle: `PrivacySettings.persist_conversations` (default `True`).

---

## 3. Preference learning (M5.2)

`PreferenceLearner` (`mimosa/memory/preference_learner.py`) aggregates
observations into a `learned_preferences` table keyed by
`(category, key, value)` and reports a **calibrated confidence**:

```
confidence = dominance × (1 − exp(−count / saturation))   # saturation = 3
```

So a value that is both *dominant* and *well-evidenced* scores high; a single
observation never looks certain. Roughly **~5 consistent observations** cross
the default `0.6` threshold.

```python
pl = PreferenceLearner(db_path)
for _ in range(5):
    pl.observe("editor", "default", "kate")
pl.predict("editor", "default")        # → ("kate", confidence)
pl.explain("editor", "default")        # counts behind the prediction
pl.forget("editor", "default")         # forget one fact
```

Toggle: `PrivacySettings.learn_preferences` (default `True`).

---

## 4. Semantic memory (M5.3)

`SemanticMemory` (`mimosa/memory/semantic_memory.py`) stores text + embeddings
for similarity recall, with **three layers of graceful degradation**:

| Layer | Preferred | Fallback |
|-------|-----------|----------|
| Vector store | `chromadb` | in-process cosine store (JSONL persist) |
| Embedder | injected | `sentence-transformers` → `HashingEmbedder` |

```python
sm = SemanticMemory(store_dir)
sm.add_turn("We discussed the Q3 budget and travel costs.")
sm.recall("what did we say about spending?", k=3)   # → [SemanticResult, …]
```

> Without ML deps, `HashingEmbedder` gives **lexical** (token-overlap) recall —
> deterministic and dependency-free. Installing `sentence-transformers` upgrades
> it to true semantic similarity with **no code change**. Pass `use_chroma=False`
> to force the fallback (used by the test suite).

Optional deps live commented in `requirements.txt`. Toggle:
`PrivacySettings.semantic_memory` (default `True`).

---

## 5. Privacy guard (M5.4)

`PrivacyGuard` (`mimosa/memory/privacy_guard.py`) decides whether a prompt is
sensitive and, if so, forces it to a **local** model. Detection is tiered:

1. **Regex** — credit cards, SSNs, secret-disclosure phrasing (confidence 0.95).
2. **Keyword categories** — medical, financial, legal, credentials, personal,
   relationships (base confidence 0.7).
3. **User-learned terms** — via `PreferenceLearner` (category `"privacy"`).
4. **Optional local LLM classifier** — off by default, and *local-only*.

```python
guard = PrivacyGuard(preference_learner=pl)
assessment = guard.assess("I need to discuss my medical diagnosis")
assessment.level            # Sensitivity.PRIVATE
assessment.reasons          # human-readable "why"

provider, use_local = guard.create_provider_for(query, settings)
guard.redact("card 4111 1111 1111 1111")   # masks PII
guard.learn_private_term("projectx")        # teach a private term
```

`fail_safe_private=True` routes ambiguous cases local. Toggle:
`PrivacySettings.auto_private_mode` (default `True`).

---

## 6. Privacy summary

- All memory data is stored **locally**; no feature adds a network call.
- Sensitive prompts are routed to a local provider by construction.
- Each feature has an independent off-switch in `PrivacySettings`.
- Learned preferences and private terms are **forgettable**; PII can be redacted;
  private turns are flagged per row so they can be filtered or excluded from
  long-term recall.

---

## 7. Configuration quick reference

```python
PrivacySettings(
    persist_conversations = True,   # M5.1
    learn_preferences     = True,   # M5.2
    semantic_memory       = True,   # M5.3
    auto_private_mode     = True,   # M5.4
)
```

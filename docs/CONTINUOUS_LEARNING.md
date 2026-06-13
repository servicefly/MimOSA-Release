# Continuous Learning & Relationship (Milestone 4)

MimOSA doesn't just learn about you once during onboarding — it keeps getting to
know you over time. This document explains how continuous learning, proactive
questions, memory consolidation, relationship tracking and context-aware
suggestions work, and how you stay in control of all of it.

Everything described here is **local-only** and **opt-out**. None of it ever
gates core functionality, and learning never interrupts a conversation: if any
part fails, MimOSA simply carries on talking with you.

---

## 1. The big picture

| Capability | What it does | Module |
| --- | --- | --- |
| Continuous learning | Picks up facts/preferences from everyday chat | `mimosa/learning/continuous_learner.py` |
| Pattern detection | Notices tool, timing and style habits | `mimosa/learning/pattern_detector.py` |
| Context analysis | Reads time-of-day / recent activity for suggestions | `mimosa/learning/context_analyzer.py` |
| Proactive questions | Occasionally asks a thoughtful question | `mimosa/learning/proactive_questioner.py` |
| Memory consolidation | Merges duplicates, reconciles contradictions, tidies | `mimosa/memory/consolidator.py` |
| Relationship tracking | Tracks new → familiar → close, adjusts tone | `mimosa/memory/relationship_tracker.py` |
| Proactive suggestions | High-confidence, context-aware nudges | `mimosa/suggestions/` |

---

## 2. Continuous learning

After onboarding, every exchange you have with MimOSA is (best-effort) handed to
the **continuous learner**. It:

1. **Extracts facts** from what you said (using the same fact extractor as
   onboarding — LLM-assisted with a heuristic fallback).
2. **Folds them into your profile** via `ProfileManager.update_from_facts`, so
   newly learned details show up in future answers.
3. **Records behavioural patterns** (which tools you use, when you're active,
   whether you prefer concise or detailed replies).

It is wired into the voice loop so it runs automatically:

```
recognize speech → route to a skill → speak the reply
                                     ↘ analyze_conversation(user, reply)  # best-effort
```

If learning is turned off (see §7) or anything goes wrong, the learner is simply
skipped — the conversation is never blocked.

---

## 3. Proactive questions

Occasionally MimOSA may ask a gentle, genuinely curious question to fill a gap in
what it knows ("I don't think I ever asked — what do you usually work on?").

These are carefully rate-limited so they never feel naggy:

- **At most one or two per day**, governed by your chosen frequency.
- **Never repeats** a question it has already asked.
- **Frequency setting:** `rarely`, `balanced` (default) or `often`.
- **Fully opt-out:** turn questions off entirely and MimOSA never asks.

The questioner only proposes a question when the timing budget allows and there's
genuinely something useful to learn.

---

## 4. Memory consolidation

Over time, raw memories accumulate duplicates and the occasional contradiction
(you used to prefer X, now you prefer Y). The **consolidator** keeps memory
accurate and lean:

- **Merge duplicates** — near-identical memories are collapsed into one.
- **Reconcile contradictions** — when two memories conflict, the **newer** one
  wins and the stale one is retired.
- **Tidy clutter** — low-value, stale entries are pruned.

Two passes are available:

- **Light pass** — fast, runs opportunistically; merges obvious duplicates.
- **Deep pass** — more thorough; reconciles contradictions and prunes. You can
  trigger this any time from **Settings → Learning → Memory Management →
  Consolidate now**.

---

## 5. Relationship tracking

MimOSA keeps a simple, private sense of how well it knows you and how much
you've interacted. This maps to a **relationship stage**:

| Stage | Meaning | Tone |
| --- | --- | --- |
| **new** | Just getting started | Polite, a little more formal, explains itself |
| **familiar** | You've chatted a fair bit | Warmer, more relaxed, light shorthand |
| **close** | A well-established companion | Easy, familiar, friend-like |

The current stage is shown in **Settings → Learning**, and it gently shapes the
assistant's system prompt (via `build_system_prompt(..., relationship_note=...)`)
so MimOSA sounds appropriately warm for where your relationship is.

---

## 6. Context-aware suggestions

When MimOSA is **confident** (>70%) that a suggestion is genuinely helpful, it may
offer a context-aware nudge — for example, "You usually start coding around now —
want me to open your editor?"

Suggestions are driven by detected patterns plus the current context (time of
day, recent activity). Low-confidence ideas are never surfaced, and you can turn
suggestions off completely.

---

## 7. You're always in control

Open **Settings → Learning** to find:

**Learning Preferences**

- **Allow proactive questions** — on/off.
- **Question frequency** — `rarely` / `balanced` / `often`.
- **Proactive suggestions** — on/off.
- **Learn from conversations** — on/off (turn off to stop continuous learning
  entirely; onboarding-only behaviour).

**Memory Management**

- **View my memory** — see what MimOSA has learned (profile, patterns,
  relationship, questions asked).
- **Consolidate now** — run a deep clean-up pass on demand.

**Relationship**

- A friendly readout of your current stage (new / familiar / close).

These map to `LearningSettings` in `mimosa/utils/config.py`:

```python
LearningSettings(
    allow_questions        = True,        # ask occasional questions
    question_frequency     = "balanced",  # rarely | balanced | often
    proactive_suggestions  = True,        # context-aware nudges
    learn_from_conversations = True,      # continuous learning
)
```

---

## 8. Privacy & storage

- Everything here is stored **locally** under `~/.local/share/mimosa/` — patterns,
  the questions-asked log and relationship state alongside the existing memory
  collections. No feature adds a network call.
- All toggles default to a privacy-respecting, opt-out model and **never** gate
  core functionality.
- Existing **v1.0.0** profiles and settings load unchanged in v1.1.0: the new
  `learning` settings simply use their defaults when absent.

See [`PRIVACY.md`](PRIVACY.md) for the full privacy picture and
[`MEMORY_SYSTEM.md`](MEMORY_SYSTEM.md) for the underlying memory store.

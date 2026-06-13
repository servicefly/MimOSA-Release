# Privacy in MimOSA

MimOSA is a **privacy-first, local-by-default** voice companion. Its guiding rule
is simple: *your data is yours, and it stays on your device.* This document
summarises exactly what MimOSA stores, where, and how you stay in control.

---

## 1. Core principles

1. **Local-only by default.** Memory, profile, learned preferences, patterns,
   relationship state and logs all live on your machine. No feature in the
   learning/memory stack makes a network call.
2. **Opt-out, never opt-in-required.** Every learning and memory feature has an
   off-switch. Turning a feature off **never** disables core functionality —
   MimOSA still listens, answers and helps.
3. **Defensive by design.** If a learning or memory step fails, MimOSA degrades
   gracefully and keeps the conversation going. Learning never interrupts you.
4. **Forgettable.** You can review, consolidate and clear what MimOSA knows.

---

## 2. What is stored, and where

Everything lives under `~/.local/share/mimosa/`:

| Data | Description |
| --- | --- |
| User profile | Structured facts/preferences learned about you |
| Conversation history | Past turns (can be disabled) |
| Learned preferences | Inferred likes/habits |
| Episodic memories | Notable moments/events |
| Patterns | Tool, timing and communication-style habits (M4) |
| Questions asked | Log so MimOSA never repeats a proactive question (M4) |
| Relationship state | new / familiar / close progression (M4) |

Settings live under `~/.config/mimosa/`, logs under the standard log location
(see [`USER_GUIDE.md`](USER_GUIDE.md)).

The memory store uses **Chroma** when available, with a zero-dependency
**pure-Python/JSON fallback** — both entirely local.

---

## 3. Sensitive prompts

When privacy mode applies, sensitive prompts are routed to a **local** model
provider by construction, so private content is not sent to any remote service.
`PrivacySettings.auto_private_mode` (default **on**) governs this.

---

## 4. Continuous learning & privacy (M4)

The Milestone 4 features are all local and opt-out:

- **Learn from conversations** — turn off to stop continuous learning entirely.
- **Proactive questions** — turn off, or set frequency to `rarely`.
- **Proactive suggestions** — turn off to silence context-aware nudges.

Find these in **Settings → Learning → Learning Preferences**. See
[`CONTINUOUS_LEARNING.md`](CONTINUOUS_LEARNING.md) for details.

---

## 5. Reviewing and clearing your data

- **Review / Edit My Profile** — Settings → see and correct what MimOSA knows.
- **View my memory** — Settings → Learning → Memory Management.
- **Consolidate now** — merge duplicates and reconcile contradictions on demand.
- **Clear All Memories** — Settings → wipe stored memory.
- **Manual deletion** — delete files under `~/.local/share/mimosa/` to remove
  data at the filesystem level.

---

## 6. Configuration quick reference

```python
PrivacySettings(
    persist_conversations = True,   # store conversation history
    learn_preferences     = True,   # infer preferences
    semantic_memory       = True,   # semantic recall
    auto_private_mode     = True,   # route sensitive prompts locally
)

LearningSettings(
    allow_questions          = True,        # occasional proactive questions
    question_frequency       = "balanced",  # rarely | balanced | often
    proactive_suggestions    = True,        # context-aware nudges
    learn_from_conversations = True,        # continuous learning
)
```

---

## 7. Backward compatibility

Upgrading from **v1.0.0** to **v1.1.0** keeps your existing profile and settings
intact. New learning settings default to a privacy-respecting configuration and
are simply absent-safe — old config files load without modification.

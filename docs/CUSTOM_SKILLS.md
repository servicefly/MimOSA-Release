# Custom Skills (M4.1)

MimOSA lets you teach it new commands **without writing any Python**. A custom
skill is a small declarative specification — some trigger phrases, a matching
rule, and a response — stored locally in your config. Custom skills are matched
*before* the generic question/LLM fallback, so your own intents always win.

> **Safe by design.** Custom skills are **data, not code**. There is no `eval`,
> no `exec`, and no shell execution. A custom skill can only return a fixed text
> reply or ask your configured LLM a templated question.

---

## Anatomy of a custom skill

Each skill is a `CustomSkillSpec` with these fields:

| Field | Meaning | Default |
|-------|---------|---------|
| `id` | Stable identifier (auto-slugified from the name if omitted) | derived |
| `name` | Human-readable label | — |
| `triggers` | One or more phrases that should activate the skill | — |
| `match_mode` | How triggers are matched: `any`, `all`, `exact`, `regex` | `any` |
| `response_type` | `text` (fixed reply) or `llm` (templated prompt) | `text` |
| `response` | The reply text, or the prompt template for `llm` | — |
| `enabled` | Whether the skill is active | `true` |

### Match modes

| Mode | Fires when… |
|------|-------------|
| `any` | the utterance contains **any** trigger phrase |
| `all` | the utterance contains **all** trigger phrases |
| `exact` | the utterance **equals** a trigger (case-insensitive, trimmed) |
| `regex` | the utterance matches a trigger interpreted as a regular expression |

### Response types

- **`text`** — returns `response` verbatim. Fully offline.
- **`llm`** — sends `response` (as a prompt template) to the configured LLM
  provider. Honours `provider = none`: when no LLM is available, the skill
  degrades to a friendly notice instead of failing.

---

## Where custom skills live

Custom skills are stored in the **Skills** section of your local config
(`~/.config/mimosa/settings.json`, override with `MIMOSA_CONFIG`). They are
loaded into the live intent router when the voice loop starts.

Helpers on `SkillsSettings`:

```python
from mimosa.utils.config import AppConfigManager

mgr = AppConfigManager()
cfg = mgr.load().get()

cfg.skills.add_custom_skill({
    "name": "Standup link",
    "triggers": ["open standup", "daily standup"],
    "match_mode": "any",
    "response_type": "text",
    "response": "Here's your standup doc: https://example.internal/standup",
})
mgr.save()
```

`SkillsSettings.custom_specs()` returns validated `CustomSkillSpec` objects;
`remove_custom_skill(id)` deletes one. Invalid entries raise `CustomSkillError`
during validation, so a bad spec can never silently break routing.

---

## How matching fits into the router

The three-tier intent router gains a **Tier 1c** custom-match stage, placed
between built-in greeting patterns and the question-shape heuristic:

```
Tier 1a  exact/built-in command patterns
Tier 1b  greeting patterns
Tier 1c  >>> custom skills <<<        (new in M4.1)
Tier 2   question-shape heuristic → question/LLM skill
Tier 3   LLM classification fallback
```

A custom match returns an `IntentClassification` with intent `custom:<id>`,
confidence `0.9`, and `source="custom"`, which the router dispatches to the
corresponding `CustomSkill`.

---

## Testing

`tests/test_custom_skill.py` (48 tests) covers spec validation,
(de)serialization, slugify, all match modes, text & LLM responses, router
precedence, and config round-trips — all fully offline.

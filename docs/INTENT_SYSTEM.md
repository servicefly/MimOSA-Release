# MimOSA Intent System (M1.3)

This document describes how MimOSA turns a recognized utterance into an action:
**intent classification**, **skill routing**, **conversation context**, and the
**privacy/cost trade-offs** baked into the design.

It complements [`VOICE_PIPELINE.md`](VOICE_PIPELINE.md) (which covers wake word →
STT → TTS) and [`ARCHITECTURE.md`](ARCHITECTURE.md) (the LLM abstraction layer).

---

## 1. Where it fits

```
  ┌────────┐   ┌──────────┐   ┌──────────────┐   ┌────────────┐   ┌────────┐
  │ wake   │──▶│ STT      │──▶│ INTENT       │──▶│ skill      │──▶│ TTS    │
  │ word   │   │ (Whisper)│   │ ROUTER       │   │ (+ LLM)    │   │ (Piper)│
  └────────┘   └──────────┘   └──────────────┘   └────────────┘   └────────┘
     local        local          local +            local OR         local
                                cloud (text)        cloud (text)
```

The intent router (`mimosa/core/intent_router.py`) is the brain between speech
recognition and the spoken reply. The voice loop
(`mimosa/voice/voice_loop.py`) calls `router.route(text, context=...)` during
its `PROCESSING` step and speaks the returned text.

**Privacy boundary:** audio never leaves the machine. Local skills never touch
the network. LLM-backed skills send only the *transcribed text* — never audio.

---

## 2. Hybrid routing (why three tiers)

Sending every utterance to the cloud LLM would be slow, costly, and leak more
than necessary. So routing escalates only as far as it must:

```
  text ─▶ Tier 1: local regex heuristics ─▶ (confident?) ─▶ skill
                       │ no                              ▲
                       ▼                                 │
            Tier 1b: question-shape check ───────────────┤
                       │ no                               │
                       ▼                                  │
            Tier 2: LLM classification ──────────────────┘
                       │
                       ▼  (low confidence / unknown)
            fall back to the question skill
```

### Tier 1 — local heuristics (zero cost, instant)
Compiled regex patterns recognize the common cases on-device:

| Intent | Example triggers | Confidence |
|--------|------------------|:----------:|
| `time` / `date` | "what time is it", "today's date", "what day" | 0.97 |
| `calculator` | "what is 2 + 2", "12 times 9", "square root of…" | 0.95 |
| `weather` | "weather in Paris", "is it raining", "forecast" | 0.90 |
| `greeting` | "hello", "hi there", "good morning", "thanks" | 0.90 |

If a Tier-1 pattern matches with confidence ≥ the threshold, we route
immediately and **never call the LLM**.

### Tier 1b — question-shape heuristic
If no keyword pattern matched but the utterance *looks* like a question —
it starts with `who/what/when/where/why/how/which/is/are/can/…` or ends with
`?` — we route it to the **question skill** at confidence 0.85.

Rationale: a general question needs the LLM to *answer* anyway, so spending a
*separate* LLM call just to label it "question" is wasteful. This skips one
round-trip on the most common LLM path.

### Tier 2 — LLM classification (only when ambiguous)
Genuinely ambiguous input ("tell me something interesting", "do that again")
is sent to the LLM with a small constrained prompt that asks for one of the
supported intent labels plus a confidence score. The response is parsed
defensively (`_parse_classification`) and unknown labels default to `question`.

### Fallback
If the final intent is `unknown` or confidence is below
`INTENT_CONFIDENCE_THRESHOLD` (default `0.7`), the router routes to the question
skill so the user always gets a helpful answer rather than "I don't understand."

---

## 3. Skills

Every skill subclasses `BaseSkill` (`mimosa/skills/base_skill.py`) and returns a
`SkillResult(text, success, skill, metadata)`. The base class's `run()` wraps
`handle()` in error handling, so a skill failure becomes a graceful spoken
message — never an unhandled crash in the voice loop.

| Intent(s) | Skill | Uses LLM | Network | Notes |
|-----------|-------|:--------:|:-------:|-------|
| `time`, `date` | `TimeSkill` | ❌ | ❌ | `datetime` only |
| `calculator`, `math` | `CalculatorSkill` | ❌ | ❌ | Safe AST evaluator, **no `eval`** |
| `weather` | `WeatherSkill` | ❌ | ✅ | [wttr.in](https://wttr.in), no API key |
| `greeting`, `chitchat` | `GreetingSkill` | ✅ | ✅ | Local canned fallback if LLM down |
| `question` | `QuestionSkill` | ✅ | ✅ | Concise, voice-friendly answers |

### A note on the calculator's safety
`CalculatorSkill` does **not** use Python's `eval`. It parses the expression
with `ast.parse(..., mode="eval")` and walks the tree against an explicit
allow-list of numeric/operator nodes. Names, attribute access, function calls,
and anything else raise `CalculatorError`, which becomes a friendly message.
This prevents code injection through the microphone.

### Adding a new skill
1. Subclass `BaseSkill`; set `name`, `intents`, and `uses_llm`.
2. Implement `handle(self, text, context=None) -> SkillResult`.
3. Register it: pass it in `IntentRouter(skills=[...])`, or call
   `router.register_skill(MySkill())`. Add a heuristic pattern in
   `intent_router.py` if it should be caught locally (recommended for
   latency/privacy), otherwise it can be reached via LLM classification.

The router indexes skills by each intent label they declare, so registration is
fully modular — no central switch statement to edit.

---

## 4. Conversation context

`ConversationManager` (`mimosa/core/conversation_manager.py`) keeps a bounded,
in-memory history of `Turn`s (default `MAX_CONVERSATION_HISTORY=10`). Before
each LLM-backed skill runs, the router passes
`conversation.get_context_messages()` — a list of
`mimosa.llm.base_provider.Message` objects — so the model can resolve follow-ups
("and in London?").

* `add_turn(user_text, assistant_text, intent)` records a turn.
* `get_context_messages(max_messages=None)` returns recent turns as chat
  messages for the LLM.
* `last_intent()`, `clear()`, `reset_session()` manage state.
* `to_memory_records()` is a forward-looking seam for the Phase 3 persistent
  memory system; today it just serializes turns.

History is **in-memory only** in M1.3 — nothing is written to disk, keeping the
privacy surface minimal until the dedicated memory milestone.

---

## 5. Configuration

Set these in `.env` (see `.env.example`):

| Variable | Default | Purpose |
|----------|---------|---------|
| `INTENT_CONFIDENCE_THRESHOLD` | `0.7` | Below this, route to the question skill |
| `MAX_CONVERSATION_HISTORY` | `10` | Turns of context kept for LLM skills |
| `DEFAULT_LOCATION` | — | Fallback city for weather when none is spoken |
| `WEATHER_API_KEY` | — | Reserved; wttr.in needs no key today |

The LLM provider itself is configured via the M1.1 settings (`LLM_PROVIDER`,
`ABACUS_API_KEY`, etc.) and built through
`mimosa.llm.provider_factory.create_provider()`. The router accepts **any**
`BaseLLMProvider`, so a future local model plugs in with no router changes.

---

## 6. Trying it out

```bash
# Fixed example utterances for every intent (uses the real LLM if available):
python scripts/test_intents.py --demo

# Interactive REPL: type utterances, see classification + reply + LLM usage:
python scripts/test_intents.py

# Confirm the router + LLM round-trip is healthy:
python scripts/test_intents.py --check

# Full simulated pipeline (no microphone): router + LLM + optional TTS:
python scripts/test_full_loop.py --simulate

# Real end-to-end voice turn on a desktop with audio hardware:
python scripts/test_full_loop.py --once
```

The automated suite (`tests/test_intent_router.py`, 44 tests) runs **fully
offline** — the LLM is mocked and network calls are monkeypatched — so
`pytest` needs no API key or internet.

---

## 7. Troubleshooting

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| LLM skills reply "I can't reach my language model" | No/invalid `ABACUS_API_KEY`, or network down | Check `.env`; run `python scripts/test_intents.py --check` |
| Everything routes to `question` | Confidence threshold too high, or heuristics not matching | Lower `INTENT_CONFIDENCE_THRESHOLD`; verify phrasing; add a pattern |
| Weather says "no live data" | wttr.in unreachable or unknown location | Check connectivity; set `DEFAULT_LOCATION`; name the city explicitly |
| Calculator says it can't compute | Expression rejected by the safe evaluator | Use plain arithmetic; functions/names are intentionally blocked |
| Follow-up ("and in London?") misrouted | No keyword, so it goes to the question skill with context | Expected; the LLM answers using conversation history |
| Slow responses | Every turn hitting the LLM | Confirm local intents match Tier-1 patterns (they should be instant) |

For deeper diagnostics, run any script with `-v` for DEBUG logging, which prints
the classified intent, confidence, source (heuristic vs. LLM), and skill for
each turn.

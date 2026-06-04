# MimOSA Architecture

This document explains MimOSA's high-level architecture, with a focus on the
**LLM abstraction layer** introduced in Milestone 1.1 — the design that makes
MimOSA's local-first, privacy-first promises enforceable.

---

## 1. Big picture

MimOSA runs **entirely on the user's Linux machine**. The only thing that may
touch the network is the LLM call — and even that can be forced fully local.

```
 ┌──────────────────────────────────────────────────────────────┐
 │  MimOSA (local process)                                        │
 │                                                                │
 │   voice/      →  core/ (agent loop + state machine)            │
 │   (wake word,    │                                             │
 │    Whisper STT)  ├──→  memory/  (session, long-term,           │
 │                  │              semantic, private, file index) │
 │                  │                                             │
 │                  ├──→  skills/  (file ops, apps, system,       │
 │                  │              research)                      │
 │                  │                                             │
 │                  └──→  llm/  ── ABSTRACTION LAYER ──┐          │
 │   voice/ (Piper TTS) ←───────── response ───────────┘          │
 │                                                     │          │
 └─────────────────────────────────────────────────── │ ─────────┘
                                                       │
                          ┌────────────────────────────┴───────────┐
                          │                                         │
                  Abacus.AI RouteLLM                    Local model (future)
                   (cloud, default)                     Ollama / llama.cpp
                                                        (on-device, private)
```

Every other subsystem depends on the **LLM abstraction layer**, never on a
concrete model backend.

---

## 2. The LLM abstraction layer (`mimosa/llm`)

### 2.1 Why it exists

Two core project principles depend on funneling *all* model traffic through a
single, stable interface:

1. **Local-first / provider independence.** Swapping the default cloud model
   (Abacus.AI RouteLLM) for a fully local model (Ollama, llama.cpp) — or adding
   OpenAI/Anthropic — must be a **configuration** change, not a code change.
2. **Privacy enforcement.** Because there is exactly one path to "an LLM", a
   **Privacy Guard** can force *local-only* mode for sensitive conversations
   and be certain nothing is sent to the cloud.

### 2.2 Components

| File | Responsibility |
|------|----------------|
| `base_provider.py` | The abstract contract `BaseLLMProvider` plus shared data types (`Message`, `Role`, `ChatResponse`, `LLMError`). |
| `abacus_provider.py` | Default **cloud** provider — Abacus.AI RouteLLM over an OpenAI-compatible HTTP API. `is_local = False`. |
| `local_provider.py` | **Placeholder** for on-device inference (Ollama / llama.cpp). `is_local = True`. Wired in now; inference lands in a later milestone. |
| `provider_factory.py` | `create_provider()` — runtime selection from config/env. The single entry point the rest of the app uses. |

### 2.3 The contract

```python
class BaseLLMProvider(abc.ABC):
    name: str               # e.g. "abacus", "local"
    is_local: bool          # True => runs on-device (privacy-safe)

    def chat(self, messages, *, temperature=0.7, max_tokens=None, **kw)
        -> ChatResponse: ...

    async def stream_chat(self, messages, ...) -> AsyncIterator[str]: ...

    def health_check(self) -> bool: ...
```

- **Inputs/outputs are provider-agnostic:** a list of `Message` objects in, a
  normalized `ChatResponse` out (content, model, provider, token counts, raw).
- **Errors are unified:** providers wrap backend failures in `LLMError`.
- **`is_local`** is the flag the Privacy Guard and factory use to identify
  offline-capable, privacy-safe providers.

### 2.4 Runtime selection

`create_provider()` resolves the backend in priority order:

1. Explicit `provider="abacus"|"local"` argument.
2. `use_local=True` argument **or** the `USE_LOCAL_LLM` environment variable →
   selects the **local** provider. *(This is the Privacy Guard hook.)*
3. Otherwise the default cloud provider (`abacus`).

```python
from mimosa.llm import create_provider

llm = create_provider()                 # default → Abacus.AI RouteLLM
private_llm = create_provider(use_local=True)   # Privacy Guard → on-device
```

Callers only ever see a `BaseLLMProvider`. They never import `AbacusProvider`
or `LocalProvider` directly.

---

## 3. Adding local LLM support (future)

`LocalProvider` already defines the interface, configuration surface
(`backend` = `ollama` | `llama_cpp`, `base_url`, `model`), and `is_local=True`.
Completing it means implementing `chat()` / `health_check()`:

- **Ollama backend:** POST to a local daemon at `http://localhost:11434`
  (`/api/chat`). Easiest path; supports many open models.
- **llama.cpp backend:** bind directly to GGUF models via `llama-cpp-python`;
  no background daemon, maximal control.

Because the factory and tests already reference `LocalProvider`, finishing it
requires **zero changes** to any calling code — the rest of MimOSA keeps
calling `create_provider()` exactly as before.

### Adding other providers (OpenAI, Anthropic, …)

1. Add `mimosa/llm/<name>_provider.py` subclassing `BaseLLMProvider`.
2. Register it in `PROVIDER_REGISTRY` in `provider_factory.py`.
3. Add tests.

---

## 4. Privacy Guard interaction (design intent)

```
user message
     │
     ▼
privacy detector (keywords → patterns → LLM analysis)
     │  sensitive?
     ├── yes ──► create_provider(use_local=True)   # never touches cloud
     └── no  ──► create_provider()                 # default (cloud) path
```

The Privacy Guard chooses the provider per conversation. Since both paths
return the same `BaseLLMProvider` interface, the agent loop is identical
regardless of which backend handles the request — only the *destination* of the
data changes, and for private conversations that destination is guaranteed to
stay on-device.

---

## 5. Milestone 1.1 scope recap

M1.1 delivers the **skeleton** only:

- Full package/directory structure with documented `__init__.py` files.
- The complete LLM abstraction layer (abstract base, Abacus provider, local
  placeholder, factory).
- Configuration (`.gitignore`, `.env.example`, `requirements.txt`).
- `scripts/health_check.py` and a `pytest` setup-validation suite.

Voice, memory, skills, and UI modules are intentionally placeholders to be
filled in by later milestones.

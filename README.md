# MimOSA

**MimOSA** (Mimicking OS Assistant) — a privacy-focused, voice-controlled AI
assistant for Linux with personality and a local-first architecture.

MimOSA lives *in* your operating system: it controls apps, finds files, manages
system settings, and conducts research — all through natural conversation.
Unlike typical assistants, MimOSA keeps private conversations on your machine,
learns your preferences over time, and is designed so its language model can run
either in the cloud (Abacus.AI RouteLLM) or **fully locally** (Ollama,
llama.cpp) with no code changes.

> **Status:** **Phase 1 (Foundations) complete** — project scaffold + LLM
> abstraction (M1.1), local-first voice pipeline (M1.2), and the three-tier
> intent router with built-in skills (M1.3). **Phase 2** is underway:
> **M2.1 — File Operations** is complete (search/open/create/move/delete with a
> full safety sandbox). All **197 automated tests** pass offline.

---

## ✨ Core principles

| Principle | What it means |
|-----------|---------------|
| **Privacy first** | Sensitive data never leaves your machine; private conversations are encrypted and never sent to the cloud. |
| **Local-first** | All LLM calls flow through one abstraction layer, so a cloud model can be swapped for a local one via configuration. |
| **Token efficient** | Smart memory management minimizes API cost. |
| **Conversational UX** | Feels like talking to a friend, not issuing commands. |
| **Resource aware** | Transparent about system resource usage; degrades gracefully. |

---

## 📁 Project structure

```
osa_project/
├── mimosa/                 # Main package
│   ├── core/               # Agent loop & conversation state machine
│   ├── llm/                # LLM abstraction (Abacus + future local models)
│   ├── voice/              # Wake word, STT (Whisper), TTS (Piper)
│   ├── memory/             # Session, long-term, semantic & private memory
│   ├── skills/             # File ops, app launching, research, system control
│   ├── system/             # Distro detection, resource monitoring, app registry
│   ├── ui/                 # GTK4 interface & 2D avatar (Phase 3)
│   └── utils/              # Logging & configuration helpers
├── data/                   # Local storage, models, indices (git-ignored)
├── scripts/                # Utility scripts (e.g. health_check.py)
├── tests/                  # Automated test suite
├── config/                 # Configuration files
├── docs/                   # Architecture & design docs
├── requirements.txt        # Phase 1 dependencies
├── .env.example            # Environment variable template
└── pytest.ini              # Test configuration
```

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for a deep dive on the LLM
abstraction layer and how local-LLM support plugs in.

---

## 🚀 Setup

### Prerequisites

- **Python 3.10+** (developed and tested on 3.11)
- A Linux desktop (primary target: Kubuntu / Ubuntu 24.04+; adaptable to other
  distros)
- System library for audio capture:
  ```bash
  sudo apt install portaudio19-dev
  ```

### 1. Clone and enter the project

```bash
git clone https://github.com/servicefly/MimOSA.git
cd MimOSA
```

### 2. Create a virtual environment

```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

> `openai-whisper` pulls in PyTorch, which is a large download. The first run
> also downloads the Whisper and Piper voice models.

### 4. Configure your environment

```bash
cp .env.example .env
```

Then edit `.env` and set your values:

| Variable | Purpose |
|----------|---------|
| `ABACUS_API_KEY` | Your Abacus.AI key (required unless `USE_LOCAL_LLM=true`). |
| `USE_LOCAL_LLM` | `true` to force fully local inference (privacy mode). |
| `PORCUPINE_ACCESS_KEY` | Picovoice key for wake-word detection. |
| `LOG_LEVEL` | `DEBUG` / `INFO` / `WARNING` / `ERROR` / `CRITICAL`. |

### 5. Verify your environment

```bash
python scripts/health_check.py
```

This checks your Python version, dependency imports, Abacus.AI connectivity
(if a key is present), and prints system info.

---

## 🎙️ Voice pipeline (local-first)

MimOSA's entire voice stack runs **on-device** — spoken audio never leaves your
machine. Only (optional) LLM calls go to the cloud.

```
  IDLE ──(wake word)──▶ LISTENING ──(speech)──▶ PROCESSING ──(reply)──▶ SPEAKING ──▶ IDLE
   │                                                                                  ▲
   └──────────────────────────────────────────────────────────────────────────────-─┘
```

| Stage | Backend | Notes |
|-------|---------|-------|
| Wake word | [Porcupine](https://picovoice.ai/) | Dependency-free **energy fallback** if no key/package |
| Speech-to-text | [OpenAI Whisper](https://github.com/openai/whisper) | Fully local; model set by `WHISPER_MODEL` |
| Text-to-speech | [Piper](https://github.com/rhasspy/piper) | Fully local; voice set by `PIPER_VOICE` |

### Voice configuration

Set these in your `.env` (see `.env.example`):

| Variable | Default | Purpose |
|----------|---------|---------|
| `WAKE_WORD` | `hey mimosa` | Activation phrase |
| `PORCUPINE_ACCESS_KEY` | — | Picovoice key (optional; falls back to energy detector) |
| `WHISPER_MODEL` | `base` | `tiny`/`base`/`small`/`medium`/`large` (+ `.en`) |
| `PIPER_VOICE` | `en_US-lessac-medium` | Piper voice name or `.onnx` path |
| `AUDIO_INPUT_DEVICE` | `default` | Mic device index or `default` |
| `AUDIO_OUTPUT_DEVICE` | `default` | Speaker device index or `default` |

### Trying it out

```bash
# Report which backends are available (no recording):
python scripts/test_voice_loop.py --check

# List audio devices:
python scripts/test_voice_loop.py --list-devices

# Single turn, push-to-talk (no wake word needed):
python scripts/test_voice_loop.py --once --no-wake

# Continuous, wake-word driven:
python scripts/test_voice_loop.py
```

> **Heads-up:** On a headless VM (no microphone/speakers) the voice modules
> import fine and the tester reports backends as unavailable instead of
> crashing. Real voice I/O requires a desktop with audio hardware (e.g.
> Kubuntu) and `sudo apt install portaudio19-dev`. See
> [`docs/VOICE_PIPELINE.md`](docs/VOICE_PIPELINE.md) for the full guide.

---

## 🧠 Intent system (routing & skills)

Once your words are transcribed, MimOSA decides **what you want** and hands the
request to the right *skill*. To keep things fast, private, and cheap, routing
is **hybrid**:

```
  text ─▶ Tier 1: local regex heuristics ─▶ (confident?) ─▶ skill
                       │ no                              ▲
                       ▼                                 │
            Tier 1b: question-shape check ───────────────┤
                       │ no                               │
                       ▼                                  │
            Tier 2: LLM classification ──────────────────┘
```

* **Tier 1 (local, zero-cost):** regex heuristics catch time, date, math,
  weather, and greetings instantly — **no LLM call**.
* **Tier 1b:** question-shaped utterances (who/what/why… or ending in `?`) are
  routed straight to the question skill, which needs the LLM to *answer*
  anyway — so we skip a redundant classification call.
* **Tier 2 (LLM):** only genuinely ambiguous input is sent to the LLM for
  classification. Low-confidence results fall back to the general question
  skill.

### Skills

| Intent | Skill | LLM? | What it does |
|--------|-------|:----:|--------------|
| `time` / `date` | `TimeSkill` | ❌ local | Current time, date, day of week |
| `calculator` / `math` | `CalculatorSkill` | ❌ local | Safe arithmetic (AST allow-list, **no `eval`**) |
| `weather` | `WeatherSkill` | ❌ local | Live conditions via [wttr.in](https://wttr.in) (no API key) |
| `greeting` / `chitchat` | `GreetingSkill` | ✅ cloud | Friendly small talk (local fallback if offline) |
| `question` | `QuestionSkill` | ✅ cloud | General-knowledge Q&A, concise & voice-friendly |
| `file_ops` / `file` | `FileOperationsSkill` | ❌ local | **(M2.1)** Search, open, create, move, delete files/folders — sandboxed to your home dir |

> **Privacy:** local skills never touch the network. LLM-backed skills send
> only the **transcribed text** — never audio. Every skill degrades gracefully:
> a failure returns a spoken apology, never an unhandled crash.

### Intent configuration

Set these in your `.env` (see `.env.example`):

| Variable | Default | Purpose |
|----------|---------|---------|
| `INTENT_CONFIDENCE_THRESHOLD` | `0.7` | Below this, route to the question skill |
| `MAX_CONVERSATION_HISTORY` | `10` | Turns of context kept for LLM skills |
| `DEFAULT_LOCATION` | — | Fallback city for weather when none is said |
| `WEATHER_API_KEY` | — | Reserved (wttr.in needs none today) |

### Trying it out

```bash
# Run example utterances for every intent type (uses the real LLM if available):
python scripts/test_intents.py --demo

# Interactive: type utterances and see classification + reply:
python scripts/test_intents.py

# Check the router + LLM are reachable:
python scripts/test_intents.py --check

# Full pipeline, simulated (no microphone needed — type instead of speak):
python scripts/test_full_loop.py --simulate

# Full pipeline on a real desktop (wake word ▶ STT ▶ intent ▶ LLM ▶ TTS):
python scripts/test_full_loop.py --once
```

See [`docs/INTENT_SYSTEM.md`](docs/INTENT_SYSTEM.md) for the full design,
extension guide, and troubleshooting.

---

## 🗂️ File operations (M2.1 — system integration)

MimOSA's first **system integration** skill lets you manage files by voice while
a strict safety layer keeps the assistant inside your personal space.

| You say | MimOSA does |
|---------|-------------|
| "Find my budget spreadsheet" | Case-insensitive name/type search across your home folder |
| "Find my photos" | Filters by file-type category (images, documents, audio, video…) |
| "Open report.pdf" | Launches it with the desktop default app (`xdg-open`) |
| "Create a folder called Taxes" | Makes an empty directory |
| "Create a file called notes.txt in Projects" | Creates a file (optionally inside a folder) |
| "Move report.txt to Documents" | Moves/renames, with conflict detection |
| "Delete old-notes.txt" | **Asks to confirm**, then moves it to the **Trash** (recoverable) |
| "Permanently delete junk.txt" | **Asks to confirm**, then deletes for good |

**Safety guardrails (always on):**

- **Home-directory sandbox.** Operations are confined to `$HOME` (plus a few
  scratch/removable mounts). Anything outside is refused.
- **System blacklist.** `/etc`, `/bin`, `/sys`, `/proc`, `/boot`, `/usr`, … are
  *never* touched, even via symlinks or `..` traversal (paths are fully resolved
  first).
- **Confirmation for destructive actions.** Deletes and overwriting moves are
  two-step: MimOSA describes the action and waits for "yes"/"no".
- **Trash by default.** Deletes go to the Trash via
  [`send2trash`](https://pypi.org/project/Send2Trash/) so mistakes are
  recoverable; permanent deletion is opt-in.
- **Sensitive dotfiles** (`~/.ssh`, `~/.config`, `~/.gnupg`, …) get extra
  caution before any destructive op.

All operations are **100 % local** — nothing is sent to the cloud. The safety
logic lives in [`mimosa/system/file_safety.py`](mimosa/system/file_safety.py)
and the skill in [`mimosa/skills/file_ops.py`](mimosa/skills/file_ops.py). See
[`docs/FILE_OPERATIONS.md`](docs/FILE_OPERATIONS.md) for the full design.

---

## 🧪 Running tests

```bash
pytest
```

The setup tests validate the directory structure, config files, importable
dependencies, and the LLM provider factory. They run offline and require no API
key.

---

## 🤝 Contributing

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for the branching model, commit
conventions, and workflow.

---

## 📄 License

MIT — see [`LICENSE`](LICENSE).

# MimOSA

**MimOSA** (Mimicking OS Assistant) — a privacy-focused, voice-controlled AI
assistant for Linux with personality and a local-first architecture.

MimOSA lives *in* your operating system: it controls apps, finds files, manages
system settings, and conducts research — all through natural conversation.
Unlike typical assistants, MimOSA keeps private conversations on your machine,
learns your preferences over time, and is designed so its language model can run
either in the cloud (Abacus.AI RouteLLM) or **fully locally** (Ollama,
llama.cpp) with no code changes.

> **Status:** Milestone **M1.1 — Project Setup & Environment** complete.
> This establishes the project scaffold, the LLM abstraction layer, and the
> tooling (health check + tests) the rest of the build depends on.

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

# MimOSA

**MimOSA** (Mimicking OS Assistant) is a **privacy-focused, voice-controlled AI
assistant for Kubuntu / Ubuntu** with personality and a local-first
architecture.

MimOSA lives *in* your operating system: it launches apps, finds and manages
files, adjusts system settings, conducts research, and chats with you — all
through natural conversation. Unlike typical assistants, MimOSA keeps your
private conversations **on your own machine**, learns your preferences over
time, and lets its language model run either in the cloud or **fully locally**
(Ollama, llama.cpp) with no code changes.

> **Privacy by design:** sensitive data never leaves your computer, private
> conversations are encrypted, and there is **no telemetry** of any kind.

---

## ✨ Key features

- **🎙️ Natural voice control** — wake word, on-device speech-to-text (Whisper)
  and text-to-speech (Piper); works fully offline.
- **🧠 Local-first LLM** — one abstraction layer means you can use a cloud model
  or a local one (Ollama / llama.cpp) via configuration alone.
- **🗂️ File operations** — search, open, create, move and delete files inside a
  safety sandbox.
- **🚀 App & system control** — launch/close applications, and control volume,
  brightness, Wi-Fi and battery.
- **🖥️ Kubuntu/KDE integration** — OS & hardware awareness with KDE Plasma
  D-Bus integration.
- **🙂 Desktop avatar (optional)** — a circular, always-on-top GTK4 avatar that
  animates with the assistant's state and lip-syncs to its speech.
- **🧩 Custom skills (no code)** — teach MimOSA new commands with declarative
  triggers and text/LLM responses.
- **💬 Companion UI** — optional system-tray icon and a text-chat window that
  shares the same brain as the voice loop.
- **🔒 Encrypted memory & learning** — local, encrypted conversation store with
  preference learning and proactive context.
- **🔎 Privacy-aware research** — multi-source web research that respects your
  privacy.
- **♿ Accessible & graceful** — accessibility-labelled UI, and everything
  degrades gracefully to a headless voice loop if a component is unavailable.

---

## 📦 Installation

MimOSA targets **Kubuntu / Ubuntu 24.04+** (Python 3.10+). Pick whichever method
matches your comfort level. **New to Linux? Use the one-liner or the bootstrap
script** — they install everything for you, including system packages.

| Method | Best for | Installs system packages? |
|--------|----------|:-------------------------:|
| **① One-liner** | Absolute beginners on Ubuntu/Kubuntu | ✅ yes (automatic) |
| **② Bootstrap script** | Beginners who cloned the repo | ✅ yes (via `apt`) |
| **③ `.deb` package** | System-wide install / menu launcher | ✅ yes (via `apt`) |

See **[INSTALL.md](INSTALL.md)** for the full step-by-step guide.

### ① Quick Start — one-liner (easiest)

On a fresh **Ubuntu/Kubuntu** machine, paste this into a terminal:

```bash
curl -fsSL https://raw.githubusercontent.com/servicefly/MimOSA-Release/main/get-mimosa.sh | bash
```

This installs `git` if needed, clones MimOSA into `~/MimOSA`, then installs
**all** system and Python dependencies and sets up the `mimosa` command. To skip
the voice/UI system packages, use a core-only install:

```bash
curl -fsSL https://raw.githubusercontent.com/servicefly/MimOSA-Release/main/get-mimosa.sh | MIMOSA_BOOTSTRAP_ARGS=--core bash
```

> Prefer to read before you run? Download
> [`get-mimosa.sh`](get-mimosa.sh), inspect it, then run it.

### ② Bootstrap script (clone, then one command)

If you've already cloned the repo, `bootstrap.sh` installs every system
dependency via `apt` and then runs `install.sh` for you:

```bash
git clone https://github.com/servicefly/MimOSA-Release.git
cd MimOSA-Release
./bootstrap.sh            # installs Python, PortAudio, GTK4, etc. + MimOSA
# variants:
./bootstrap.sh --core     # skip the voice + GUI system packages
./bootstrap.sh --no-voice # skip just the PortAudio (voice) packages
./bootstrap.sh --yes      # don't prompt for confirmation
```

### ③ `.deb` package (system-wide install)

Build a Debian/Ubuntu package that installs MimOSA into `/opt/mimosa`, adds the
`mimosa` command, and creates an applications-menu launcher:

```bash
# Build (one-time tooling install with --install-build-deps):
./packaging/build-deb.sh --install-build-deps

# Install (apt resolves system dependencies automatically):
sudo apt install ./packaging/dist/mimosa-assistant_*.deb
```

Uninstall with `sudo apt remove mimosa-assistant`. Full details in
**[packaging/README.md](packaging/README.md)**.

### Prerequisites

- **Python 3.10+** (developed and tested on 3.11)
- A Linux desktop (primary target: Kubuntu / Ubuntu 24.04+)
- For the voice extra, the PortAudio system library (installed automatically by
  methods ①–③, or manually with `sudo apt install portaudio19-dev`)

---

## 🧭 Basic usage

After installing with the one-liner or bootstrap script:

```bash
cd ~/MimOSA               # or wherever you cloned it
source .venv/bin/activate
mimosa --check            # verify the environment and show the log location
mimosa                    # launch (GUI if available, otherwise headless voice loop)
```

If you installed the **`.deb` package**, just run `mimosa` from anywhere, or
launch **MimOSA Assistant** from your applications menu.

- **First launch** runs a friendly *"Get to Know MimOSA"* setup wizard where you
  tell it your name and what to call it.
- **Talk to it** using your wake word, or type in the companion chat window.
- Try things like *"open Firefox"*, *"find my notes from last week"*,
  *"turn the volume down"*, or *"what's my battery level?"*.

For everyday tips and the full command reference, see the
**[User Guide](docs/USER_GUIDE.md)**.

---

## 🆘 Troubleshooting

Running into trouble? Most common issues (audio devices, missing system
libraries, GTK/GUI, models not downloading) are covered in
**[docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md)**.

`mimosa --check` also prints exactly where your config, data, and **log files**
live, which is the first thing to look at when something goes wrong.

---

## 🐛 Reporting issues

Found a bug or have a feature request? Please open an issue on the
**[GitHub issue tracker](https://github.com/servicefly/MimOSA-Release/issues)**.
Including your OS version, the output of `mimosa --check`, and the relevant log
lines helps a lot.

---

## 📄 License

MimOSA is released under the **MIT License**. See the [LICENSE](LICENSE) file
for the full text.

Developer: Aaron Henry & VERA (His custom built AI).

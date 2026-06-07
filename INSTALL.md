# Installing MimOSA

This is the dead-simple installation guide. If anything goes wrong, see the
**[Troubleshooting guide](docs/TROUBLESHOOTING.md)**.

MimOSA is **privacy-first and local-first**: it runs entirely on your machine,
sends no telemetry, and works offline. Cloud access is optional and only used
for the language model if you choose to enable it.

---

## 0. Choose an install method

| Method | Best for | Installs system packages for you? |
|--------|----------|:---------------------------------:|
| **One-liner** (below) | Absolute beginners on Ubuntu/Kubuntu | ✅ yes |
| **Bootstrap script** (`./bootstrap.sh`) | Beginners who cloned the repo | ✅ yes |
| **`.deb` package** (`packaging/`) | System-wide install + menu launcher | ✅ yes |
| **`./install.sh`** ([§2](#2-install-one-command)) | Advanced users managing their own deps | ❌ no |

### Easiest: one-liner (fresh Ubuntu/Kubuntu)

```bash
curl -fsSL https://raw.githubusercontent.com/servicefly/MimOSA/develop/get-mimosa.sh | bash
```

This installs `git` if needed, clones MimOSA into `~/MimOSA`, and installs **all**
system + Python dependencies. Then:

```bash
cd ~/MimOSA && source .venv/bin/activate && mimosa
```

### Bootstrap script (clone, then one command)

```bash
git clone https://github.com/servicefly/MimOSA.git
cd MimOSA
./bootstrap.sh        # apt-installs Python/PortAudio/GTK4, then runs install.sh
```

Flags: `--core` (skip voice + UI packages), `--no-voice`, `--no-ui`, `--yes`
(no prompts).

### `.deb` package (system-wide)

```bash
./packaging/build-deb.sh --install-build-deps
sudo apt install ./packaging/dist/mimosa-assistant_*.deb
```

See **[packaging/README.md](packaging/README.md)** for full details. The rest of
this guide covers the manual `install.sh` method for advanced users.

---

## 1. What you need

- **Linux desktop** — primary target is Kubuntu / Ubuntu 24.04+, but any modern
  Linux distribution works.
- **Python 3.10 or newer** (3.11 recommended). Check with:
  ```bash
  python3 --version
  ```
- **git** to clone the project.

That's it for the core assistant. Two **optional** features need system
libraries (you can add them later):

| Feature | System package (Debian/Ubuntu) | Needed for |
|---------|-------------------------------|------------|
| Voice (microphone + speech) | `sudo apt install portaudio19-dev` | the on-device voice stack |
| Avatar / Companion UI | `sudo apt install libgtk-4-1 gir1.2-gtk-4.0` | the GTK4 desktop avatar |

---

## 2. Install (one command)

```bash
git clone https://github.com/servicefly/MimOSA.git
cd MimOSA
./install.sh
```

That installs the **core** assistant into a local virtual environment
(`.venv`) and gives you a `mimosa` command. To include the optional extras:

```bash
./install.sh --with-voice    # add the voice stack (needs PortAudio, above)
./install.sh --with-ui       # add the GTK4 avatar / companion UI
./install.sh --with-all      # install everything
```

Useful flags:

| Flag | Effect |
|------|--------|
| `--with-voice` | install the on-device speech-to-text / text-to-speech extra |
| `--with-ui` | install the GTK4 avatar + companion UI extra |
| `--with-all` | install all optional extras |
| `--venv PATH` | use a custom virtual-environment directory |
| `--help` | show usage |

When it finishes, the script prints exactly where your **config**, **data**,
and **log files** live (see [§5](#5-where-things-live)).

---

## 3. First run

```bash
source .venv/bin/activate     # activate the environment
mimosa --check                # sanity-check the environment + show log location
mimosa                        # launch MimOSA
```

`mimosa` starts the GTK4 desktop avatar if a graphical session and the UI extra
are available, and otherwise falls back to a fully **headless** voice/text loop.

On the very first launch, a **first-run setup wizard** walks you through a few
quick choices, including a **"Get to Know MimOSA"** step where you tell it:

- your name (so it can greet you personally),
- what you'd like to call the assistant,
- your preferred pronouns and how chatty you want it to be.

You can change all of this later in **Settings → Personalization**.

---

## 4. Configuration (optional)

MimOSA works out of the box. Configuration is only needed to enable the cloud
language model or wake-word detection. Copy the example and edit it:

```bash
cp .env.example .env
```

| Variable | Purpose |
|----------|---------|
| `ABACUS_API_KEY` | Your Abacus.AI key — required only if you want the cloud LLM. Leave unset (or set `USE_LOCAL_LLM=true`) for fully local operation. |
| `USE_LOCAL_LLM` | `true` to force fully local inference (privacy mode). |
| `PORCUPINE_ACCESS_KEY` | Picovoice key for wake-word detection (optional; an energy-based fallback works without it). |
| `LOG_LEVEL` | `DEBUG` / `INFO` / `WARNING` / `ERROR` / `CRITICAL`. |

---

## 5. Where things live

MimOSA follows the XDG base-directory convention:

| What | Location |
|------|----------|
| **Config** | `~/.config/mimosa/` |
| **Data** (conversation store, learned preferences, task queue) | `~/.local/share/mimosa/` |
| **Logs** | `~/.local/share/mimosa/logs/mimosa.log` (rotating; up to 4 files) |

Run `mimosa --check` at any time to print the resolved log location for your
machine.

---

## 6. Uninstall

```bash
./uninstall.sh            # remove the virtual environment only
./uninstall.sh --purge    # also delete your data and config (asks first)
./uninstall.sh --purge --yes   # purge without the confirmation prompt
```

Without `--purge`, your conversation history, preferences, and settings are
left untouched so you can reinstall later and pick up where you left off.

---

## 7. First-run downloads & costs

- The **voice** extra pulls in `openai-whisper`, which depends on **PyTorch** —
  a large (multi-hundred-MB) download.
- The first time you use voice, MimOSA downloads the **Whisper** speech model
  and a **Piper** voice model. These are cached locally afterwards.
- No account or API key is required for local operation. An Abacus.AI key is
  only needed if you opt into the cloud LLM.

See the **[Issues to address before installation](ISSUES_TO_ADDRESS.md)** for a
full checklist of optional prerequisites.

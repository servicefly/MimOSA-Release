# MimOSA Release Notes

## v1.0.0-rc.2 — Release Candidate 2

**Milestone 1** focuses on a fully key-free local wake word, a stronger sense of
self, and a smoother install/setup experience. Everything remains privacy-first
and local-first; no telemetry, no cloud requirement.

### What's new

- 🗣️ **openWakeWord** — Porcupine has been removed entirely. Wake-word detection
  now runs on **openWakeWord**, which is 100% local and needs **no API key**.
  The default wake phrase is still "hey mimosa". An energy-based fallback
  remains for minimal environments.
- 💾 **Reliable config saving** — `~/.config/mimosa/settings.json` is now always
  written on first launch, even if you dismiss the setup wizard, so your
  preferences persist correctly.
- 🧡 **A real identity & natural tone** — MimOSA now knows exactly who she is
  (your own local assistant, not a generic cloud model) and speaks like a warm,
  thoughtful friend rather than reciting "This is MimOSA. The weather is 72
  degrees." A single shared persona drives every LLM-backed reply.
- 🧭 **`mimosa` on your PATH** — the installer now adds a `mimosa` launcher to
  `~/.local/bin` (and updates your shell profile), so you can just type
  `mimosa` from any terminal.
- 🎚️ **Device pickers in Settings** — the microphone and speaker options are now
  dropdowns of your actual detected devices instead of free-text fields.
- 🧠 **Hardware capability detection** — at startup MimOSA silently checks
  whether your machine could train a custom wake word on-device
  (gpu / cpu / insufficient) and caches the result for a future milestone. This
  is logging-only; nothing is shown or sent anywhere.
- 🎙️ **Voice-style preference** — a new optional "voice style" choice
  (neutral / female / male) in the setup wizard and Settings, biasing how
  MimOSA presents. Defaults to neutral.

### Upgrade notes

- `pvporcupine` is no longer a dependency; `openwakeword` replaces it. Run
  `pip install -r requirements.txt` (or re-run `install.sh`) to update.
- No `PICOVOICE_ACCESS_KEY` / Porcupine key is needed anymore.

---

## v1.0.0-rc.1 — Release Candidate 1

**The first production-ready release candidate of MimOSA**, a privacy-first,
local-first, voice-controlled AI assistant for Linux. All eight development
phases are complete and the full automated suite (**1,377 tests**) passes
offline.

### Highlights

- 🎙️ **On-device voice pipeline** — wake word → Whisper STT → skills → Piper TTS,
  all local. Optional cloud LLM via Abacus.AI; fully local mode available.
- 🧠 **Memory & learning** — encrypted conversation store, preference learning,
  proactive context.
- 🔎 **Web research** — opt-in, privacy-aware multi-source research & summaries.
- ⚙️ **Background tasks** — task queue + resource monitor + on-device error-fix
  learning, all behind settings toggles.
- 🪟 **Companion UI** — optional GTK4 desktop avatar with lip-sync, system tray,
  and text-chat window; graceful headless fallback.

### New in Phase 8 (Polish & Testing)

- **Graceful error UX** — every error becomes a calm, spoken-friendly message;
  a traceback never reaches the user (it goes only to the log). Known fixes are
  suggested automatically when the on-device learner has seen the error before.
- **Logging** — a single rotating log file at
  `~/.local/share/mimosa/logs/mimosa.log` (1 MiB × 3 backups), privacy-safe, with
  `mimosa --check` to print its location and `--no-log-file` to opt out.
- **"Get to Know MimOSA"** — a first-run personalization step (your name, what to
  call the assistant, pronouns, chattiness, greet-by-name), editable later under
  **Settings → Personalization**.
- **Accessibility** — keyboard navigation, screen-reader labels, and inline help
  on every settings/wizard field; high-contrast-friendly.
- **New settings pages** — Personalization, Background Tasks, Web Research.
- **Automatic data maintenance** — retention-based purge + `VACUUM` on startup.
- **One-command install/uninstall** — `pyproject.toml` package (`mimosa`
  command), `install.sh` (with `--with-voice` / `--with-ui` / `--with-all`), and
  `uninstall.sh` (`--purge` to remove data too).
- **End-to-end tests** — hermetic, assembled-app integration coverage.

### Install

```bash
git clone https://github.com/servicefly/MimOSA.git
cd MimOSA
./install.sh            # + --with-voice / --with-ui / --with-all
source .venv/bin/activate
mimosa --check
mimosa
```

See **[INSTALL.md](INSTALL.md)** for the full guide and
**[docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md)** for help with optional
prerequisites (PortAudio, GTK4, API keys, first-run model downloads).

### Before promoting to a final 1.0.0

This is a **release candidate**. Recommended validation on a real desktop:

1. Run the GTK-gated UI tests in a graphical session (the 10 currently skipped).
2. Exercise the voice path with a real microphone + PortAudio.
3. Confirm the avatar/companion UI on a GTK4 desktop.
4. Smoke-test a clean `./install.sh` → `mimosa` → `./uninstall.sh --purge` cycle.

`main` is intentionally left untouched; promotion is a human decision after RC
validation.

### Privacy

No telemetry. All conversation data, preferences, tasks, and logs stay on your
machine. Cloud access is optional and limited to the LLM if you enable it.

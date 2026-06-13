# MimOSA Release Notes

## v1.1.0 — Continuous Learning & Polish 🌱

**Milestone 4** turns MimOSA from a one-time learner into a companion that keeps
getting to know you. After the initial onboarding, MimOSA quietly learns from
everyday conversations, occasionally asks a thoughtful question, keeps its memory
tidy, and adapts its warmth as your relationship grows — all **local-only** and
**fully opt-out**. Learning never gates core features and never interrupts a chat.

**What's new**

- **Continuous learning.** MimOSA now picks up facts and preferences from
  ordinary conversations — not just onboarding — and folds them into your local
  profile so it stays current as your life changes.
- **Proactive questions.** Now and then MimOSA may ask a gentle, genuinely
  curious "get to know you" question. It's rate-limited to one or two a day,
  never repeats itself, and can be set to *rarely*, *balanced* or *often* — or
  turned off entirely.
- **Memory consolidation.** Duplicate memories are merged, contradictions are
  reconciled (newer wins), and stale clutter is tidied — with both a light
  everyday pass and a deeper periodic clean-up — so memory stays accurate and lean.
- **Relationship tracking.** MimOSA understands how well it knows you — *new →
  familiar → close* — and adjusts how warm and familiar it sounds to match.
- **Context-aware suggestions.** High-confidence, time- and pattern-aware
  nudges ("you usually start coding around now — want me to open your editor?")
  that only surface when MimOSA is confident, and that you can switch off.
- **Smarter conversations.** Light emotion awareness and reference resolution
  mean follow-ups like "open it again" or "do that once more" just work.
- **New Settings.** A **Learning Preferences** page (allow questions, frequency,
  proactive suggestions, learn-from-conversations), a **Memory Management**
  section (view your memory, run a consolidation) and a friendly readout of your
  current relationship stage.
- **Private by design.** Everything new is on-device and opt-out. Existing
  v1.0.0 profiles and settings load unchanged.

All Milestone 1 (voice assistant + avatar), Milestone 2 (custom wake-word
training) and Milestone 3 (onboarding & memory) features are preserved.

---

## v1.0.0 — First Full Release 🎉

**Milestone 3** brings MimOSA to its first stable release with a **conversational
onboarding & memory system**. Instead of a dry form, MimOSA can sit down for a
warm, friend-like "get to know you" chat — and actually *remember* what it
learns, so every future interaction feels personal.

**What's new**

- **Conversational onboarding.** A natural, adaptive conversation across seven
  topics (introductions, work, interests, daily rhythm, how you use your
  computer, how you'd like help, and goals/people). MimOSA asks gentle
  follow-ups, notices when an answer is thin and warmly encourages you to share
  a little more — never interrogating.
- **On-device memory.** A local vector store (Chroma when available, with a
  zero-dependency pure-Python fallback) keeps four collections — your profile,
  conversation history, learned preferences and episodic memories — under
  `~/.local/share/mimosa/memory`. Nothing leaves your device.
- **Structured profile.** LLM-assisted fact extraction (with a heuristic
  offline fallback) builds a tidy profile that's injected into the assistant's
  system prompt, so answers reflect what MimOSA knows about you.
- **Pause & resume.** Step away mid-onboarding and pick up exactly where you
  left off.
- **You're in control.** New Settings actions: **Review / Edit My Profile**,
  **Redo Onboarding**, and **Clear All Memories**.
- **Graceful everywhere.** Missing Chroma → JSON fallback; missing LLM →
  heuristic extraction; onboarding is always optional and resumable.

All Milestone 1 (voice assistant + avatar) and Milestone 2 (custom wake-word
training) features are preserved.

---

## v1.0.0-rc.3 — Release Candidate 3

**Milestone 2** introduces a complete **custom wake-word training system** so
MimOSA can answer to a name of your choosing — like "Jarvis" or "Computer" —
trained **entirely on your device**. No audio is ever uploaded, and the built-in
"Hey MimOSA" always keeps working as a guaranteed fallback. Training is strictly
opt-in and happens *after* first-run setup.

### What's new

- ✨ **Custom wake-word training (on-device)** — generate synthetic speech with
  Piper TTS, augment it (background noise, reverb, far-field, negatives), train
  an openWakeWord model and export a personal `.onnx` — all locally.
- 🧭 **Redesigned setup wizard** — the per-field info-icon tooltips are gone,
  replaced by a **persistent guidance sidebar** that explains each step in plain
  sight, in MimOSA's warm, friend-like voice.
- 🔤 **"Your Own Wake Word" step** — type a name and get **instant analysis**:
  difficulty, success probability, syllable/phonetic breakdown, false-trigger
  warnings, and an estimated training time tailored to your hardware. Choose to
  **train now**, **train later**, or **keep "Hey MimOSA"**.
- 📊 **Training UI** — staged progress with percentage, time remaining, live
  epoch/loss/accuracy, plus **pause/resume** and **cancel** at any time.
- 🎙️ **"Test Your Wake Word" step** — say your new wake word and get a clear ✅/❌
  with **Try again** and **Skip**.
- 📦 **One-time dependency prompt** — before downloading the ~2.5 GB training
  stack (PyTorch, TensorFlow), MimOSA asks first:
  **Download & Train** / **Use Mimosa Instead** / **Cancel**.
- 🗣️ **Gender-aware voices** — Piper voice selection now follows your chosen
  voice style for both training data and spoken responses.
- ⚙️ **Settings additions** — **Re-run Setup Wizard** and **Train a custom wake
  word** (your other settings are preserved).
- 🛡️ **Graceful degradation everywhere** — training never crashes the assistant;
  any failure or cancellation cleanly falls back to "Hey MimOSA".

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

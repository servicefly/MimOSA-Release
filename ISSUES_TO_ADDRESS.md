# Issues to address before installation

MimOSA is **production-ready** and its full automated test suite (1,377 tests)
passes offline. The items below are **environment prerequisites and known
limitations** — not bugs in MimOSA — that you should be aware of before a real
deployment. None of them block the core, headless assistant; they enable
optional capabilities.

Legend: 🟢 optional / nice-to-have · 🟡 needed for a specific feature · 🔴 must
fix if you want that feature to work at all.

---

## 1. 🟡 Microphone & PortAudio (for voice)

The on-device voice stack needs the PortAudio system library and a working
microphone:

```bash
sudo apt install portaudio19-dev
./install.sh --with-voice
```

Without it, MimOSA still runs in **text/headless** mode. Ensure your user can
access audio devices (typically membership in the `audio` group).

## 2. 🟡 GTK4 system packages (for the avatar / companion UI)

The desktop avatar and companion windows need GTK 4 and its GObject
introspection bindings:

```bash
sudo apt install libgtk-4-1 gir1.2-gtk-4.0
./install.sh --with-ui
```

Headless machines and SSH sessions without a display automatically fall back to
the voice/CLI loop — this is expected, not an error.

## 3. 🟢 Abacus.AI API key (for the cloud LLM)

For richer answers you can set `ABACUS_API_KEY` in `.env`. **This is optional** —
with no key (or `USE_LOCAL_LLM=true`) MimOSA uses fully local fallbacks and
sends nothing to the cloud. Note that cloud LLM usage may incur API costs on
your Abacus.AI account.

## 4. 🟡 First-run model downloads (for voice)

The first time you use voice, MimOSA downloads:

- an **OpenAI Whisper** speech-to-text model (size set by `WHISPER_MODEL`), and
- a **Piper** text-to-speech voice (set by `PIPER_VOICE`).

These are one-time downloads cached locally. Budget time and bandwidth for the
first voice interaction.

## 5. 🟡 PyTorch download size (for voice)

The `voice` extra depends on `openai-whisper`, which pulls in **PyTorch** — a
large (multi-hundred-MB) install. If you don't need voice immediately, install
the core only and add `--with-voice` later.

## 6. 🟢 Wake-word key (for precise hotword)

A Picovoice **Porcupine** key (`PORCUPINE_ACCESS_KEY`) enables the precise wake
word. Without it, an **energy-based fallback** is used, which may need a
louder/closer trigger.

## 7. 🟢 Optional semantic-search dependencies

The `semantic` extra adds embedding-based search and also pulls in heavy ML
dependencies (PyTorch / sentence-transformers). Install only if you want
semantic memory search; the assistant works without it.

## 8. 🟢 Python version

MimOSA requires **Python 3.10+** (3.11 recommended). `install.sh` verifies this
and exits with a clear message if your interpreter is too old.

## 9. 🟢 Disk space & data location

Conversation history, learned preferences, the task queue, and logs live under
`~/.local/share/mimosa/`. The retention policy + automatic `VACUUM` keep this
bounded, but ensure the partition has reasonable free space, especially if you
keep history "forever" (retention = 0).

---

## Summary checklist

- [ ] Python 3.10+ available
- [ ] `portaudio19-dev` installed *(only if you want voice)*
- [ ] `libgtk-4-1 gir1.2-gtk-4.0` installed *(only if you want the avatar/UI)*
- [ ] `ABACUS_API_KEY` set *(only if you want the cloud LLM)*
- [ ] Bandwidth/time for first-run Whisper/Piper + PyTorch downloads *(voice)*
- [ ] `PORCUPINE_ACCESS_KEY` set *(only for the precise wake word)*
- [ ] Adequate free disk space under `~/.local/share/mimosa/`

If every box that matters to you is checked, run `./install.sh` (with the extras
you need) and then `mimosa --check`.

# MimOSA Voice Pipeline

This document describes MimOSA's **local-first** voice stack: how it works, how
to configure it, what hardware it needs, and how to troubleshoot it.

> **Privacy principle:** every voice stage — wake word, speech-to-text, and
> text-to-speech — runs **on-device**. Captured audio never leaves the machine.
> The only component that may contact the cloud is the (optional) LLM, which
> operates on *text*, not audio.

---

## 1. Architecture

```
        ┌──────────────────────────────────────────────────────────────┐
        │                        VoiceLoop (state machine)              │
        │                                                               │
   ┌────▼────┐   wake word   ┌───────────┐  PCM   ┌────────────┐  text  │
   │  IDLE   │──────────────▶│ LISTENING │───────▶│ PROCESSING │──────┐ │
   └────▲────┘  (Porcupine / └───────────┘ (record└────────────┘      │ │
        │        energy)      AudioManager  until   Whisper STT        │ │
        │                                  silence)  + response        │ │
        │                                            handler (echo)    │ │
        │           play WAV          ┌──────────┐    reply text        │ │
        └─────────────────────────────│ SPEAKING │◀────────────────────┘ │
                  AudioManager         └──────────┘   Piper TTS           │
        └──────────────────────────────────────────────────────────────┘
```

### Modules (`mimosa/voice/`)

| Module | Class(es) | Responsibility |
|--------|-----------|----------------|
| `audio_manager.py` | `AudioManager` | Mic capture, playback, device enumeration, silence/RMS detection, WAV save. Lazy PyAudio backend. |
| `wake_word.py` | `PorcupineWakeWord`, `EnergyWakeWord`, `create_wake_word_detector()` | Detect the activation phrase. Porcupine primary, energy-based fallback. |
| `stt.py` | `WhisperSTT`, `create_stt()` | Local speech-to-text via OpenAI Whisper. |
| `tts.py` | `PiperTTS`, `create_tts()` | Local text-to-speech via Piper. |
| `voice_loop.py` | `VoiceLoop`, `VoiceState` | The IDLE→LISTENING→PROCESSING→SPEAKING state machine that wires it all together. |

### Design guarantees

* **Imports never fail.** Optional/heavy dependencies (PyAudio, Whisper, Piper,
  Porcupine) are imported *inside methods*, never at module import time. You can
  `import mimosa.voice.*` on any machine.
* **Graceful degradation.** Missing backends raise specific, descriptive errors
  (`AudioUnavailableError`, `STTError`, `TTSError`, `WakeWordError`) only when a
  feature is actually exercised. The wake-word factory falls back to an
  energy-based detector rather than failing.
* **Per-turn resilience.** A failure in one turn (e.g. transient mic glitch) is
  logged and the loop continues.

---

## 2. State machine

| State | What happens | Transition |
|-------|--------------|------------|
| `IDLE` | Continuously listens for the wake word at low CPU. | Wake word → `LISTENING` |
| `LISTENING` | Records the utterance until trailing silence (`record_until_silence`). | Audio captured → `PROCESSING` |
| `PROCESSING` | Whisper transcribes locally; the response handler produces a reply. In M1.2 the handler simply **echoes** (no LLM yet). | Reply ready → `SPEAKING` |
| `SPEAKING` | Piper synthesizes the reply; AudioManager plays it. | Done → `IDLE` |
| `STOPPED` | Loop has been shut down and resources released. | — |

The response handler is injectable, so a real LLM-backed handler can be dropped
in later without touching the audio path.

---

## 3. Configuration

All settings live in `.env` (copy from `.env.example`):

| Variable | Default | Description |
|----------|---------|-------------|
| `WAKE_WORD` | `hey mimosa` | Activation phrase. See the Porcupine note below. |
| `PORCUPINE_ACCESS_KEY` | — | Free key from [Picovoice Console](https://console.picovoice.ai/). Optional — without it, the energy fallback is used. |
| `WHISPER_MODEL` | `base` | `tiny`, `base`, `small`, `medium`, `large` (and `.en` English-only variants). Smaller = faster/lighter. |
| `PIPER_VOICE` | `en_US-lessac-medium` | Piper voice name or path to an `.onnx` model. |
| `AUDIO_INPUT_DEVICE` | `default` | Microphone device index (from `--list-devices`) or `default`. |
| `AUDIO_OUTPUT_DEVICE` | `default` | Speaker device index or `default`. |

### About the wake word "hey mimosa"

Porcupine ships a fixed set of **built-in** keywords (e.g. `jarvis`,
`computer`, `alexa`, `picovoice`). There is **no built-in model for "hey
mimosa"**, so:

* With Porcupine but **no custom model**, MimOSA maps to a built-in keyword
  (`jarvis`) as a stand-in.
* To use the exact phrase "hey mimosa", train a custom keyword at the Picovoice
  Console, download the `.ppn` file, and pass it via `keyword_paths` to
  `PorcupineWakeWord` / `create_wake_word_detector`.
* Without Porcupine entirely, the **energy-based** detector triggers on a burst
  of sound (it cannot match a specific phrase — useful for development only).

---

## 4. Hardware & OS requirements

### Real desktop (e.g. Kubuntu) — full functionality

```bash
# System audio library (required by PyAudio):
sudo apt install portaudio19-dev

# Python dependencies (Whisper pulls in PyTorch — large download):
pip install -r requirements.txt
```

You need a working **microphone** and **speakers/headphones**. Whisper and
Piper download their models on first use and then work offline.

### Headless VM / CI — limited by design

The development VM used to build MimOSA is **headless**: it has no microphone,
no speakers, and no audio drivers. On such a machine:

* Voice modules **import successfully**.
* `AudioManager.is_available()` returns `False`.
* `scripts/test_voice_loop.py --check` reports backends as *NOT available*
  rather than crashing.
* The automated test suite (`tests/test_voice_pipeline.py`) **fully mocks**
  audio/ML backends, so all tests pass without any hardware.

This separation lets the pipeline be developed and tested in CI while remaining
fully functional on a real Linux desktop.

---

## 5. Manual testing

`scripts/test_voice_loop.py` is an interactive harness (not part of the
automated suite):

```bash
python scripts/test_voice_loop.py --check          # backend availability
python scripts/test_voice_loop.py --list-devices   # enumerate audio devices
python scripts/test_voice_loop.py --once --no-wake  # one push-to-talk turn
python scripts/test_voice_loop.py                   # continuous, wake-word driven
python scripts/test_voice_loop.py -v                # verbose DEBUG logging
```

Responses come from the M1.2 echo handler, so this validates the audio path
(capture → transcription → synthesis → playback) independent of any LLM.

---

## 6. Automated tests

`tests/test_voice_pipeline.py` covers, with everything mocked:

* `AudioManager` construction, availability, RMS (silence/loud/empty), WAV
  round-trip, and `AudioUnavailableError` on use without a backend.
* Wake word: energy fallback from the factory, energy `process()` on
  synthetic silent/loud PCM, and `WakeWordError` when constructing Porcupine
  without the package.
* `WhisperSTT`: env-driven model selection, availability, `STTError` without
  Whisper, mocked transcription, PCM→float32 resampling, and width validation.
* `PiperTTS`: env-driven voice selection, availability, `TTSError` without
  Piper, mocked WAV synthesis, and `speed`→`length_scale` inversion.
* `VoiceLoop`: full mocked turn (echo), no-speech handling, custom handler,
  STT/TTS failure resilience, stop flag, and shutdown cleanup.

```bash
pytest -q tests/test_voice_pipeline.py
```

---

## 7. Troubleshooting

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| `--check` shows Audio NOT available | PyAudio/PortAudio missing, or headless VM | `sudo apt install portaudio19-dev` then `pip install pyaudio`; on a VM this is expected |
| Whisper NOT available | `openai-whisper` not installed | `pip install openai-whisper` (large; pulls PyTorch) |
| Piper NOT available | `piper-tts` not installed | `pip install piper-tts` |
| Wake word uses `EnergyWakeWord` | No Porcupine key/package | Set `PORCUPINE_ACCESS_KEY` and `pip install pvporcupine` |
| Wake word never matches "hey mimosa" | No built-in Porcupine model for that phrase | Train a custom `.ppn` and pass via `keyword_paths`, or use a built-in keyword |
| First transcription/synthesis is slow | Model downloading on first use | Subsequent runs use the cached model and are fast |
| `STTError: Unsupported sample width` | Non-16-bit PCM fed to STT | Provide 16-bit PCM (AudioManager produces this by default) |

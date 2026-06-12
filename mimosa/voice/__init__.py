"""Voice pipeline package for MimOSA.

Handles the full local voice stack so that audio never has to leave the
machine:

* **Wake word detection** -- openWakeWord continuously monitors the microphone
  for the configured wake word, fully on-device with no API key.
* **Speech-to-Text (STT)** -- OpenAI Whisper runs locally to transcribe user
  speech to text.
* **Text-to-Speech (TTS)** -- Piper TTS synthesizes natural-sounding speech
  locally for MimOSA's responses.

Keeping STT and TTS local is a core privacy guarantee: spoken audio is never
sent to any cloud service.

Modules:

* :mod:`mimosa.voice.audio_manager` -- microphone capture / playback, device
  enumeration, and silence detection (lazy PyAudio backend).
* :mod:`mimosa.voice.wake_word` -- openWakeWord detector with a dependency-free
  energy-based fallback, plus a graceful factory.
* :mod:`mimosa.voice.stt` -- local Whisper speech-to-text.
* :mod:`mimosa.voice.tts` -- local Piper text-to-speech.
* :mod:`mimosa.voice.voice_loop` -- the IDLE -> LISTENING -> PROCESSING ->
  SPEAKING state machine that ties everything together.

All modules import successfully even when optional audio/ML dependencies are
absent (e.g. on a headless CI machine); errors are raised only when a feature
is actually exercised.
"""

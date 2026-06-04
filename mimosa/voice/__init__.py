"""Voice pipeline package for MimOSA.

Handles the full local voice stack so that audio never has to leave the
machine:

* **Wake word detection** -- Porcupine (``pvporcupine``) continuously monitors
  the microphone for the configured wake word.
* **Speech-to-Text (STT)** -- OpenAI Whisper runs locally to transcribe user
  speech to text.
* **Text-to-Speech (TTS)** -- Piper TTS synthesizes natural-sounding speech
  locally for MimOSA's responses.

Keeping STT and TTS local is a core privacy guarantee: spoken audio is never
sent to any cloud service.

Future modules expected here: ``wake_word.py``, ``voice_input.py`` (Whisper),
and ``voice_output.py`` (Piper).
"""

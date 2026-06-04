#!/usr/bin/env python3
"""Interactive manual tester for the MimOSA voice pipeline (M1.2).

This is a *hands-on* harness for a real Linux desktop (e.g. Kubuntu) with a
working microphone and speakers. It is **not** part of the automated test suite
(that lives in ``tests/test_voice_pipeline.py`` and runs fully mocked).

What it does
------------
* ``--list-devices``   : enumerate available audio input/output devices.
* ``--check``          : report which voice backends are available
                         (PyAudio, Whisper, Piper, Porcupine) without recording.
* ``--once``           : run a single turn (wake word -> listen -> STT -> echo
                         -> TTS), then exit.
* (default)            : run the continuous loop until Ctrl+C.
* ``--no-wake``        : skip wake-word detection and start recording
                         immediately (push-to-talk style) -- handy when you have
                         no Porcupine key.

Responses are produced by the M1.2 *echo* handler (no LLM yet), so this only
validates the audio path: capture, transcription, synthesis, and playback.

Examples::

    python scripts/test_voice_loop.py --check
    python scripts/test_voice_loop.py --list-devices
    python scripts/test_voice_loop.py --once --no-wake
    python scripts/test_voice_loop.py            # continuous, wake-word driven
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

# Make 'mimosa' importable when run directly from the repo root.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mimosa.voice.audio_manager import AudioManager  # noqa: E402
from mimosa.voice.stt import WhisperSTT  # noqa: E402
from mimosa.voice.tts import PiperTTS  # noqa: E402
from mimosa.voice.voice_loop import VoiceLoop  # noqa: E402
from mimosa.voice.wake_word import create_wake_word_detector  # noqa: E402


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s  %(levelname)-7s  %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def cmd_list_devices() -> int:
    """Print available audio devices."""
    mgr = AudioManager()
    if not mgr.is_available():
        print(
            "Audio backend (PyAudio/PortAudio) is NOT available on this "
            "machine.\n"
            "  - Install the system library:  sudo apt install portaudio19-dev\n"
            "  - Then the Python binding:     pip install pyaudio\n"
            "On a headless VM there is usually no audio hardware at all."
        )
        return 1

    inputs = mgr.list_input_devices()
    outputs = mgr.list_output_devices()
    print("\n=== Input (microphone) devices ===")
    for d in inputs:
        print(f"  [{d.index}] {d.name}  ({d.max_input_channels} ch)")
    print("\n=== Output (speaker) devices ===")
    for d in outputs:
        print(f"  [{d.index}] {d.name}  ({d.max_output_channels} ch)")
    if not inputs:
        print("  (no input devices found)")
    if not outputs:
        print("  (no output devices found)")
    return 0


def cmd_check() -> int:
    """Report which voice backends are available, without recording."""
    mgr = AudioManager()
    audio_ok = mgr.is_available()
    whisper_ok = WhisperSTT().is_available()
    piper_ok = PiperTTS().is_available()

    detector = create_wake_word_detector(os.getenv("WAKE_WORD", "hey mimosa"))
    backend = type(detector).__name__

    def mark(ok: bool) -> str:
        return "available" if ok else "NOT available"

    print("\n=== MimOSA voice backend check ===")
    print(f"  Audio I/O (PyAudio/PortAudio) : {mark(audio_ok)}")
    print(f"  Whisper STT (openai-whisper)  : {mark(whisper_ok)}")
    print(f"  Piper TTS (piper-tts)         : {mark(piper_ok)}")
    print(f"  Wake-word backend in use      : {backend}")
    print()
    if not (audio_ok and whisper_ok and piper_ok):
        print(
            "Some backends are missing. On a real desktop, install them with:\n"
            "  sudo apt install portaudio19-dev\n"
            "  pip install -r requirements.txt\n"
            "The pipeline will only fully run once Audio + Whisper + Piper are "
            "present."
        )
    else:
        print("All voice backends are present. You can run the loop.")
    return 0


def cmd_run(once: bool, no_wake: bool) -> int:
    """Run the voice loop (single turn or continuous)."""
    mgr = AudioManager()
    if not mgr.is_available():
        print(
            "Cannot run the voice loop: no audio backend available.\n"
            "Run with --check for details, or --list-devices to inspect "
            "hardware. This is expected on a headless VM."
        )
        return 1

    loop = VoiceLoop(audio_manager=mgr)

    if once:
        if not no_wake:
            print(f"Say the wake word ('{os.getenv('WAKE_WORD', 'hey mimosa')}') "
                  "then speak...")
        else:
            print("Speak now (recording immediately)...")
        reply = loop.run_once(wait_for_wake=not no_wake)
        print(f"\nMimOSA replied: {reply!r}")
        loop.shutdown()
        return 0

    print(
        "Starting continuous voice loop. Press Ctrl+C to stop.\n"
        f"Wake word: '{os.getenv('WAKE_WORD', 'hey mimosa')}'"
    )
    try:
        if no_wake:
            # Push-to-talk style: one turn per Enter press.
            while True:
                input("\nPress Enter to speak (Ctrl+C to quit)...")
                reply = loop.run_once(wait_for_wake=False)
                print(f"MimOSA replied: {reply!r}")
        else:
            loop.run()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        loop.shutdown()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Manual MimOSA voice pipeline tester")
    parser.add_argument("--list-devices", action="store_true", help="List audio devices and exit")
    parser.add_argument("--check", action="store_true", help="Report backend availability and exit")
    parser.add_argument("--once", action="store_true", help="Run a single turn then exit")
    parser.add_argument("--no-wake", action="store_true", help="Skip wake word; record immediately")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose (DEBUG) logging")
    args = parser.parse_args()

    _setup_logging(args.verbose)

    # Load .env if python-dotenv is available (best effort).
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except Exception:
        pass

    if args.list_devices:
        return cmd_list_devices()
    if args.check:
        return cmd_check()
    return cmd_run(once=args.once, no_wake=args.no_wake)


if __name__ == "__main__":
    raise SystemExit(main())

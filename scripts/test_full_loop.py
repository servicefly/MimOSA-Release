#!/usr/bin/env python3
"""End-to-end tester for the full MimOSA pipeline (M1.3).

This wires the *entire* assistant together exactly as it runs in production:

    wake word  ->  speech-to-text  ->  intent router  ->  skill (+LLM)  ->  TTS

It is meant for a real Linux desktop with a microphone and speakers. On a
headless VM (no audio hardware), the audio path is unavailable -- use
``--simulate`` to feed typed utterances through the *real* router + LLM + TTS
path instead, or ``--check`` to see what's available.

Privacy note
------------
Audio never leaves the machine: wake-word detection, recording, and Whisper STT
are all local. Only the recognized *text* of a question/greeting is sent to the
cloud LLM. Local intents (time/date/calculator/weather) never use the LLM.

Modes
-----
* ``--check``      : report backend availability (audio, STT, TTS, LLM) and exit.
* ``--simulate``   : skip the microphone; type utterances and run them through
                     the real IntentRouter + LLM (+ optional TTS). Great on a VM.
* ``--once``       : run exactly one real voice turn, then exit.
* ``--turns N``    : run N real voice turns (default: continuous until Ctrl+C).
* ``--no-wake``    : push-to-talk; skip wake word and record on Enter.

Examples::

    python scripts/test_full_loop.py --check
    python scripts/test_full_loop.py --simulate
    python scripts/test_full_loop.py --once
    python scripts/test_full_loop.py --no-wake --turns 3
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

# Make 'mimosa' importable when run directly from the repo root.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mimosa.core.conversation_manager import ConversationManager  # noqa: E402
from mimosa.core.intent_router import IntentRouter  # noqa: E402
from mimosa.voice.audio_manager import AudioManager  # noqa: E402
from mimosa.voice.stt import WhisperSTT  # noqa: E402
from mimosa.voice.tts import PiperTTS  # noqa: E402
from mimosa.voice.voice_loop import VoiceLoop  # noqa: E402

logger = logging.getLogger("mimosa.test_full_loop")


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s  %(levelname)-7s  %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def _build_llm():
    """Create the default LLM provider, or ``None`` if unavailable."""
    try:
        from mimosa.llm.provider_factory import create_provider

        provider = create_provider()
        logger.info("LLM provider ready: %s", type(provider).__name__)
        return provider
    except Exception as exc:  # pragma: no cover - depends on env/credentials
        logger.warning("No LLM provider available (%s).", exc)
        return None


def cmd_check() -> int:
    """Report availability of every stage in the pipeline."""
    mgr = AudioManager()
    audio_ok = mgr.is_available()
    whisper_ok = WhisperSTT().is_available()
    piper_ok = PiperTTS().is_available()
    llm = _build_llm()

    def mark(ok: bool) -> str:
        return "available" if ok else "NOT available"

    print("\n=== MimOSA full-pipeline check ===")
    print(f"  Audio I/O (PyAudio)          : {mark(audio_ok)}")
    print(f"  Whisper STT (openai-whisper) : {mark(whisper_ok)}")
    print(f"  Piper TTS (piper-tts)        : {mark(piper_ok)}")
    print(f"  LLM provider                 : {mark(llm is not None)}"
          + (f" ({type(llm).__name__})" if llm else ""))
    print()
    if not (audio_ok and whisper_ok and piper_ok):
        print("Audio path incomplete -> use --simulate to exercise the "
              "router + LLM + TTS without a microphone.\n"
              "On a real desktop, install backends with:\n"
              "  sudo apt install portaudio19-dev\n"
              "  pip install -r requirements.txt")
    else:
        print("All stages present. You can run real voice turns.")
    return 0


def cmd_simulate(voice: bool) -> int:
    """Feed typed utterances through the real router + LLM (+ optional TTS).

    This exercises everything *after* speech-to-text, which is the part that
    differs between M1.2 and M1.3, without needing a microphone.
    """
    llm = _build_llm()
    router = IntentRouter(llm_provider=llm)
    conversation = ConversationManager()

    tts = None
    if voice:
        t = PiperTTS()
        tts = t if t.is_available() else None
        if tts is None:
            print("(--voice requested but Piper TTS unavailable; text-only.)")

    print("\n=== MimOSA simulated end-to-end loop ===")
    print("Type what you would say to MimOSA. Context persists across turns.")
    print("Try a follow-up like 'and in London?' after asking the weather.")
    print("Type 'quit' / 'exit' (or Ctrl+C) to stop.\n")

    turn = 0
    try:
        while True:
            try:
                text = input("you> ").strip()
            except EOFError:
                break
            if text.lower() in {"quit", "exit", ":q"}:
                break
            if not text:
                continue

            turn += 1
            context = conversation.get_context_messages()
            result = router.route(text, context=context)
            intent = result.metadata.get("intent", "?")
            confidence = result.metadata.get("confidence", 0.0)
            source = result.metadata.get("classification_source", "?")
            conversation.add_turn(user_text=text, assistant_text=result.text,
                                  intent=intent)

            print(f"  [turn {turn}] intent={intent} "
                  f"({confidence:.2f}, via {source})")
            print(f"  MimOSA> {result.text}")
            if tts is not None:
                try:
                    tts.speak(result.text)
                except Exception as exc:  # pragma: no cover - hardware dependent
                    logger.debug("TTS playback failed: %s", exc)
    except KeyboardInterrupt:
        print("\nStopped.")

    print(f"\nConversation had {conversation.turn_count} turn(s); "
          f"last intent={conversation.last_intent()}.")
    return 0


def cmd_run(once: bool, no_wake: bool, turns: int) -> int:
    """Run real voice turns through the full pipeline."""
    mgr = AudioManager()
    if not mgr.is_available():
        print("Cannot run real voice turns: no audio backend available.\n"
              "Use --simulate to test the router/LLM/TTS path, or --check "
              "for details. This is expected on a headless VM.")
        return 1

    llm = _build_llm()
    router = IntentRouter(llm_provider=llm)
    conversation = ConversationManager()
    loop = VoiceLoop(audio_manager=mgr, intent_router=router,
                     conversation_manager=conversation)

    wake = not no_wake
    wake_word = os.getenv("WAKE_WORD", "hey mimosa")

    def _one(n: int) -> None:
        if wake:
            print(f"\n[{n}] Say the wake word ('{wake_word}'), then speak...")
        else:
            input(f"\n[{n}] Press Enter then speak...")
        reply = loop.run_once(wait_for_wake=wake)
        print(f"    MimOSA replied: {reply!r}")

    try:
        if once:
            _one(1)
        elif turns > 0:
            for n in range(1, turns + 1):
                _one(n)
        else:
            print("Continuous mode. Press Ctrl+C to stop.")
            n = 0
            while True:
                n += 1
                _one(n)
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        loop.shutdown()
    print(f"\nConversation had {conversation.turn_count} turn(s).")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="End-to-end MimOSA pipeline tester")
    parser.add_argument("--check", action="store_true",
                        help="Report backend availability and exit")
    parser.add_argument("--simulate", action="store_true",
                        help="Type utterances instead of using a microphone")
    parser.add_argument("--once", action="store_true",
                        help="Run a single real voice turn then exit")
    parser.add_argument("--turns", type=int, default=0,
                        help="Number of real voice turns (0 = continuous)")
    parser.add_argument("--no-wake", action="store_true",
                        help="Skip wake word; record on Enter (push-to-talk)")
    parser.add_argument("--voice", action="store_true",
                        help="Speak replies in --simulate mode (if TTS present)")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="Verbose (DEBUG) logging")
    args = parser.parse_args()

    _setup_logging(args.verbose)

    # Load .env if python-dotenv is available (best effort).
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except Exception:
        pass

    if args.check:
        return cmd_check()
    if args.simulate:
        return cmd_simulate(voice=args.voice)
    return cmd_run(once=args.once, no_wake=args.no_wake, turns=args.turns)


if __name__ == "__main__":
    raise SystemExit(main())

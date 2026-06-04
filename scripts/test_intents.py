#!/usr/bin/env python3
"""Interactive tester for the MimOSA intent router and skills (M1.3).

This harness exercises the *brain* of MimOSA -- intent classification and skill
routing -- **without** needing a microphone or speakers. You type an utterance,
and it shows how the router classifies it and what the matching skill replies.

It is a manual/diagnostic tool; the automated suite lives in
``tests/test_intent_router.py`` and runs fully mocked/offline.

Privacy note
------------
Local skills (time, date, calculator) are answered entirely on-device and never
touch the network. Only the *text* of questions/greetings is sent to the cloud
LLM -- never audio. This script prints, for every turn, whether the LLM was
contacted so you can verify that privacy boundary yourself.

What it does
------------
* ``--demo``   : run a fixed set of example utterances for all five intent
                 types and print classification + response for each, then exit.
* ``--check``  : report whether the LLM provider is reachable, then exit.
* ``--voice``  : after each text turn, also speak the reply via Piper TTS
                 (falls back silently to text-only if TTS is unavailable).
* (default)    : interactive REPL -- type utterances, Ctrl+C / "quit" to exit.

Examples::

    python scripts/test_intents.py --demo
    python scripts/test_intents.py --check
    python scripts/test_intents.py                 # interactive REPL
    python scripts/test_intents.py --voice         # REPL + spoken replies
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

logger = logging.getLogger("mimosa.test_intents")

# A representative example for each supported intent. Used by --demo and shown
# as hints in the interactive REPL.
DEMO_UTTERANCES = [
    ("time", "What time is it?"),
    ("date", "What's today's date?"),
    ("calculator", "What is 23 times 19 plus 4?"),
    ("weather", "What's the weather in Paris?"),
    ("greeting", "Hey there, how are you?"),
    ("question", "Who wrote Pride and Prejudice?"),
    ("ambiguous", "Tell me something interesting."),
]


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s  %(levelname)-7s  %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def _build_llm():
    """Create the default LLM provider, or ``None`` if unavailable.

    The router still works without an LLM: local intents (time/date/calculator/
    weather) are answered on-device, and LLM-backed skills degrade to a friendly
    "I can't reach my language model right now" message.
    """
    try:
        from mimosa.llm.provider_factory import create_provider

        provider = create_provider()
        logger.info("LLM provider ready: %s", type(provider).__name__)
        return provider
    except Exception as exc:  # pragma: no cover - depends on env/credentials
        logger.warning(
            "No LLM provider available (%s). Local intents still work; "
            "LLM-backed skills will return a graceful fallback.",
            exc,
        )
        return None


def _make_tts():
    """Return an initialized Piper TTS engine, or ``None`` if unavailable."""
    try:
        from mimosa.voice.tts import PiperTTS

        tts = PiperTTS()
        if tts.is_available():
            return tts
    except Exception as exc:  # pragma: no cover - optional dependency
        logger.debug("TTS unavailable: %s", exc)
    return None


def _handle_turn(router: IntentRouter, conversation: ConversationManager,
                 text: str, tts=None) -> None:
    """Classify one utterance, route it, and print the diagnostics."""
    text = text.strip()
    if not text:
        return

    classification = router.classify(text)
    context = conversation.get_context_messages()
    result = router.route(text, context=context)

    intent = result.metadata.get("intent", classification.intent)
    confidence = result.metadata.get("confidence", classification.confidence)
    source = result.metadata.get("classification_source", classification.source)

    # Determine whether the LLM was contacted: either classification used it,
    # or the skill that produced the reply is an LLM-backed skill.
    skill_obj = next((s for s in router.skills if s.name == result.skill), None)
    skill_uses_llm = bool(getattr(skill_obj, "uses_llm", False))
    used_llm = source == "llm" or (skill_uses_llm and router.llm is not None)

    conversation.add_turn(user_text=text, assistant_text=result.text, intent=intent)

    print("  ----------------------------------------------------------")
    print(f"  intent       : {intent}  (confidence {confidence:.2f}, "
          f"via {source})")
    print(f"  skill        : {result.skill}")
    print(f"  LLM used      : {'yes' if used_llm else 'no (on-device)'}")
    print(f"  success       : {result.success}")
    print(f"  reply         : {result.text}")
    print("  ----------------------------------------------------------")

    if tts is not None:
        try:
            tts.speak(result.text)
        except Exception as exc:  # pragma: no cover - hardware dependent
            logger.debug("TTS playback failed: %s", exc)


def cmd_check(router: IntentRouter) -> int:
    """Report router readiness and LLM reachability."""
    print("\n=== MimOSA intent router check ===")
    print(f"  Registered skills : {[s.name for s in router.skills]}")
    print(f"  Confidence thresh : {router.confidence_threshold}")
    if router.llm is None:
        print("  LLM provider      : NOT available "
              "(local intents work; LLM skills will fall back)")
        return 1
    print(f"  LLM provider      : {type(router.llm).__name__}")
    try:
        from mimosa.llm.base_provider import Message, Role

        resp = router.llm.chat([Message(role=Role.USER, content="Reply with: OK")],
                               max_tokens=8)
        print(f"  LLM round-trip    : OK -> {resp.content!r} (model={resp.model})")
    except Exception as exc:
        print(f"  LLM round-trip    : FAILED ({exc})")
        return 1
    print()
    return 0


def cmd_demo(router: IntentRouter) -> int:
    """Run the fixed demo utterances through the router."""
    conversation = ConversationManager()
    print("\n=== MimOSA intent demo ===")
    for label, utterance in DEMO_UTTERANCES:
        print(f"\n[{label}]  you: {utterance}")
        _handle_turn(router, conversation, utterance)
    print("\nDemo complete.")
    return 0


def cmd_repl(router: IntentRouter, voice: bool) -> int:
    """Interactive read-eval-print loop for typed utterances."""
    conversation = ConversationManager()
    tts = _make_tts() if voice else None
    if voice and tts is None:
        print("(--voice requested but Piper TTS is unavailable; "
              "continuing text-only.)")

    print("\n=== MimOSA interactive intent tester ===")
    print("Type an utterance and press Enter. Examples:")
    for _, utterance in DEMO_UTTERANCES:
        print(f"    - {utterance}")
    print("Type 'quit' or 'exit' (or Ctrl+C) to stop.\n")

    try:
        while True:
            try:
                text = input("you> ")
            except EOFError:
                break
            if text.strip().lower() in {"quit", "exit", ":q"}:
                break
            _handle_turn(router, conversation, text, tts=tts)
    except KeyboardInterrupt:
        print("\nStopped.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Manual MimOSA intent router / skill tester")
    parser.add_argument("--demo", action="store_true",
                        help="Run fixed example utterances and exit")
    parser.add_argument("--check", action="store_true",
                        help="Report router/LLM availability and exit")
    parser.add_argument("--voice", action="store_true",
                        help="Also speak replies via Piper TTS (if available)")
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

    llm = _build_llm()
    router = IntentRouter(llm_provider=llm)

    if args.check:
        return cmd_check(router)
    if args.demo:
        return cmd_demo(router)
    return cmd_repl(router, voice=args.voice)


if __name__ == "__main__":
    raise SystemExit(main())

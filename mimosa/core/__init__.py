"""Core orchestration package for MimOSA.

This package contains the central agent loop and conversation state machine
that tie together every other subsystem (voice, LLM, memory, skills). The
``core`` layer is responsible for:

* Running the main event loop that listens for the wake word, transcribes
  speech, routes intent to the LLM, executes skills, and speaks responses.
* Managing conversation state (idle, listening, thinking, speaking, paused).
* Coordinating privacy-aware context preparation before any LLM call.

Modules (M1.3):

* :class:`~mimosa.core.intent_router.IntentRouter` -- hybrid (heuristic + LLM)
  intent classification and dispatch to skills.
* :class:`~mimosa.core.conversation_manager.ConversationManager` -- bounded,
  in-memory conversation history and session metadata (the seam for Phase 3
  long-term memory).

Future modules expected here: ``agent.py`` (main agent loop) and
``state_machine.py`` (conversation states).
"""

from mimosa.core.conversation_manager import ConversationManager, Turn
from mimosa.core.intent_router import (
    IntentClassification,
    IntentRouter,
    SUPPORTED_INTENTS,
)

__all__ = [
    "IntentRouter",
    "IntentClassification",
    "SUPPORTED_INTENTS",
    "ConversationManager",
    "Turn",
]

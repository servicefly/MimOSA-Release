"""Core orchestration package for MimOSA.

This package contains the central agent loop and conversation state machine
that tie together every other subsystem (voice, LLM, memory, skills). The
``core`` layer is responsible for:

* Running the main event loop that listens for the wake word, transcribes
  speech, routes intent to the LLM, executes skills, and speaks responses.
* Managing conversation state (idle, listening, thinking, speaking, paused).
* Coordinating privacy-aware context preparation before any LLM call.

Future modules expected here: ``agent.py`` (main agent loop) and
``state_machine.py`` (conversation states).
"""

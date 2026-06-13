"""MimOSA - Mimicking OS Assistant.

A privacy-focused, voice-controlled AI assistant for Linux with personality
and a local-first architecture.

MimOSA deeply integrates with the Linux operating system to control apps, find
files, manage system settings, and conduct research -- all through natural
conversation. Core design principles:

* **Privacy First** -- sensitive data never leaves the user's machine.
* **Token Efficiency** -- minimize API calls through smart memory management.
* **Conversational UX** -- feels like talking to a friend, not issuing commands.
* **Local-First** -- the LLM abstraction layer is designed so that cloud
  providers (Abacus.AI RouteLLM) can be transparently swapped for fully local
  models (Ollama, llama.cpp) without changing any calling code.

This top-level package exposes the project version and ties together the
sub-packages: :mod:`mimosa.core`, :mod:`mimosa.llm`, :mod:`mimosa.voice`,
:mod:`mimosa.memory`, :mod:`mimosa.skills`, :mod:`mimosa.system`,
:mod:`mimosa.ui`, and :mod:`mimosa.utils`.
"""

__version__ = "1.1.0"
__author__ = "A. Henry"
__all__ = ["__version__", "__author__"]

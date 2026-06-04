"""Local LLM provider -- placeholder for fully on-device inference.

This module reserves the seam for MimOSA's **local-first** future. When
complete, :class:`LocalProvider` will run language models entirely on the
user's machine using one of:

* **Ollama** -- talk to a local ``ollama serve`` HTTP endpoint
  (``http://localhost:11434``). Easiest path; supports many open models.
* **llama.cpp** -- bind directly to GGUF models via ``llama-cpp-python`` for
  maximum control and no background daemon.

Why this matters
-----------------
A local provider is what makes MimOSA's privacy promise enforceable. Because
all LLM traffic flows through :class:`~mimosa.llm.base_provider.BaseLLMProvider`,
the Privacy Guard can force ``USE_LOCAL_LLM`` (or per-conversation local-only
mode) and be certain that sensitive content is processed on-device and never
sent to any cloud API. :attr:`is_local` is ``True`` precisely so that the
Privacy Guard and factory can identify safe, offline-capable providers.

Status: **not yet implemented.** The class is wired into the provider factory
so configuration and tests can reference it today; calling :meth:`chat` raises
:class:`NotImplementedError` with guidance until the backend lands.
"""

from __future__ import annotations

import os
from typing import Iterable, Optional

from mimosa.llm.base_provider import (
    BaseLLMProvider,
    ChatResponse,
    Message,
)

#: Default Ollama daemon endpoint assumed for the future implementation.
DEFAULT_OLLAMA_URL = "http://localhost:11434"

#: A sensible small default local model (overridable via config/env).
DEFAULT_LOCAL_MODEL = "llama3.1:8b"

#: Supported local backends planned for this provider.
SUPPORTED_BACKENDS = ("ollama", "llama_cpp")


class LocalProvider(BaseLLMProvider):
    """On-device LLM provider (Ollama / llama.cpp).

    .. note::
        This is a forward-looking placeholder. Inference is **not** wired up
        yet; :meth:`chat` raises :class:`NotImplementedError`. The interface,
        configuration surface, and :attr:`is_local` flag are finalized so that
        the rest of MimOSA -- and the test suite -- can depend on it now.

    Args:
        model: Local model identifier (e.g. ``"llama3.1:8b"`` for Ollama or a
            path to a ``.gguf`` file for llama.cpp).
        backend: Which local engine to use; one of :data:`SUPPORTED_BACKENDS`.
        base_url: Ollama daemon URL (ignored for the llama.cpp backend).
        **options: Extra options forwarded to the base class.
    """

    name = "local"
    is_local = True  # Runs entirely on-device -- safe for private mode.

    def __init__(
        self,
        model: str = DEFAULT_LOCAL_MODEL,
        *,
        backend: str = "ollama",
        base_url: Optional[str] = None,
        **options,
    ) -> None:
        super().__init__(model=model, **options)
        if backend not in SUPPORTED_BACKENDS:
            raise ValueError(
                f"Unsupported local backend {backend!r}. "
                f"Expected one of {SUPPORTED_BACKENDS}."
            )
        self.backend = backend
        self.base_url = (
            base_url or os.getenv("OLLAMA_BASE_URL") or DEFAULT_OLLAMA_URL
        ).rstrip("/")

    # -- BaseLLMProvider interface ----------------------------------------

    def chat(
        self,
        messages: Iterable[Message],
        *,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        **kwargs,
    ) -> ChatResponse:
        """Not yet implemented.

        Raises:
            NotImplementedError: Always, until the local backend is built.
                When implemented, this will POST to the Ollama ``/api/chat``
                endpoint (or invoke llama.cpp) and return a normalized
                :class:`ChatResponse`.
        """
        raise NotImplementedError(
            "LocalProvider is a placeholder for future on-device inference "
            f"(planned backends: {', '.join(SUPPORTED_BACKENDS)}). "
            "Set USE_LOCAL_LLM=false to use the Abacus.AI provider for now."
        )

    def health_check(self) -> bool:
        """Return ``False`` -- the local backend is not available yet.

        When implemented, this will ping the Ollama daemon (or confirm the
        GGUF model file exists) and return ``True`` only if local inference is
        actually ready.
        """
        return False

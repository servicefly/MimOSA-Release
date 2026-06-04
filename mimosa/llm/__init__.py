"""LLM abstraction layer for MimOSA.

This package is the single gateway through which *every* large-language-model
call in MimOSA flows. Centralizing LLM access behind a stable interface is the
foundation of two of MimOSA's core principles:

1. **Local-First / Provider Independence** -- The rest of the codebase never
   imports a concrete provider (Abacus.AI, Ollama, OpenAI, ...). It depends
   only on the abstract :class:`~mimosa.llm.base_provider.BaseLLMProvider`
   interface. Swapping the cloud Abacus.AI RouteLLM provider for a fully local
   model (Ollama, llama.cpp) is a *configuration* change, not a code change.

2. **Privacy Guard** -- Because all traffic is funneled through this layer, a
   Privacy Guard can force "local-only" mode at runtime, guaranteeing that
   sensitive content is routed exclusively to on-device models and never sent
   to the cloud.

Public API
----------
* :class:`~mimosa.llm.base_provider.BaseLLMProvider` -- the abstract contract.
* :class:`~mimosa.llm.base_provider.Message` / ``ChatResponse`` -- data types.
* :class:`~mimosa.llm.abacus_provider.AbacusProvider` -- Abacus.AI RouteLLM.
* :class:`~mimosa.llm.local_provider.LocalProvider` -- local-model placeholder.
* :func:`~mimosa.llm.provider_factory.create_provider` -- config-driven
  runtime provider selection.
"""

from mimosa.llm.base_provider import (
    BaseLLMProvider,
    ChatResponse,
    LLMError,
    Message,
    Role,
)
from mimosa.llm.provider_factory import create_provider, get_provider_class

__all__ = [
    "BaseLLMProvider",
    "ChatResponse",
    "LLMError",
    "Message",
    "Role",
    "create_provider",
    "get_provider_class",
]

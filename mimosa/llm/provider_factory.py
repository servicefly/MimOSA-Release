"""Runtime provider selection for MimOSA's LLM layer.

The factory is the one place that decides *which* concrete
:class:`~mimosa.llm.base_provider.BaseLLMProvider` MimOSA uses, based on
configuration and environment. Everything else in the codebase calls
:func:`create_provider` and receives an object that satisfies the abstract
interface -- it neither knows nor cares whether the model runs in the cloud or
on-device.

Selection logic
---------------
``create_provider`` resolves the provider in this order:

1. An explicit ``provider`` argument (``"abacus"``, ``"local"``), if given.
2. The ``USE_LOCAL_LLM`` flag (env var or argument). When truthy, the local,
   on-device provider is selected -- this is the hook the **Privacy Guard**
   uses to force local-only processing for sensitive conversations.
3. Otherwise, the default cloud provider (Abacus.AI RouteLLM).

Registering a new backend (e.g. OpenAI, Anthropic, a new local engine) is a
matter of adding one entry to :data:`PROVIDER_REGISTRY`; no calling code
changes.
"""

from __future__ import annotations

import os
from typing import Dict, Optional, Type

from mimosa.llm.abacus_provider import AbacusProvider
from mimosa.llm.anthropic_provider import AnthropicProvider
from mimosa.llm.base_provider import BaseLLMProvider
from mimosa.llm.local_provider import LocalProvider
from mimosa.llm.openai_provider import OpenAIProvider

#: Maps a provider key to its implementing class. Extend this to add backends
#: without touching call sites. ``"ollama"`` is an alias for the on-device
#: :class:`LocalProvider` (which speaks the Ollama daemon protocol).
PROVIDER_REGISTRY: Dict[str, Type[BaseLLMProvider]] = {
    "abacus": AbacusProvider,
    "openai": OpenAIProvider,
    "anthropic": AnthropicProvider,
    "ollama": LocalProvider,
    "local": LocalProvider,
}

#: Provider used when nothing else is specified.
DEFAULT_PROVIDER = "abacus"


def _env_truthy(value: Optional[str]) -> bool:
    """Interpret common truthy string values from env/config."""
    if value is None:
        return False
    return value.strip().lower() in {"1", "true", "yes", "on"}


def get_provider_class(provider_key: str) -> Type[BaseLLMProvider]:
    """Return the provider class registered under ``provider_key``.

    Args:
        provider_key: A key present in :data:`PROVIDER_REGISTRY`.

    Raises:
        ValueError: If the key is not registered.
    """
    try:
        return PROVIDER_REGISTRY[provider_key]
    except KeyError:
        raise ValueError(
            f"Unknown LLM provider {provider_key!r}. "
            f"Available: {sorted(PROVIDER_REGISTRY)}."
        ) from None


def resolve_provider_key(
    provider: Optional[str] = None,
    use_local: Optional[bool] = None,
) -> str:
    """Decide which provider key to use from explicit args and environment.

    Args:
        provider: Explicit provider key; wins if given.
        use_local: Explicit local-only override. If ``None``, the
            ``USE_LOCAL_LLM`` environment variable is consulted.

    Returns:
        A provider key guaranteed to exist in :data:`PROVIDER_REGISTRY`.
    """
    if provider:
        # Validate eagerly so misconfiguration fails fast.
        get_provider_class(provider)
        return provider

    if use_local is None:
        use_local = _env_truthy(os.getenv("USE_LOCAL_LLM"))

    if use_local:
        return "local"

    return DEFAULT_PROVIDER


def create_provider(
    provider: Optional[str] = None,
    *,
    use_local: Optional[bool] = None,
    model: Optional[str] = None,
    **options,
) -> BaseLLMProvider:
    """Instantiate and return the appropriate LLM provider.

    This is the public entry point used throughout MimOSA. Callers depend only
    on the returned :class:`~mimosa.llm.base_provider.BaseLLMProvider`
    interface, which keeps the rest of the codebase provider-agnostic.

    Args:
        provider: Explicit provider key (``"abacus"`` / ``"local"``). If
            omitted, selection falls back to ``use_local`` / env / default.
        use_local: Force the local, on-device provider when ``True``. Used by
            the Privacy Guard to enforce local-only mode. If ``None``, the
            ``USE_LOCAL_LLM`` env var decides.
        model: Optional model identifier override passed to the provider.
        **options: Extra provider-specific options (api_key, base_url, ...).

    Returns:
        A ready-to-use provider instance.

    Example:
        >>> provider = create_provider()                  # default: Abacus
        >>> private = create_provider(use_local=True)     # privacy guard path
    """
    key = resolve_provider_key(provider=provider, use_local=use_local)
    provider_cls = get_provider_class(key)
    if model is not None:
        options["model"] = model
    return provider_cls(**options)

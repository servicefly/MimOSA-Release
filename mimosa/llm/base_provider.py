"""Abstract base class and shared data types for all LLM providers.

Every concrete LLM backend in MimOSA -- whether a cloud API (Abacus.AI
RouteLLM, OpenAI, Anthropic) or a fully local engine (Ollama, llama.cpp) --
implements the :class:`BaseLLMProvider` interface defined here.

Why an abstraction layer?
-------------------------
The rest of MimOSA depends *only* on this module, never on a concrete
provider. This delivers two project-critical properties:

* **Local-first extensibility** -- adding support for a new local model means
  writing one new subclass of :class:`BaseLLMProvider`; no calling code
  changes. See :mod:`mimosa.llm.local_provider` for the planned local path.
* **Privacy enforcement** -- a Privacy Guard can force MimOSA into a
  "local-only" mode by selecting a local provider at runtime, guaranteeing
  sensitive content never reaches the network. Each provider advertises
  whether it keeps data on-device via :attr:`BaseLLMProvider.is_local`.

The interface is intentionally minimal and provider-agnostic: a list of
:class:`Message` objects in, a :class:`ChatResponse` out, with both a blocking
(:meth:`~BaseLLMProvider.chat`) and a streaming
(:meth:`~BaseLLMProvider.stream_chat`) entry point.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from enum import Enum
from typing import AsyncIterator, Iterable, Optional


class LLMError(RuntimeError):
    """Raised when an LLM provider fails to produce a response.

    Concrete providers should wrap transport/SDK errors in this exception so
    that calling code can handle a single, provider-agnostic error type.
    """


class Role(str, Enum):
    """The author role of a chat message (OpenAI-style convention)."""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


@dataclass
class Message:
    """A single chat message exchanged with an LLM.

    Attributes:
        role: Who authored the message (see :class:`Role`).
        content: The text content of the message.
        name: Optional author name (used by some tool/function-calling APIs).
    """

    role: Role
    content: str
    name: Optional[str] = None

    def to_dict(self) -> dict:
        """Serialize to the ``{"role": ..., "content": ...}`` dict shape that
        most chat-completion APIs expect."""
        data = {"role": self.role.value, "content": self.content}
        if self.name is not None:
            data["name"] = self.name
        return data


@dataclass
class ChatResponse:
    """A normalized response returned by every provider.

    Providers map their native response shapes onto this dataclass so callers
    get a consistent result regardless of backend.

    Attributes:
        content: The assistant's text reply.
        model: Identifier of the model that produced the reply.
        provider: Name of the provider that handled the request.
        prompt_tokens: Tokens consumed by the prompt, if reported.
        completion_tokens: Tokens generated in the reply, if reported.
        raw: The provider's raw response payload, for debugging/advanced use.
    """

    content: str
    model: str
    provider: str
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    raw: dict = field(default_factory=dict)

    @property
    def total_tokens(self) -> Optional[int]:
        """Total tokens used, when both prompt and completion counts exist."""
        if self.prompt_tokens is None or self.completion_tokens is None:
            return None
        return self.prompt_tokens + self.completion_tokens


class BaseLLMProvider(abc.ABC):
    """Abstract contract that every LLM provider must implement.

    Subclasses encapsulate all knowledge of a specific backend (authentication,
    request shaping, response parsing). Calling code interacts only with this
    interface, which is what makes providers hot-swappable via
    :func:`mimosa.llm.provider_factory.create_provider`.

    Args:
        model: The model identifier to use for requests.
        **options: Provider-specific options (api keys, base urls, sampling
            params, etc.). Stored on :attr:`options` for subclasses to use.
    """

    #: Human-readable provider name; subclasses must override.
    name: str = "base"

    #: Whether this provider runs entirely on-device. The Privacy Guard relies
    #: on this flag to enforce local-only mode for sensitive content.
    is_local: bool = False

    def __init__(self, model: str, **options) -> None:
        self.model = model
        self.options = options

    @abc.abstractmethod
    def chat(
        self,
        messages: Iterable[Message],
        *,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        **kwargs,
    ) -> ChatResponse:
        """Send a chat request and return a complete :class:`ChatResponse`.

        Args:
            messages: Ordered conversation history to send to the model.
            temperature: Sampling temperature (0.0 = deterministic).
            max_tokens: Optional cap on generated tokens.
            **kwargs: Provider-specific overrides.

        Returns:
            A normalized :class:`ChatResponse`.

        Raises:
            LLMError: If the provider fails to produce a response.
        """
        raise NotImplementedError

    async def stream_chat(
        self,
        messages: Iterable[Message],
        *,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        **kwargs,
    ) -> AsyncIterator[str]:
        """Stream a chat response token-by-token (text chunks).

        The default implementation falls back to a single blocking
        :meth:`chat` call and yields the full content once. Providers that
        support native streaming should override this for low-latency output.

        Yields:
            Successive text chunks of the assistant's reply.
        """
        response = self.chat(
            messages, temperature=temperature, max_tokens=max_tokens, **kwargs
        )
        yield response.content

    @abc.abstractmethod
    def health_check(self) -> bool:
        """Return ``True`` if the provider is reachable and correctly
        configured.

        Used by ``scripts/health_check.py`` and tests to verify connectivity
        without performing a full generation. Implementations should be cheap
        and must never raise -- return ``False`` on any failure.
        """
        raise NotImplementedError

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return (
            f"{type(self).__name__}(name={self.name!r}, model={self.model!r}, "
            f"is_local={self.is_local})"
        )

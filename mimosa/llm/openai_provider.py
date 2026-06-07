"""OpenAI chat-completions provider.

A thin, dependency-light client for OpenAI's OpenAI-compatible
``/v1/chat/completions`` endpoint. Selected when the user picks **OpenAI** in
the setup wizard's "Connect Your AI Brain" step and supplies an API key.

This provider is **not local**: requests leave the machine, so the Privacy
Guard avoids it for conversations marked private (handing back a local provider
instead).

Configuration
-------------
The API key is read, in order of precedence, from:

1. the ``api_key`` constructor option, then
2. the ``OPENAI_API_KEY`` environment variable.

The base URL defaults to ``https://api.openai.com/v1`` and can be overridden
via the ``base_url`` option or ``OPENAI_BASE_URL`` env var (handy for
OpenAI-compatible gateways).
"""

from __future__ import annotations

import os
from typing import Iterable, Optional

import requests

from mimosa.llm.base_provider import (
    BaseLLMProvider,
    ChatResponse,
    LLMError,
    Message,
    Role,
)

#: Default OpenAI chat-completions endpoint.
DEFAULT_BASE_URL = "https://api.openai.com/v1"

#: Default model alias.
DEFAULT_MODEL = "gpt-4o-mini"


class OpenAIProvider(BaseLLMProvider):
    """LLM provider backed by the OpenAI chat-completions API.

    Args:
        model: Model name to request. Defaults to ``"gpt-4o-mini"``.
        api_key: OpenAI API key. Falls back to ``OPENAI_API_KEY`` env var.
        base_url: Override the API base URL. Falls back to ``OPENAI_BASE_URL``
            env var, then :data:`DEFAULT_BASE_URL`.
        timeout: Per-request timeout in seconds.
        **options: Extra options forwarded to the base class.
    """

    name = "openai"
    is_local = False  # Cloud provider -- data leaves the device.

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        *,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout: float = 60.0,
        **options,
    ) -> None:
        super().__init__(model=model, **options)
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        self.base_url = (
            base_url or os.getenv("OPENAI_BASE_URL") or DEFAULT_BASE_URL
        ).rstrip("/")
        self.timeout = timeout

    # -- internal helpers --------------------------------------------------

    def _headers(self) -> dict:
        if not self.api_key:
            raise LLMError(
                "OPENAI_API_KEY is not set. Provide it via the api_key option "
                "or the OPENAI_API_KEY environment variable."
            )
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    # -- BaseLLMProvider interface ----------------------------------------

    def chat(
        self,
        messages: Iterable[Message],
        *,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        **kwargs,
    ) -> ChatResponse:
        """Send a chat-completion request to OpenAI and normalize the reply.

        Raises:
            LLMError: On missing credentials, network errors, non-2xx
                responses, or unparseable payloads.
        """
        payload = {
            "model": self.model,
            "messages": [m.to_dict() for m in messages],
            "temperature": temperature,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        payload.update(kwargs)

        try:
            resp = requests.post(
                f"{self.base_url}/chat/completions",
                headers=self._headers(),
                json=payload,
                timeout=self.timeout,
            )
        except requests.RequestException as exc:  # network-level failure
            raise LLMError(f"OpenAI request failed: {exc}") from exc

        if resp.status_code >= 400:
            raise LLMError(
                f"OpenAI returned HTTP {resp.status_code}: {resp.text[:500]}"
            )

        try:
            data = resp.json()
            choice = data["choices"][0]
            content = choice["message"]["content"]
        except (ValueError, KeyError, IndexError) as exc:
            raise LLMError(f"Could not parse OpenAI response: {exc}") from exc

        usage = data.get("usage", {}) or {}
        return ChatResponse(
            content=content,
            model=data.get("model", self.model),
            provider=self.name,
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
            raw=data,
        )

    def health_check(self) -> bool:
        """Return ``True`` if an API key is configured and the endpoint
        answers a tiny request. Never raises."""
        if not self.api_key:
            return False
        try:
            self.chat([Message(role=Role.USER, content="ping")], max_tokens=1)
            return True
        except Exception:
            return False

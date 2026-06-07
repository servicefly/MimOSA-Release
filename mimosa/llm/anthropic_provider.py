"""Anthropic (Claude) Messages API provider.

A thin, dependency-light client for Anthropic's ``/v1/messages`` endpoint.
Selected when the user picks **Anthropic** in the setup wizard's "Connect Your
AI Brain" step and supplies an API key.

This provider is **not local**: requests leave the machine, so the Privacy
Guard avoids it for conversations marked private (handing back a local provider
instead).

Configuration
-------------
The API key is read, in order of precedence, from:

1. the ``api_key`` constructor option, then
2. the ``ANTHROPIC_API_KEY`` environment variable.

The base URL defaults to ``https://api.anthropic.com/v1`` and can be overridden
via the ``base_url`` option or ``ANTHROPIC_BASE_URL`` env var.
"""

from __future__ import annotations

import os
from typing import Iterable, List, Optional

import requests

from mimosa.llm.base_provider import (
    BaseLLMProvider,
    ChatResponse,
    LLMError,
    Message,
    Role,
)

#: Default Anthropic Messages endpoint.
DEFAULT_BASE_URL = "https://api.anthropic.com/v1"

#: Default model alias.
DEFAULT_MODEL = "claude-3-5-sonnet-latest"

#: Anthropic API version header value.
API_VERSION = "2023-06-01"


class AnthropicProvider(BaseLLMProvider):
    """LLM provider backed by the Anthropic Claude Messages API.

    Args:
        model: Model name to request. Defaults to
            ``"claude-3-5-sonnet-latest"``.
        api_key: Anthropic API key. Falls back to ``ANTHROPIC_API_KEY`` env var.
        base_url: Override the API base URL. Falls back to
            ``ANTHROPIC_BASE_URL`` env var, then :data:`DEFAULT_BASE_URL`.
        timeout: Per-request timeout in seconds.
        **options: Extra options forwarded to the base class.
    """

    name = "anthropic"
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
        self.api_key = api_key or os.getenv("ANTHROPIC_API_KEY")
        self.base_url = (
            base_url or os.getenv("ANTHROPIC_BASE_URL") or DEFAULT_BASE_URL
        ).rstrip("/")
        self.timeout = timeout

    # -- internal helpers --------------------------------------------------

    def _headers(self) -> dict:
        if not self.api_key:
            raise LLMError(
                "ANTHROPIC_API_KEY is not set. Provide it via the api_key "
                "option or the ANTHROPIC_API_KEY environment variable."
            )
        return {
            "x-api-key": self.api_key,
            "anthropic-version": API_VERSION,
            "Content-Type": "application/json",
        }

    @staticmethod
    def _split_messages(messages: Iterable[Message]):
        """Separate a system prompt (if any) from the conversation turns.

        Anthropic takes the system prompt as a top-level ``system`` field
        rather than a message with ``role="system"``.
        """
        system_parts: List[str] = []
        turns: List[dict] = []
        for m in messages:
            role = m.role.value if hasattr(m.role, "value") else str(m.role)
            if role == "system":
                system_parts.append(m.content)
            else:
                turns.append({"role": role, "content": m.content})
        return ("\n\n".join(system_parts) or None), turns

    # -- BaseLLMProvider interface ----------------------------------------

    def chat(
        self,
        messages: Iterable[Message],
        *,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        **kwargs,
    ) -> ChatResponse:
        """Send a request to the Anthropic Messages API and normalize the reply.

        Raises:
            LLMError: On missing credentials, network errors, non-2xx
                responses, or unparseable payloads.
        """
        system, turns = self._split_messages(messages)
        payload = {
            "model": self.model,
            "messages": turns,
            "temperature": temperature,
            # Anthropic requires max_tokens; default to a sensible cap.
            "max_tokens": max_tokens if max_tokens is not None else 1024,
        }
        if system:
            payload["system"] = system
        payload.update(kwargs)

        try:
            resp = requests.post(
                f"{self.base_url}/messages",
                headers=self._headers(),
                json=payload,
                timeout=self.timeout,
            )
        except requests.RequestException as exc:  # network-level failure
            raise LLMError(f"Anthropic request failed: {exc}") from exc

        if resp.status_code >= 400:
            raise LLMError(
                f"Anthropic returned HTTP {resp.status_code}: {resp.text[:500]}"
            )

        try:
            data = resp.json()
            # Content is a list of blocks; concatenate the text blocks.
            blocks = data.get("content", [])
            content = "".join(
                b.get("text", "") for b in blocks if b.get("type") == "text"
            )
        except (ValueError, KeyError, AttributeError) as exc:
            raise LLMError(f"Could not parse Anthropic response: {exc}") from exc

        usage = data.get("usage", {}) or {}
        return ChatResponse(
            content=content,
            model=data.get("model", self.model),
            provider=self.name,
            prompt_tokens=usage.get("input_tokens"),
            completion_tokens=usage.get("output_tokens"),
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

"""Abstract base class and shared types for MimOSA skills.

A *skill* is a self-contained handler for one kind of user intent (telling the
time, doing arithmetic, answering a question, etc.). The
:class:`~mimosa.core.intent_router.IntentRouter` classifies each utterance and
dispatches it to the matching skill.

Design goals
------------
* **Uniform interface** -- every skill implements :meth:`BaseSkill.handle`,
  taking the recognized text (plus optional conversation context) and returning
  a :class:`SkillResult`. The router never needs to know skill internals.
* **Local-first / privacy aware** -- skills declare via :attr:`uses_llm`
  whether they send text to the cloud LLM. Purely local skills (time,
  calculator) never touch the network, which both protects privacy and avoids
  unnecessary latency/cost.
* **Graceful errors** -- :meth:`BaseSkill.run` wraps :meth:`handle` so any
  exception becomes a well-formed :class:`SkillResult` with a spoken-friendly
  error message, never an unhandled crash in the voice loop.

Adding a new skill is just subclassing :class:`BaseSkill`, setting
:attr:`name`/:attr:`intents`, and implementing :meth:`handle`.
"""

from __future__ import annotations

import abc
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class SkillResult:
    """The normalized outcome of handling an intent.

    Attributes:
        text: The response text to speak/show to the user.
        success: ``True`` if the skill handled the request successfully.
        skill: Name of the skill that produced this result.
        metadata: Optional structured data (e.g. computed value, raw API
            payload) useful for logging, tests, or downstream consumers.
    """

    text: str
    success: bool = True
    skill: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


class BaseSkill(abc.ABC):
    """Abstract contract every skill implements.

    Args:
        llm_provider: Optional shared LLM provider
            (:class:`~mimosa.llm.base_provider.BaseLLMProvider`). Skills that
            set :attr:`uses_llm` to ``True`` use it to generate responses;
            local skills ignore it.

    Subclasses must set:
        * :attr:`name` -- a short unique identifier.
        * :attr:`intents` -- intent label(s) this skill handles.
        * implement :meth:`handle`.
    """

    #: Short, unique skill identifier (e.g. ``"time"``).
    name: str = "base"

    #: Intent label(s) this skill handles. The router matches a classified
    #: intent against these.
    intents: List[str] = []

    #: Whether this skill sends text to the (cloud) LLM. Local skills set this
    #: to ``False`` so the system can reason about privacy/cost.
    uses_llm: bool = False

    def __init__(self, llm_provider=None) -> None:
        self.llm = llm_provider
        self.logger = logging.getLogger(f"mimosa.skills.{self.name}")

    @abc.abstractmethod
    def handle(self, text: str, context: Optional[List] = None) -> SkillResult:
        """Process the user's utterance and return a :class:`SkillResult`.

        Args:
            text: The recognized user text (already transcribed).
            context: Optional ordered conversation history
                (list of :class:`~mimosa.llm.base_provider.Message`) for skills
                that benefit from multi-turn context.

        Returns:
            A :class:`SkillResult` with the response text.

        Raises:
            Exception: Subclasses may raise freely; :meth:`run` converts any
                exception into a graceful error result.
        """
        raise NotImplementedError

    def run(self, text: str, context: Optional[List] = None) -> SkillResult:
        """Invoke :meth:`handle` with logging and uniform error handling.

        This is what the router calls. It guarantees a :class:`SkillResult` is
        always returned, even if the skill raises, so a single failing skill
        never crashes the voice loop.
        """
        self.logger.debug("Handling intent with text=%r", text)
        try:
            result = self.handle(text, context=context)
            if not result.skill:
                result.skill = self.name
            self.logger.debug("Result: success=%s text=%r", result.success, result.text)
            return result
        except Exception as exc:  # noqa: BLE001 - intentional catch-all boundary
            self.logger.exception("Skill %r failed: %s", self.name, exc)
            return SkillResult(
                text=self._error_message(),
                success=False,
                skill=self.name,
                metadata={"error": str(exc)},
            )

    def _error_message(self) -> str:
        """A user-friendly, speakable fallback message for failures.

        Subclasses may override to give more specific guidance.
        """
        return "Sorry, I ran into a problem handling that request."

    def has_pending_confirmation(self) -> bool:
        """Whether this skill is awaiting a yes/no follow-up from the user.

        The :class:`~mimosa.core.intent_router.IntentRouter` checks this *before*
        classifying a new utterance: if a skill has queued a destructive action
        (e.g. a delete awaiting confirmation), the next utterance is routed back
        to that skill so a bare "yes"/"no" resolves the prompt instead of being
        re-classified. Stateless skills return ``False`` (the default).
        """
        return False

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return f"{type(self).__name__}(name={self.name!r}, intents={self.intents})"

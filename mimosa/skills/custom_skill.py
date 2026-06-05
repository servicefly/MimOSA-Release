"""User-defined custom skills (M4.1).

Phase 3 reserved a ``custom`` slot in
:class:`~mimosa.utils.config.SkillsSettings` for "future custom skills". This
module turns that reservation into a working feature: users can teach MimOSA
their own commands -- a set of **triggers** that map to a canned response, or
to an LLM prompt template -- without writing any Python.

A custom skill is described by a :class:`CustomSkillSpec` (a plain,
JSON-serialisable record stored in the config) and realised at runtime as a
:class:`CustomSkill` (a normal :class:`~mimosa.skills.base_skill.BaseSkill`).
The :class:`~mimosa.core.intent_router.IntentRouter` consults the active custom
skills *before* falling back to the generic question/LLM path, so a matching
custom command is answered locally and instantly.

Design goals
------------
* **Privacy-first & safe by default.** A custom skill can return *text* or ask
  the *LLM* (using the user's own prompt template). It deliberately **cannot**
  execute arbitrary shell commands -- there is no code-exec response type -- so
  importing or loading a config can never run untrusted code.
* **Pure & hermetic.** No GTK / audio / network imports here; the optional LLM
  call goes through the injected provider, so everything is unit-testable on a
  headless machine.
* **Robust.** Specs validate/clamp/normalise themselves; a malformed entry is
  skipped rather than crashing the assistant.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from mimosa.llm.base_provider import LLMError, Message, Role
from mimosa.skills.base_skill import BaseSkill, SkillResult

logger = logging.getLogger("mimosa.skills.custom")

#: Allowed ways a custom skill matches an utterance against its triggers.
MATCH_MODES = ("any", "all", "exact", "regex")
DEFAULT_MATCH_MODE = "any"

#: Allowed response strategies. ``text`` is a fixed canned reply; ``llm`` runs
#: the user's prompt template through the configured LLM (with a graceful
#: fallback to the response text when no LLM is available).
RESPONSE_TYPES = ("text", "llm")
DEFAULT_RESPONSE_TYPE = "text"

#: Prefix used to namespace a custom skill's synthetic intent label so it never
#: collides with the built-in intents.
CUSTOM_INTENT_PREFIX = "custom:"

_SLUG_RE = re.compile(r"[^a-z0-9]+")


class CustomSkillError(ValueError):
    """Raised when a custom-skill spec cannot be normalised into a valid form."""


def slugify(value: str) -> str:
    """Turn an arbitrary label into a stable, lowercase ``a-z0-9_`` slug."""
    slug = _SLUG_RE.sub("_", str(value or "").strip().lower()).strip("_")
    return slug


@dataclass
class CustomSkillSpec:
    """A JSON-serialisable description of a user-defined skill.

    Attributes:
        id: Stable identifier (slug). Auto-derived from ``name`` when blank.
        name: Human-friendly display name.
        triggers: Phrases/keywords (or, in ``regex`` mode, patterns) that
            activate the skill.
        match_mode: One of :data:`MATCH_MODES` -- ``any`` (default; any trigger
            present as a substring), ``all`` (every trigger present), ``exact``
            (utterance equals a trigger), or ``regex`` (any trigger matches as a
            regular expression).
        response_type: ``text`` (canned reply) or ``llm`` (prompt template).
        response: The canned reply (used directly for ``text``; the offline
            fallback for ``llm``).
        llm_prompt: Prompt/system template for ``llm`` responses. ``{input}`` is
            substituted with the user's utterance.
        enabled: Whether the skill is active.
        priority: Lower sorts earlier; ties break on insertion order.
    """

    id: str = ""
    name: str = ""
    triggers: List[str] = field(default_factory=list)
    match_mode: str = DEFAULT_MATCH_MODE
    response_type: str = DEFAULT_RESPONSE_TYPE
    response: str = ""
    llm_prompt: str = ""
    enabled: bool = True
    priority: int = 100

    def validate(self) -> "CustomSkillSpec":
        """Normalise/clamp every field in place and return ``self``.

        Never raises; instead it coerces bad values to sane defaults so a
        hand-edited config can't brick the assistant. Use
        :func:`normalize_custom_spec` if you want validation errors surfaced.
        """
        self.name = str(self.name or "").strip()
        self.id = slugify(self.id) or slugify(self.name)

        if isinstance(self.triggers, str):
            self.triggers = [self.triggers]
        if not isinstance(self.triggers, (list, tuple)):
            self.triggers = []
        # strip, drop blanks, de-dup (preserve order)
        seen: set = set()
        cleaned: List[str] = []
        for t in self.triggers:
            s = str(t).strip()
            if s and s.lower() not in seen:
                seen.add(s.lower())
                cleaned.append(s)
        self.triggers = cleaned

        if self.match_mode not in MATCH_MODES:
            self.match_mode = DEFAULT_MATCH_MODE
        if self.response_type not in RESPONSE_TYPES:
            self.response_type = DEFAULT_RESPONSE_TYPE

        self.response = str(self.response or "")
        self.llm_prompt = str(self.llm_prompt or "")
        self.enabled = bool(self.enabled)
        try:
            self.priority = int(self.priority)
        except (TypeError, ValueError):
            self.priority = 100
        return self

    def is_usable(self) -> bool:
        """True if the spec is complete enough to build a working skill.

        Requires an id, at least one trigger, and a usable response source
        (canned text, or an LLM prompt/response for ``llm`` type).
        """
        if not self.id or not self.triggers:
            return False
        if self.response_type == "text":
            return bool(self.response.strip())
        return bool(self.llm_prompt.strip() or self.response.strip())

    @property
    def intent_label(self) -> str:
        """The synthetic intent label this skill registers under."""
        return f"{CUSTOM_INTENT_PREFIX}{self.id}"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "triggers": list(self.triggers),
            "match_mode": self.match_mode,
            "response_type": self.response_type,
            "response": self.response,
            "llm_prompt": self.llm_prompt,
            "enabled": self.enabled,
            "priority": self.priority,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CustomSkillSpec":
        if not isinstance(data, dict):
            data = {}
        known = {
            "id", "name", "triggers", "match_mode", "response_type",
            "response", "llm_prompt", "enabled", "priority",
        }
        filtered = {k: v for k, v in data.items() if k in known}
        return cls(**filtered).validate()


def normalize_custom_spec(data: Any) -> CustomSkillSpec:
    """Build a validated :class:`CustomSkillSpec`, raising on unusable input.

    Unlike :meth:`CustomSkillSpec.validate` (which silently coerces), this
    raises :class:`CustomSkillError` when the result would not be usable -- the
    right behaviour for an interactive "add skill" form.
    """
    if isinstance(data, CustomSkillSpec):
        spec = data.validate()
    else:
        spec = CustomSkillSpec.from_dict(data if isinstance(data, dict) else {})
    if not spec.name and not spec.id:
        raise CustomSkillError("a custom skill needs a name")
    if not spec.triggers:
        raise CustomSkillError("a custom skill needs at least one trigger phrase")
    if not spec.is_usable():
        raise CustomSkillError(
            "a custom skill needs a response (text) or an LLM prompt"
        )
    # Validate regex triggers compile.
    if spec.match_mode == "regex":
        for pat in spec.triggers:
            try:
                re.compile(pat)
            except re.error as exc:
                raise CustomSkillError(f"invalid regex trigger {pat!r}: {exc}") from exc
    return spec


class CustomSkill(BaseSkill):
    """A runtime skill realised from a :class:`CustomSkillSpec`."""

    uses_llm = False  # overridden per-spec in __init__

    def __init__(self, spec: CustomSkillSpec, llm_provider=None,
                 max_tokens: int = 160) -> None:
        self.spec = spec.validate()
        self.name = f"custom_{self.spec.id}"
        self.intents = [self.spec.intent_label]
        self.uses_llm = self.spec.response_type == "llm"
        self.max_tokens = max_tokens
        super().__init__(llm_provider=llm_provider)
        # Pre-compile regex triggers for speed.
        self._regexes: List[re.Pattern] = []
        if self.spec.match_mode == "regex":
            for pat in self.spec.triggers:
                try:
                    self._regexes.append(re.compile(pat, re.IGNORECASE))
                except re.error:
                    logger.warning("Skipping invalid regex trigger %r in %s",
                                   pat, self.name)

    # -- matching ----------------------------------------------------------

    def matches(self, text: str) -> bool:
        """Whether ``text`` activates this custom skill per its match mode."""
        if not self.spec.enabled or not self.spec.triggers:
            return False
        lowered = (text or "").strip().lower()
        if not lowered:
            return False
        mode = self.spec.match_mode
        if mode == "regex":
            return any(rx.search(text or "") for rx in self._regexes)
        if mode == "exact":
            return any(lowered == t.strip().lower() for t in self.spec.triggers)
        if mode == "all":
            return all(t.strip().lower() in lowered for t in self.spec.triggers)
        # default "any"
        return any(t.strip().lower() in lowered for t in self.spec.triggers)

    # -- handling ----------------------------------------------------------

    def handle(self, text: str, context: Optional[List] = None) -> SkillResult:
        if self.spec.response_type == "llm" and self.llm is not None:
            return self._handle_llm(text, context)
        # text response (also the offline fallback for llm specs)
        reply = self.spec.response or "Okay."
        return SkillResult(
            text=reply,
            skill=self.name,
            metadata={"source": "custom_text", "custom_id": self.spec.id},
        )

    def _handle_llm(self, text: str, context: Optional[List]) -> SkillResult:
        template = self.spec.llm_prompt or "{input}"
        prompt = template.replace("{input}", text or "")
        messages: List[Message] = [Message(role=Role.SYSTEM, content=prompt)]
        if context:
            messages.extend(context[-4:])
        messages.append(Message(role=Role.USER, content=text or ""))
        try:
            response = self.llm.chat(messages, temperature=0.7,
                                     max_tokens=self.max_tokens)
        except LLMError as exc:
            logger.info("Custom LLM skill %s failed, using fallback: %s",
                        self.name, exc)
            return SkillResult(
                text=self.spec.response or "Sorry, I couldn't answer that right now.",
                skill=self.name,
                metadata={"source": "fallback", "custom_id": self.spec.id,
                          "error": str(exc)},
            )
        reply = (response.content or "").strip() or (self.spec.response or "Okay.")
        return SkillResult(
            text=reply,
            skill=self.name,
            metadata={"source": "custom_llm", "custom_id": self.spec.id,
                      "model": response.model},
        )


def build_custom_skills(specs, llm_provider=None) -> List[CustomSkill]:
    """Build the active, usable :class:`CustomSkill` objects from raw specs.

    ``specs`` may be an iterable of :class:`CustomSkillSpec` or plain dicts (as
    stored in the config). Disabled, duplicate, or unusable specs are skipped;
    the result is sorted by ``priority`` (then insertion order).
    """
    built: List[CustomSkill] = []
    seen_ids: set = set()
    indexed = []
    for order, raw in enumerate(specs or []):
        spec = raw if isinstance(raw, CustomSkillSpec) else CustomSkillSpec.from_dict(raw)
        spec.validate()
        if not spec.enabled or not spec.is_usable():
            continue
        if spec.id in seen_ids:
            logger.debug("Skipping duplicate custom skill id %r", spec.id)
            continue
        seen_ids.add(spec.id)
        indexed.append((spec.priority, order, spec))
    indexed.sort(key=lambda t: (t[0], t[1]))
    for _, _, spec in indexed:
        built.append(CustomSkill(spec, llm_provider=llm_provider))
    return built

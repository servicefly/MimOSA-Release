"""Structured user profile storage & management (M6).

The conversational onboarding (see :mod:`mimosa.onboarding`) learns facts about
the user. Those facts live in two complementary places:

* the **vector store** (:class:`mimosa.memory.vector_store.MemoryVectorStore`)
  for *semantic recall* ("have we talked about my job?"), and
* this **structured profile** -- a small, human-readable JSON document that is
  easy to display in Settings, edit by hand, and inject verbatim into MimOSA's
  system prompt.

:class:`UserProfile` is the schema; :class:`ProfileManager` loads/saves it and
offers friendly mutators. Everything is **local-only** and degrades
gracefully: a missing/corrupt file simply yields an empty profile, and saves
are atomic + best-effort (never crash the app).
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from mimosa.memory.paths import profile_path

logger = logging.getLogger(__name__)

#: Profile categories that hold a *list* of values (deduplicated, order kept).
LIST_FIELDS = ("skills", "interests", "goals")
#: Profile categories that hold a *mapping* of key -> value.
DICT_FIELDS = ("tools", "preferences", "schedule", "relationships")
#: Profile categories that hold a single scalar string.
SCALAR_FIELDS = ("name", "occupation")

__all__ = ["UserProfile", "ProfileManager", "LIST_FIELDS", "DICT_FIELDS", "SCALAR_FIELDS"]


@dataclass
class UserProfile:
    """The structured facts MimOSA has learned about the user.

    Mirrors the milestone's documented shape. Every field is optional; empty
    means "not learned yet".
    """

    name: str = ""
    occupation: str = ""
    skills: List[str] = field(default_factory=list)
    tools: Dict[str, Any] = field(default_factory=dict)
    interests: List[str] = field(default_factory=list)
    preferences: Dict[str, Any] = field(default_factory=dict)
    schedule: Dict[str, Any] = field(default_factory=dict)
    relationships: Dict[str, Any] = field(default_factory=dict)
    goals: List[str] = field(default_factory=list)
    #: Unix timestamp of the last update (informational).
    updated_at: float = 0.0

    # -- (de)serialisation -------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        """Return the spec-shaped ``{"user_profile": {...}}`` document."""
        data = asdict(self)
        return {"user_profile": data}

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> "UserProfile":
        """Build a profile from a (possibly partial/legacy) dict. Never raises."""
        if not isinstance(data, dict):
            return cls()
        prof = data.get("user_profile", data)
        if not isinstance(prof, dict):
            return cls()
        known = {
            "name", "occupation", "skills", "tools", "interests",
            "preferences", "schedule", "relationships", "goals", "updated_at",
        }
        filtered = {k: v for k, v in prof.items() if k in known}
        inst = cls()
        for k, v in filtered.items():
            try:
                setattr(inst, k, v)
            except Exception:  # pragma: no cover - defensive
                pass
        inst._normalise()
        return inst

    def _normalise(self) -> "UserProfile":
        for f in SCALAR_FIELDS:
            v = getattr(self, f, "")
            setattr(self, f, str(v).strip() if v is not None else "")
        for f in LIST_FIELDS:
            v = getattr(self, f, [])
            if isinstance(v, str):
                v = [v]
            if not isinstance(v, (list, tuple)):
                v = []
            seen, clean = set(), []
            for item in v:
                s = str(item).strip()
                if s and s.lower() not in seen:
                    seen.add(s.lower())
                    clean.append(s)
            setattr(self, f, clean)
        for f in DICT_FIELDS:
            v = getattr(self, f, {})
            if not isinstance(v, dict):
                v = {}
            setattr(self, f, {str(k): v[k] for k in v})
        try:
            self.updated_at = float(self.updated_at or 0.0)
        except (TypeError, ValueError):
            self.updated_at = 0.0
        return self

    # -- queries -----------------------------------------------------------

    def is_empty(self) -> bool:
        """Whether nothing has been learned yet."""
        return not any([
            self.name, self.occupation, self.skills, self.tools,
            self.interests, self.preferences, self.schedule,
            self.relationships, self.goals,
        ])

    def known_fact_count(self) -> int:
        """How many individual facts are stored (for "Topic 3 of 7"-style UI)."""
        count = 0
        for f in SCALAR_FIELDS:
            if getattr(self, f):
                count += 1
        for f in LIST_FIELDS:
            count += len(getattr(self, f))
        for f in DICT_FIELDS:
            count += len(getattr(self, f))
        return count

    def to_prompt_summary(self) -> str:
        """Compact, natural-language summary for the LLM system prompt."""
        bits: List[str] = []

        def _scalar(label: str, value: str) -> None:
            if value:
                bits.append(f"{label}: {value}")

        def _list(label: str, value: List[str]) -> None:
            if value:
                bits.append(f"{label}: {', '.join(value)}")

        def _dict(label: str, value: Dict[str, Any]) -> None:
            pairs = [f"{k} {v}" for k, v in value.items() if v]
            if pairs:
                bits.append(f"{label}: {', '.join(pairs)}")

        _scalar("Name", self.name)
        _scalar("Occupation", self.occupation)
        _list("Skills", self.skills)
        _dict("Tools", self.tools)
        _list("Interests", self.interests)
        _dict("Preferences", self.preferences)
        _dict("Schedule", self.schedule)
        _dict("Relationships", self.relationships)
        _list("Goals", self.goals)
        return ". ".join(bits)

    def display_items(self) -> List[tuple]:
        """Return ``(label, value_text)`` rows for a profile-viewer UI."""
        rows: List[tuple] = []
        labels = {
            "name": "Name", "occupation": "Occupation", "skills": "Skills",
            "tools": "Tools", "interests": "Interests",
            "preferences": "Preferences", "schedule": "Schedule",
            "relationships": "Relationships", "goals": "Goals",
        }
        for f, label in labels.items():
            v = getattr(self, f)
            if not v:
                continue
            if isinstance(v, (list, tuple)):
                rows.append((label, ", ".join(map(str, v))))
            elif isinstance(v, dict):
                rows.append((label, ", ".join(f"{k}: {val}" for k, val in v.items())))
            else:
                rows.append((label, str(v)))
        return rows


class ProfileManager:
    """Load, mutate and persist the structured :class:`UserProfile`.

    Args:
        path: Where to store the JSON document. Defaults to
            :func:`mimosa.memory.paths.profile_path`.
        vector_store: Optional :class:`MemoryVectorStore`. When provided, scalar
            and list facts are mirrored into the ``user_profile`` collection so
            they're also semantically searchable. Optional and best-effort.
        autosave: Persist to disk automatically after each mutation.
    """

    def __init__(
        self,
        path: Optional[Union[str, Path]] = None,
        *,
        vector_store=None,
        autosave: bool = True,
    ) -> None:
        self.path = Path(path).expanduser() if path else profile_path()
        self.vector_store = vector_store
        self.autosave = autosave
        self.profile = self._load()

    # -- persistence -------------------------------------------------------

    def _load(self) -> UserProfile:
        try:
            if self.path.exists():
                with open(self.path, "r", encoding="utf-8") as fh:
                    return UserProfile.from_dict(json.load(fh))
        except Exception:  # pragma: no cover - corrupt file => empty profile
            logger.warning("Could not load profile at %s; starting fresh", self.path)
        return UserProfile()

    def save(self) -> bool:
        """Atomically persist the profile. Returns ``True`` on success."""
        try:
            self.profile.updated_at = time.time()
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    json.dump(self.profile.to_dict(), fh, indent=2, ensure_ascii=False)
                os.replace(tmp, self.path)
            finally:
                if os.path.exists(tmp):
                    os.remove(tmp)
            return True
        except Exception:  # pragma: no cover - persistence is best-effort
            logger.exception("Could not save profile to %s", self.path)
            return False

    def _maybe_save(self) -> None:
        if self.autosave:
            self.save()

    # -- mutation ----------------------------------------------------------

    def set_scalar(self, field_name: str, value: str) -> None:
        """Set a scalar field (``name``/``occupation``)."""
        if field_name not in SCALAR_FIELDS:
            raise ValueError(f"{field_name!r} is not a scalar profile field")
        setattr(self.profile, field_name, str(value or "").strip())
        self._mirror_fact(field_name, getattr(self.profile, field_name))
        self._maybe_save()

    def add_to_list(self, field_name: str, values: Union[str, List[str]]) -> None:
        """Add one or more values to a list field (deduplicated)."""
        if field_name not in LIST_FIELDS:
            raise ValueError(f"{field_name!r} is not a list profile field")
        if isinstance(values, str):
            values = [values]
        current = list(getattr(self.profile, field_name))
        lowered = {c.lower() for c in current}
        for v in values:
            s = str(v).strip()
            if s and s.lower() not in lowered:
                current.append(s)
                lowered.add(s.lower())
        setattr(self.profile, field_name, current)
        self._mirror_fact(field_name, current)
        self._maybe_save()

    def set_dict_value(self, field_name: str, key: str, value: Any) -> None:
        """Set ``key`` -> ``value`` inside a mapping field."""
        if field_name not in DICT_FIELDS:
            raise ValueError(f"{field_name!r} is not a dict profile field")
        d = dict(getattr(self.profile, field_name))
        d[str(key).strip()] = value
        setattr(self.profile, field_name, d)
        self._mirror_fact(f"{field_name}.{key}", value)
        self._maybe_save()

    def update_from_facts(self, facts: List[Dict[str, Any]]) -> int:
        """Apply a batch of extracted facts. Returns the number applied.

        Each fact is a dict like ``{"field": "skills", "value": "React"}`` or
        ``{"field": "tools", "key": "ide", "value": "VS Code"}``. Unknown
        fields are ignored. Never raises.
        """
        applied = 0
        for fact in facts or []:
            try:
                field_name = str(fact.get("field", "")).strip()
                value = fact.get("value")
                if not field_name or value in (None, "", [], {}):
                    continue
                if field_name in SCALAR_FIELDS:
                    self.set_scalar(field_name, value)
                    applied += 1
                elif field_name in LIST_FIELDS:
                    self.add_to_list(field_name, value)
                    applied += 1
                elif field_name in DICT_FIELDS:
                    key = str(fact.get("key", "")).strip()
                    if key:
                        self.set_dict_value(field_name, key, value)
                        applied += 1
            except Exception:  # pragma: no cover - one bad fact must not abort
                logger.debug("Skipping malformed fact: %r", fact, exc_info=True)
        return applied

    def clear(self) -> None:
        """Wipe the profile entirely (privacy "clear all" option)."""
        self.profile = UserProfile()
        self._maybe_save()

    # -- vector mirroring --------------------------------------------------

    def _mirror_fact(self, key: str, value: Any) -> None:
        """Best-effort: mirror a fact into the vector store for recall."""
        if self.vector_store is None:
            return
        try:
            self.vector_store.add_fact(key, value, source="onboarding")
        except Exception:  # pragma: no cover - best-effort
            logger.debug("Could not mirror fact %r to vector store", key, exc_info=True)

    # -- access ------------------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        return self.profile.to_dict()

    def to_prompt_summary(self) -> str:
        return self.profile.to_prompt_summary()

"""Fact extraction for MimOSA onboarding (M3).

Turns a free-text onboarding answer into a list of *facts* shaped for
:meth:`mimosa.memory.profile_manager.ProfileManager.update_from_facts`::

    {"field": "skills", "value": "python"}
    {"field": "tools", "key": "editor", "value": "vim"}
    {"field": "name", "value": "Alex"}

Two strategies, tried in order:

1. **LLM extraction** — when an LLM provider is supplied, ask it to return
   strict JSON.  Robust parsing tolerates code fences and surrounding prose.
2. **Heuristic fallback** — when no LLM is available (or it errors/returns
   nothing), use the asking question's ``profile_fields`` hint plus a few light
   regexes/keyword rules.  This keeps onboarding useful entirely offline.

The extractor **never raises**: on any failure it returns ``[]`` so the
conversation can always continue.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

try:  # base provider is light, but guard anyway for hermetic imports
    from mimosa.llm.base_provider import Message, Role
except Exception:  # pragma: no cover - defensive
    Message = None  # type: ignore
    Role = None  # type: ignore

__all__ = ["FactExtractor", "ExtractedFact"]


# Fields the profile understands (kept in sync with ProfileManager).
_LIST_FIELDS = {"skills", "interests", "goals"}
_DICT_FIELDS = {"tools", "preferences", "schedule", "relationships"}
_SCALAR_FIELDS = {"name", "occupation"}
_ALL_FIELDS = _LIST_FIELDS | _DICT_FIELDS | _SCALAR_FIELDS


@dataclass
class ExtractedFact:
    """A single structured fact extracted from a response."""

    field: str
    value: str
    key: Optional[str] = None
    confidence: float = 0.6

    def as_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"field": self.field, "value": self.value}
        if self.key:
            out["key"] = self.key
        out["confidence"] = self.confidence
        return out


_SYSTEM_PROMPT = (
    "You extract structured facts about a user from their reply during a "
    "friendly onboarding chat. Return ONLY a JSON array (no prose) of objects "
    "with keys: field, value, and optionally key. Valid fields are: "
    "name, occupation (scalars); skills, interests, goals (lists — emit one "
    "object per item); tools, preferences, schedule, relationships (maps — "
    "include a short 'key'). Only include facts the user actually stated. If "
    "nothing concrete was shared, return []."
)


class FactExtractor:
    """Extract profile facts from onboarding answers."""

    def __init__(self, llm: Any = None, *, max_tokens: int = 400):
        self.llm = llm
        self.max_tokens = max_tokens

    # -- public API -------------------------------------------------------
    def extract(
        self,
        response: str,
        topic: Any = None,
        question: Any = None,
    ) -> List[Dict[str, Any]]:
        """Return a list of fact dicts extracted from *response*.

        *topic*/*question* are optional context objects (from the question
        bank); ``question.profile_fields`` is used as a hint for the heuristic
        fallback.  Never raises.
        """

        text = (response or "").strip()
        if not text:
            return []

        facts: List[Dict[str, Any]] = []
        if self.llm is not None:
            try:
                facts = self._extract_with_llm(text, topic, question)
            except Exception:
                facts = []

        if not facts:
            try:
                facts = self._extract_heuristic(text, question)
            except Exception:
                facts = []

        return self._sanitise(facts)

    # -- LLM path ---------------------------------------------------------
    def _extract_with_llm(
        self, text: str, topic: Any, question: Any
    ) -> List[Dict[str, Any]]:
        if Message is None or Role is None:
            return []
        hint = ""
        fields = getattr(question, "profile_fields", ()) if question else ()
        q_text = getattr(question, "text", "") if question else ""
        if q_text:
            hint += f"\nThe question asked was: {q_text}"
        if fields:
            hint += f"\nLikely relevant fields: {', '.join(fields)}."
        messages = [
            Message(role=Role.SYSTEM, content=_SYSTEM_PROMPT + hint),
            Message(role=Role.USER, content=text),
        ]
        response = self.llm.chat(messages, temperature=0.0, max_tokens=self.max_tokens)
        content = getattr(response, "content", None)
        if not content:
            return []
        return self._parse_json_facts(content)

    @staticmethod
    def _parse_json_facts(content: str) -> List[Dict[str, Any]]:
        """Parse a JSON array of facts from possibly-noisy LLM output."""

        raw = content.strip()
        # Strip code fences.
        if raw.startswith("```"):
            raw = re.sub(r"^```[a-zA-Z]*\n?", "", raw)
            raw = re.sub(r"\n?```$", "", raw).strip()
        # Try direct parse, then locate the first [...] block.
        candidates = [raw]
        match = re.search(r"\[.*\]", raw, re.DOTALL)
        if match:
            candidates.append(match.group(0))
        for cand in candidates:
            try:
                data = json.loads(cand)
            except Exception:
                continue
            if isinstance(data, dict):
                data = [data]
            if isinstance(data, list):
                return [d for d in data if isinstance(d, dict)]
        return []

    # -- heuristic path ---------------------------------------------------
    def _extract_heuristic(
        self, text: str, question: Any
    ) -> List[Dict[str, Any]]:
        facts: List[Dict[str, Any]] = []
        fields = tuple(getattr(question, "profile_fields", ()) or ()) if question else ()
        lowered = text.lower().strip()

        # Name: "I'm Alex", "call me Sam", "my name is …", or a bare single word
        # when the question was specifically about the name.
        if "name" in fields:
            name = self._guess_name(text)
            if name:
                facts.append({"field": "name", "value": name, "confidence": 0.5})

        # Occupation: try "I'm a/an X", "I work as X", "I'm a … developer".
        if "occupation" in fields:
            occ = self._guess_occupation(text)
            if occ:
                facts.append({"field": "occupation", "value": occ, "confidence": 0.45})

        # List-like fields — split the answer into candidate items.
        for lf in ("skills", "interests", "goals"):
            if lf in fields:
                for item in self._split_items(text):
                    facts.append({"field": lf, "value": item, "confidence": 0.4})

        # Dict fields — store the whole (trimmed) answer under a generic key.
        for df in ("tools", "preferences", "schedule", "relationships"):
            if df in fields:
                value = self._first_clause(text)
                if value:
                    facts.append(
                        {
                            "field": df,
                            "key": df[:-1] if df.endswith("s") else df,
                            "value": value,
                            "confidence": 0.35,
                        }
                    )

        # If we had no field hints at all, make a best-effort name guess.
        if not fields:
            name = self._guess_name(text)
            if name:
                facts.append({"field": "name", "value": name, "confidence": 0.4})

        return facts

    # -- small heuristics -------------------------------------------------
    @staticmethod
    def _guess_name(text: str) -> Optional[str]:
        t = text.strip()
        patterns = (
            r"\bmy name is ([A-Z][a-zA-Z'-]+)",
            r"\b(?:i'm|im|i am) ([A-Z][a-zA-Z'-]+)\b",
            r"\bcall me ([A-Z][a-zA-Z'-]+)",
            r"\bit's ([A-Z][a-zA-Z'-]+)",
            r"\bthis is ([A-Z][a-zA-Z'-]+)",
        )
        for pat in patterns:
            m = re.search(pat, t, re.IGNORECASE)
            if m:
                cand = m.group(1)
                if cand.lower() not in {"a", "an", "the", "not", "just"}:
                    return cand
        # Bare single capitalised word ("Alex" / "Alex.")
        bare = t.strip(".!? ")
        if re.fullmatch(r"[A-Z][a-zA-Z'-]+", bare):
            return bare
        return None

    @staticmethod
    def _guess_occupation(text: str) -> Optional[str]:
        t = text.strip()
        patterns = (
            r"\bi work as (?:an? )?([a-zA-Z ]{3,40})",
            r"\b(?:i'm|im|i am) (?:an? )([a-zA-Z ]{3,40})",
            r"\bi'm working as (?:an? )?([a-zA-Z ]{3,40})",
            r"\bi study ([a-zA-Z ]{3,40})",
        )
        for pat in patterns:
            m = re.search(pat, t, re.IGNORECASE)
            if m:
                occ = m.group(1).strip().rstrip(".!?")
                # Cut off at conjunctions/extra clauses.
                occ = re.split(r"\b(?:and|but|who|which|that|because|so)\b", occ)[0].strip()
                if 2 < len(occ) <= 40:
                    return occ
        return None

    @staticmethod
    def _split_items(text: str) -> List[str]:
        """Split a comma/and-separated answer into trimmed candidate items."""

        cleaned = re.sub(
            r"^\s*(i (really )?(like|love|enjoy|prefer|do|play|am into)|my hobbies are|"
            r"i'm into|im into|things like)\s+",
            "",
            text.strip(),
            flags=re.IGNORECASE,
        )
        parts = re.split(r",| and | & |/|;", cleaned)
        items: List[str] = []
        for part in parts:
            item = part.strip().strip(".!?").strip()
            # Drop trailing filler.
            item = re.sub(r"^(and|also|too)\s+", "", item, flags=re.IGNORECASE)
            if 1 < len(item) <= 40 and item.lower() not in {"etc", "stuff", "things"}:
                items.append(item)
        return items[:6]

    @staticmethod
    def _first_clause(text: str) -> str:
        clause = re.split(r"[.!?]", text.strip())[0].strip()
        return clause[:120]

    # -- normalisation ----------------------------------------------------
    @staticmethod
    def _sanitise(facts: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        seen = set()
        for fact in facts:
            if not isinstance(fact, dict):
                continue
            field = str(fact.get("field", "")).strip().lower()
            if field not in _ALL_FIELDS:
                continue
            value = fact.get("value", "")
            if isinstance(value, (list, tuple)):
                value = ", ".join(str(v) for v in value)
            value = str(value).strip()
            if not value:
                continue
            key = fact.get("key")
            key = str(key).strip().lower() if key else None
            if field in _DICT_FIELDS and not key:
                key = field[:-1] if field.endswith("s") else field
            dedup = (field, key, value.lower())
            if dedup in seen:
                continue
            seen.add(dedup)
            clean: Dict[str, Any] = {"field": field, "value": value}
            if key:
                clean["key"] = key
            if "confidence" in fact:
                try:
                    clean["confidence"] = float(fact["confidence"])
                except Exception:
                    pass
            out.append(clean)
        return out

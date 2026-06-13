"""Continuous, in-the-background learning from ordinary conversations (M4).

:class:`ContinuousLearner` is what turns MimOSA from a one-time learner (the
onboarding chat) into a companion that keeps getting to know the user. After
each conversation turn it quietly:

1. extracts any concrete facts the user shared (reusing the onboarding
   :class:`~mimosa.onboarding.fact_extractor.FactExtractor`),
2. folds them into the structured profile,
3. stores the turn in the vector store (with a timestamp) for semantic recall,
4. feeds tool/app mentions and message length to the
   :class:`~mimosa.learning.pattern_detector.PatternDetector`.

Crucially it **does not interrupt the flow** -- it never asks the user to
confirm anything inline. Instead, :meth:`detect_learning_opportunities` returns
*candidates* for the :class:`~mimosa.learning.proactive_questioner.ProactiveQuestioner`
to schedule politely, later.

The whole module is defensive: every public method swallows errors so a
learning hiccup can never break a conversation. When learning is disabled (via
:class:`mimosa.utils.config.LearningSettings`) the learner becomes a no-op.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional

logger = logging.getLogger(__name__)

__all__ = ["ContinuousLearner", "LearningOpportunity", "detect_tool_mentions"]


#: Common desktop tools/apps MimOSA recognises in free text. Extendable; this
#: is only a *hint* source for pattern detection, never an allow-list.
KNOWN_TOOLS = frozenset(
    {
        "firefox", "chrome", "chromium", "brave", "edge", "safari",
        "vscode", "vs code", "code", "vim", "neovim", "nvim", "emacs",
        "sublime", "atom", "pycharm", "intellij", "terminal", "konsole",
        "bash", "zsh", "git", "github", "gitlab", "docker", "kubernetes",
        "slack", "discord", "zoom", "teams", "notion", "obsidian",
        "spotify", "blender", "gimp", "inkscape", "libreoffice", "thunderbird",
        "python", "node", "npm", "react", "django", "flask", "rust", "go",
    }
)

#: Verbs that usually precede an app/tool name ("open firefox", "launch code").
_OPEN_VERBS = re.compile(
    r"\b(?:open|launch|start|run|fire up|bring up|switch to)\s+([a-z][a-z0-9 +.\-]{1,30})",
    re.IGNORECASE,
)

#: A capitalised first name mentioned in passing ("ask Sarah", "tell Mike").
_PERSON_RE = re.compile(
    r"\b(?:with|to|from|for|ask|tell|call|email|meet|met|see|saw)\s+([A-Z][a-z]{2,15})\b"
)


def detect_tool_mentions(text: str) -> List[str]:
    """Return lowercased tool/app names mentioned in *text*. Never raises."""
    try:
        lowered = f" {str(text or '').lower()} "
        found: List[str] = []
        for tool in KNOWN_TOOLS:
            # word-ish boundary match
            if re.search(rf"(?<![a-z0-9]){re.escape(tool)}(?![a-z0-9])", lowered):
                found.append(tool)
        # Verb-led mentions ("open foobar") even if foobar isn't well-known.
        for m in _OPEN_VERBS.finditer(str(text or "")):
            cand = m.group(1).strip().lower()
            cand = re.split(r"\b(?:and|then|please|for|to)\b", cand)[0].strip()
            cand = cand.strip(".!?,")
            if cand and cand not in found and 1 < len(cand) <= 30:
                found.append(cand)
        # de-dup, keep order
        seen, out = set(), []
        for t in found:
            if t not in seen:
                seen.add(t)
                out.append(t)
        return out
    except Exception:  # pragma: no cover - defensive
        return []


def _detect_person_mentions(text: str) -> List[str]:
    try:
        return list(dict.fromkeys(_PERSON_RE.findall(str(text or ""))))
    except Exception:  # pragma: no cover - defensive
        return []


@dataclass
class LearningOpportunity:
    """A candidate moment to ask the user a thoughtful question.

    Attributes:
        kind: "preference" | "context" | "pattern".
        subject: What it's about (a tool name, a person, a pattern key).
        question: A natural, conversational question to (maybe) ask later.
        confidence: ``0.0..1.0`` -- how worthwhile asking seems.
        priority: "low" | "medium" | "high".
    """

    kind: str
    subject: str
    question: str
    confidence: float = 0.5
    priority: str = "low"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "subject": self.subject,
            "question": self.question,
            "confidence": round(float(self.confidence), 3),
            "priority": self.priority,
        }


class ContinuousLearner:
    """Learn from ordinary conversations without interrupting them.

    Args:
        fact_extractor: A :class:`FactExtractor` (or compatible). If omitted one
            is created lazily (heuristic-only unless an ``llm`` is given).
        profile_manager: Optional :class:`ProfileManager` to receive new facts.
        vector_store: Optional :class:`MemoryVectorStore` for semantic storage.
        pattern_detector: Optional :class:`PatternDetector` for behaviour stats.
        llm: Optional LLM provider (only used to build a default extractor).
        enabled: Master on/off switch (mirror of the user's setting).
        clock: Injectable epoch-seconds clock for deterministic tests.
    """

    def __init__(
        self,
        *,
        fact_extractor: Any = None,
        profile_manager: Any = None,
        vector_store: Any = None,
        pattern_detector: Any = None,
        llm: Any = None,
        enabled: bool = True,
        clock=time.time,
    ) -> None:
        self.profile_manager = profile_manager
        self.vector_store = vector_store
        self.pattern_detector = pattern_detector
        self.enabled = bool(enabled)
        self._clock = clock
        if fact_extractor is not None:
            self.fact_extractor = fact_extractor
        else:
            try:
                from mimosa.onboarding.fact_extractor import FactExtractor

                self.fact_extractor = FactExtractor(llm=llm)
            except Exception:  # pragma: no cover - defensive
                self.fact_extractor = None

    # -- main entry point -------------------------------------------------
    def analyze_conversation(
        self,
        user_message: str,
        assistant_response: str = "",
        *,
        timestamp: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Process one conversation turn. Returns a small report dict.

        The report is ``{"facts": [...], "tools": [...], "stored": bool,
        "applied": int}``. Never raises and never blocks on confirmations.
        """
        report: Dict[str, Any] = {"facts": [], "tools": [], "stored": False, "applied": 0}
        if not self.enabled:
            return report
        text = (user_message or "").strip()
        if not text:
            return report
        ts = float(timestamp) if timestamp is not None else float(self._clock())

        # 1) Extract facts.
        facts: List[Dict[str, Any]] = []
        if self.fact_extractor is not None:
            try:
                facts = self.fact_extractor.extract(text)
            except Exception:  # pragma: no cover - defensive
                facts = []
        report["facts"] = facts

        # 2) Fold into the profile.
        if facts and self.profile_manager is not None:
            try:
                report["applied"] = int(self.profile_manager.update_from_facts(facts))
            except Exception:  # pragma: no cover - defensive
                logger.debug("update_from_facts failed", exc_info=True)

        # 3) Store the turn for semantic recall.
        if self.vector_store is not None:
            try:
                self.vector_store.add_conversation_turn(text, assistant_response or "")
                report["stored"] = True
            except Exception:  # pragma: no cover - defensive
                logger.debug("add_conversation_turn failed", exc_info=True)

        # 4) Behavioural patterns: tool mentions + message length.
        tools = detect_tool_mentions(text)
        report["tools"] = tools
        if self.pattern_detector is not None:
            try:
                self.pattern_detector.record_message(text, timestamp=ts)
                for tool in tools:
                    self.pattern_detector.record_tool_use(tool, timestamp=ts)
            except Exception:  # pragma: no cover - defensive
                logger.debug("pattern recording failed", exc_info=True)

        return report

    # -- opportunity detection -------------------------------------------
    def detect_learning_opportunities(
        self, conversation_history: Optional[Iterable[Any]] = None
    ) -> List[LearningOpportunity]:
        """Suggest (don't ask) moments worth a follow-up question. Never raises.

        Combines two signals:

        * **Patterns** from the :class:`PatternDetector` (e.g. a heavily-used
          tool) -> a preference-confirmation question.
        * **People** mentioned in the recent history without known context ->
          a context-gathering question.
        """
        opportunities: List[LearningOpportunity] = []
        try:
            opportunities.extend(self._opportunities_from_patterns())
        except Exception:  # pragma: no cover - defensive
            logger.debug("pattern opportunities failed", exc_info=True)
        try:
            opportunities.extend(
                self._opportunities_from_history(conversation_history or [])
            )
        except Exception:  # pragma: no cover - defensive
            logger.debug("history opportunities failed", exc_info=True)
        # De-dup by (kind, subject), keep highest confidence.
        best: Dict[tuple, LearningOpportunity] = {}
        for opp in opportunities:
            key = (opp.kind, opp.subject.lower())
            if key not in best or opp.confidence > best[key].confidence:
                best[key] = opp
        ranked = sorted(best.values(), key=lambda o: o.confidence, reverse=True)
        return ranked

    def _opportunities_from_patterns(self) -> List[LearningOpportunity]:
        if self.pattern_detector is None:
            return []
        out: List[LearningOpportunity] = []
        for pat in self.pattern_detector.detect_patterns():
            if pat.kind == "tool":
                tool = pat.metadata.get("tool", pat.key.split(":")[-1])
                label = str(tool).capitalize()
                if self._profile_knows_preference(tool):
                    continue
                out.append(
                    LearningOpportunity(
                        kind="preference",
                        subject=str(tool),
                        question=(
                            f"I've noticed you reach for {label} a lot. "
                            f"Is that your go-to? I can default to it for you."
                        ),
                        confidence=pat.confidence,
                        priority="medium" if pat.confidence >= 0.8 else "low",
                    )
                )
            elif pat.kind == "communication":
                out.append(
                    LearningOpportunity(
                        kind="pattern",
                        subject=pat.key,
                        question=(
                            "I want to match your style — would you prefer I keep "
                            "my replies short and snappy?"
                            if "concise" in pat.key
                            else "Happy to go into detail — want me to keep my "
                            "answers thorough?"
                        ),
                        confidence=pat.confidence,
                        priority="low",
                    )
                )
        return out

    def _opportunities_from_history(
        self, history: Iterable[Any]
    ) -> List[LearningOpportunity]:
        out: List[LearningOpportunity] = []
        people: List[str] = []
        for turn in history:
            text = self._turn_text(turn)
            people.extend(_detect_person_mentions(text))
        # Count mentions; only ask about people who recur or whom we don't know.
        counts: Dict[str, int] = {}
        for p in people:
            counts[p] = counts.get(p, 0) + 1
        for person, count in counts.items():
            if self._profile_knows_person(person):
                continue
            conf = min(0.85, 0.45 + 0.2 * count)
            out.append(
                LearningOpportunity(
                    kind="context",
                    subject=person,
                    question=(
                        f"You've mentioned {person} a couple of times — "
                        f"who's {person} to you? It helps me keep track."
                        if count > 1
                        else f"You mentioned {person} — who's {person} to you?"
                    ),
                    confidence=conf,
                    priority="low",
                )
            )
        return out

    # -- profile helpers --------------------------------------------------
    def _profile(self):
        pm = self.profile_manager
        if pm is None:
            return None
        return getattr(pm, "profile", None)

    def _profile_knows_preference(self, tool: str) -> bool:
        prof = self._profile()
        if prof is None:
            return False
        try:
            blob = " ".join(
                [str(prof.preferences), str(prof.tools), str(prof.skills)]
            ).lower()
            return tool.lower() in blob
        except Exception:  # pragma: no cover - defensive
            return False

    def _profile_knows_person(self, person: str) -> bool:
        prof = self._profile()
        if prof is None:
            return False
        try:
            return person.lower() in str(prof.relationships).lower()
        except Exception:  # pragma: no cover - defensive
            return False

    @staticmethod
    def _turn_text(turn: Any) -> str:
        if turn is None:
            return ""
        if isinstance(turn, str):
            return turn
        # ConversationManager.Turn or dict-like.
        for attr in ("user_text", "text", "content"):
            v = getattr(turn, attr, None)
            if v:
                return str(v)
        if isinstance(turn, dict):
            return str(turn.get("user_text") or turn.get("text") or "")
        return ""

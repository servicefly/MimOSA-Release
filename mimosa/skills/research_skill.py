"""Research skill -- balanced, budgeted web research (M6.4).

This is the user-facing entry point for Milestone 6. It recognises research
requests ("research electric cars", "look into the debate about X", "find out
what people are saying about Y") and hands them to a
:class:`~mimosa.research.research_engine.ResearchEngine`, which searches,
budgets the token spend, and synthesizes a balanced, perspective-labelled
answer.

Privacy & degradation
----------------------
* The skill is **safe by default**: if no engine (or an *offline* engine) is
  wired, it does not make surprise network calls -- it explains that web
  research isn't enabled. The host app injects an online engine only when the
  user has enabled web search.
* Only the query text and fetched snippets ever touch the network/LLM, and the
  engine routes sensitive queries to a *local* model via the Privacy Guard.
* Errors never crash the voice loop: :meth:`BaseSkill.run` wraps everything.
"""

from __future__ import annotations

import re
from typing import List, Optional

from mimosa.skills.base_skill import BaseSkill, SkillResult

#: Leading phrases that signal a research intent; stripped to get the topic.
_TRIGGER_PREFIXES = [
    r"research(\s+(on|about|into))?",
    r"look(\s+(up|into))",
    r"dig(\s+into)?",
    r"investigate",
    r"find out(\s+(about|what))?",
    r"do (some |a )?research(\s+(on|about|into))?",
    r"tell me what (people|others|sources|experts) (are )?(say|think)(ing)?(\s+about)?",
    r"what (are people|do people|are sources|do experts) (say|think)(ing)?(\s+about)?",
    r"gather (sources|information|info|evidence)(\s+(on|about))?",
    r"give me a (balanced |rounded )?(overview|summary|rundown)(\s+(of|on|about))?",
    r"(summari[sz]e|overview of) the (debate|discussion|controversy)(\s+(on|about|around))?",
]

_TRIGGER_RE = re.compile(
    r"^\s*(?:please\s+)?(?:can you\s+|could you\s+|would you\s+)?(?:"
    + "|".join(_TRIGGER_PREFIXES)
    + r")\b[:,]?\s*",
    re.IGNORECASE,
)


def extract_topic(text: str) -> str:
    """Strip leading research trigger phrasing to isolate the topic.

    Falls back to the original text if nothing matches, so a bare topic still
    works when the router classified it as research.
    """
    if not text:
        return ""
    stripped = _TRIGGER_RE.sub("", text.strip())
    stripped = stripped.strip().strip("?.").strip()
    return stripped or text.strip()


class ResearchSkill(BaseSkill):
    """Answer research requests with a balanced, budgeted synthesis."""

    name = "research"
    intents = ["research"]
    uses_llm = True

    def __init__(self, llm_provider=None, *, engine=None, include_budget_note: bool = False) -> None:
        super().__init__(llm_provider=llm_provider)
        self._engine = engine
        self.include_budget_note = include_budget_note

    @property
    def engine(self):
        """The research engine, lazily built as an *offline* default if none.

        Building the default lazily (and offline) keeps construction cheap and
        guarantees no network access unless the host explicitly injects an
        online engine.
        """
        if self._engine is None:
            from mimosa.research.research_engine import ResearchEngine

            self._engine = ResearchEngine(llm_provider=self.llm)
        return self._engine

    def handle(self, text: str, context: Optional[List] = None) -> SkillResult:
        topic = extract_topic(text)
        if not topic:
            return SkillResult(
                text="What would you like me to research?",
                success=False,
                skill=self.name,
                metadata={"reason": "empty_topic"},
            )

        report = self.engine.research(topic, context=context)

        # No sources: be explicit about why (offline / no results) rather than
        # implying an authoritative answer.
        if not report.sources:
            online = bool(report.metadata.get("online"))
            if not online:
                msg = (
                    f"I'd need web search enabled to research \"{topic}\". "
                    "You can turn it on in the privacy settings."
                )
            else:
                msg = (
                    f"I couldn't find sources on \"{topic}\" right now. "
                    "Please try again or rephrase."
                )
            return SkillResult(
                text=msg,
                success=False,
                skill=self.name,
                metadata={
                    "topic": topic,
                    "online": online,
                    "num_sources": 0,
                },
            )

        answer = report.speakable(include_budget=self.include_budget_note)
        return SkillResult(
            text=answer,
            skill=self.name,
            metadata={
                "topic": topic,
                "num_sources": len(report.sources),
                "perspectives_present": report.synthesis.perspectives_present,
                "perspectives_missing": report.synthesis.perspectives_missing,
                "balanced": report.synthesis.balanced,
                "used_llm": report.synthesis.used_llm,
                "is_private": report.is_private,
                "used_local": report.used_local,
                "estimated_tokens": report.plan.estimated_total,
                "citations": report.synthesis.citations,
            },
        )

    def _error_message(self) -> str:
        return "Sorry, I couldn't complete that research right now."

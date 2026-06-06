"""Multi-source synthesis with balanced perspective labeling (M6.2).

Given a query and a set of :class:`~mimosa.research.sources.Source` objects, the
:class:`ResearchSynthesizer` produces a single answer that:

* **Groups sources by perspective** (mainstream, alternative, social,
  think-tank, academic, official, ...), so the answer can present *how
  different kinds of source frame the topic* rather than blending them into one
  undifferentiated voice.
* **Labels each perspective explicitly** and **names the gaps** -- if no
  academic or official source was found, the synthesis says so, instead of
  implying false completeness.
* **Cites** the sources it drew from.

Two synthesis paths
-------------------
* **LLM path** (when a provider is supplied): a carefully scoped system prompt
  instructs the model to be balanced, attribute claims to perspectives, avoid
  editorialising, and flag disagreement. Excerpts are truncated to the
  negotiated per-source token cap (M6.3) before being sent.
* **Extractive fallback** (no LLM, or LLM failure): a fully-local, deterministic
  summary that ranks each source's snippet by query-term overlap and lays them
  out under perspective headings. No network, no model -- the feature still
  works headlessly, just more plainly.

Privacy: only text (query + snippets) is sent to the model, and only when a
provider is supplied. The engine (M6.4) chooses a *local* provider when the
Privacy Guard flags the query, so sensitive research stays on-device.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from mimosa.research.sources import (
    Source,
    SourceCategory,
    perspective_label,
    summarize_perspectives,
)
from mimosa.research.token_budget import count_tokens, tokens_to_chars

logger = logging.getLogger("mimosa.research.synthesizer")

#: System prompt enforcing balance, attribution, and concision.
SYNTHESIS_SYSTEM_PROMPT = (
    "You are MimOSA's research assistant. You will be given a question and a "
    "set of sources grouped by perspective (mainstream media, alternative "
    "media, social, think tanks, academic, official, etc.). Write a balanced, "
    "accurate synthesis that:\n"
    "1. Directly answers the question.\n"
    "2. Attributes claims to their perspective (e.g. 'Mainstream outlets "
    "report...', 'Academic sources find...').\n"
    "3. Notes where perspectives agree and disagree.\n"
    "4. Explicitly mentions which perspectives were NOT available, if any.\n"
    "5. Stays neutral: report the spread of views, do not push one.\n"
    "Be concise and factual. Do not invent sources or facts not present in the "
    "provided material."
)

#: Tokens kept in reserve per perspective when laying out the LLM prompt body.
_TOKENS = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> List[str]:
    return _TOKENS.findall((text or "").lower())


@dataclass
class PerspectiveGroup:
    """Sources sharing one perspective category.

    Attributes:
        category: The :class:`SourceCategory` shared by these sources.
        label: Human-readable perspective label.
        sources: The sources in this group (best-first).
    """

    category: SourceCategory
    label: str
    sources: List[Source] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.sources)


@dataclass
class Synthesis:
    """The result of synthesizing a research query.

    Attributes:
        query: The research query.
        answer: The synthesized answer text.
        groups: Sources grouped by perspective.
        perspectives_present: Category values represented.
        perspectives_missing: Desired category values that were absent.
        citations: ``[{"title","url","perspective"}]`` for included sources.
        used_llm: Whether the LLM path produced the answer.
        balanced: Whether >= 2 distinct perspectives were represented.
        metadata: Extra info (e.g. token counts, model).
    """

    query: str
    answer: str
    groups: List[PerspectiveGroup] = field(default_factory=list)
    perspectives_present: List[str] = field(default_factory=list)
    perspectives_missing: List[str] = field(default_factory=list)
    citations: List[Dict[str, str]] = field(default_factory=list)
    used_llm: bool = False
    balanced: bool = False
    metadata: Dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, object]:
        return {
            "query": self.query,
            "answer": self.answer,
            "perspectives_present": list(self.perspectives_present),
            "perspectives_missing": list(self.perspectives_missing),
            "citations": list(self.citations),
            "used_llm": self.used_llm,
            "balanced": self.balanced,
            "metadata": dict(self.metadata),
        }


class ResearchSynthesizer:
    """Synthesize a balanced, perspective-labelled answer from sources.

    Args:
        llm_provider: Optional LLM provider (the
            :class:`~mimosa.llm.base_provider.BaseLLMProvider` interface). When
            ``None`` or when a call fails, the deterministic extractive fallback
            is used.
        max_tokens: Cap on the synthesized answer length (LLM path).
    """

    def __init__(self, llm_provider=None, *, max_tokens: int = 600) -> None:
        self.llm = llm_provider
        self.max_tokens = max_tokens

    # -- grouping ----------------------------------------------------------

    @staticmethod
    def group_by_perspective(sources: List[Source]) -> List[PerspectiveGroup]:
        """Group sources by category, preserving first-seen category order."""
        order: List[SourceCategory] = []
        buckets: Dict[SourceCategory, List[Source]] = {}
        for s in sources:
            cat = s.category or SourceCategory.OTHER
            if cat not in buckets:
                buckets[cat] = []
                order.append(cat)
            buckets[cat].append(s)
        return [
            PerspectiveGroup(category=cat, label=perspective_label(cat), sources=buckets[cat])
            for cat in order
        ]

    # -- public synthesis --------------------------------------------------

    def synthesize(
        self,
        query: str,
        sources: List[Source],
        *,
        per_source_tokens: int = 200,
        context: Optional[List] = None,
    ) -> Synthesis:
        """Produce a :class:`Synthesis` for ``query`` from ``sources``.

        Args:
            query: The research question.
            sources: Sources to synthesize (already budget-selected).
            per_source_tokens: Max tokens of each source's excerpt to include.
            context: Optional conversation history (LLM path).
        """
        groups = self.group_by_perspective(sources)
        persp = summarize_perspectives(sources)
        present = list(persp["present"])
        missing = list(persp["missing"])
        balanced = persp["diversity"] >= 2
        citations = [
            {
                "title": s.title,
                "url": s.url,
                "perspective": s.perspective,
            }
            for s in sources
        ]

        if not sources:
            return Synthesis(
                query=query,
                answer=(
                    "I couldn't find any sources to research that right now. "
                    "Web search may be unavailable or returned no results."
                ),
                groups=groups,
                perspectives_present=present,
                perspectives_missing=missing,
                citations=citations,
                used_llm=False,
                balanced=False,
                metadata={"reason": "no_sources"},
            )

        used_llm = False
        answer: Optional[str] = None
        if self.llm is not None:
            answer = self._synthesize_with_llm(
                query, groups, per_source_tokens=per_source_tokens, context=context
            )
            used_llm = answer is not None

        if answer is None:
            answer = self._extractive_synthesis(
                query, groups, present, missing, per_source_tokens=per_source_tokens
            )

        return Synthesis(
            query=query,
            answer=answer,
            groups=groups,
            perspectives_present=present,
            perspectives_missing=missing,
            citations=citations,
            used_llm=used_llm,
            balanced=balanced,
            metadata={
                "num_sources": len(sources),
                "num_perspectives": persp["diversity"],
            },
        )

    # -- LLM path ----------------------------------------------------------

    def _synthesize_with_llm(
        self,
        query: str,
        groups: List[PerspectiveGroup],
        *,
        per_source_tokens: int,
        context: Optional[List],
    ) -> Optional[str]:
        """Run the LLM synthesis; return ``None`` on any failure (-> fallback)."""
        # Imported lazily so the module has no hard dependency on the llm layer.
        try:
            from mimosa.llm.base_provider import LLMError, Message, Role
        except Exception:  # pragma: no cover - defensive
            return None

        body = self._build_prompt_body(query, groups, per_source_tokens=per_source_tokens)
        messages = [Message(role=Role.SYSTEM, content=SYNTHESIS_SYSTEM_PROMPT)]
        if context:
            messages.extend(context[-4:])
        messages.append(Message(role=Role.USER, content=body))

        try:
            response = self.llm.chat(messages, temperature=0.2, max_tokens=self.max_tokens)
        except LLMError as exc:
            logger.warning("LLM synthesis failed, using extractive fallback: %s", exc)
            return None
        except Exception as exc:  # noqa: BLE001 - any provider error -> fallback
            logger.warning("LLM synthesis errored, using extractive fallback: %s", exc)
            return None

        text = (response.content or "").strip()
        return text or None

    def _build_prompt_body(
        self, query: str, groups: List[PerspectiveGroup], *, per_source_tokens: int
    ) -> str:
        """Build the user-message body listing grouped, truncated sources."""
        max_chars = tokens_to_chars(per_source_tokens)
        lines = [f"Question: {query}", "", "Sources by perspective:"]
        for group in groups:
            lines.append("")
            lines.append(f"## {group.label} ({group.count})")
            for s in group.sources:
                excerpt = self._truncate(s.snippet or s.title, max_chars)
                cite = f"- {s.title}".rstrip()
                if s.domain:
                    cite += f" [{s.domain}]"
                lines.append(cite)
                if excerpt:
                    lines.append(f"  {excerpt}")
        present_labels = [g.label for g in groups]
        lines.append("")
        lines.append(
            "Perspectives represented: " + (", ".join(present_labels) or "none") + "."
        )
        lines.append(
            "Write a balanced synthesis answering the question, attributing "
            "claims to perspectives and noting any missing perspectives."
        )
        return "\n".join(lines)

    # -- extractive fallback ----------------------------------------------

    def _extractive_synthesis(
        self,
        query: str,
        groups: List[PerspectiveGroup],
        present: List[str],
        missing: List[str],
        *,
        per_source_tokens: int,
    ) -> str:
        """Deterministic, local synthesis: ranked snippets under headings."""
        q_terms = set(_tokenize(query))
        max_chars = tokens_to_chars(per_source_tokens)
        out: List[str] = []
        out.append(f"Here is what I found on: {query}")
        for group in groups:
            out.append("")
            out.append(f"{group.label}:")
            ranked = sorted(
                group.sources,
                key=lambda s: (-self._overlap(s, q_terms), s.rank),
            )
            for s in ranked:
                excerpt = self._truncate(s.snippet or s.title, max_chars)
                bullet = f"  • {excerpt}" if excerpt else f"  • {s.title}"
                if s.domain:
                    bullet += f" ({s.domain})"
                out.append(bullet)
        if missing:
            missing_labels = [perspective_label(SourceCategory(c)) for c in missing]
            out.append("")
            out.append(
                "Perspectives not represented in these results: "
                + ", ".join(missing_labels)
                + "."
            )
        if len(present) >= 2:
            out.append("")
            out.append(
                "Note: the above reflects multiple perspectives; weigh them "
                "against each other."
            )
        return "\n".join(out)

    @staticmethod
    def _overlap(source: Source, q_terms: set) -> int:
        if not q_terms:
            return 0
        terms = set(_tokenize(source.text))
        return len(terms & q_terms)

    @staticmethod
    def _truncate(text: str, max_chars: int) -> str:
        text = (text or "").strip()
        if max_chars <= 0 or len(text) <= max_chars:
            return text
        cut = text[:max_chars].rsplit(" ", 1)[0].strip()
        return (cut or text[:max_chars]).rstrip(".,;:") + "…"

"""Research orchestrator (M6.4).

:class:`ResearchEngine` wires the three research capabilities together into one
call:

1. **Search** (M6.1) -- ask the :class:`~mimosa.research.search.SearchClient`
   for category-labelled sources.
2. **Budget** (M6.3) -- have the
   :class:`~mimosa.research.token_budget.BudgetNegotiator` decide how many
   sources fit and how much excerpt each gets, then record the spend so future
   budgets self-calibrate.
3. **Synthesize** (M6.2) -- ask the
   :class:`~mimosa.research.synthesizer.ResearchSynthesizer` for a balanced,
   perspective-labelled answer over the budget-selected sources.

Privacy routing
---------------
If a :class:`~mimosa.memory.privacy_guard.PrivacyGuard` is supplied, the engine
assesses the query first. A query flagged sensitive is synthesized by a *local*
provider (via the guard's ``create_provider_for``), so private research never
reaches the cloud. Without a guard the engine simply uses the provider it was
given. Everything degrades gracefully: no search backend -> "no sources"; no
LLM -> extractive synthesis.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from mimosa.research.search import SearchClient
from mimosa.research.sources import Source, summarize_perspectives
from mimosa.research.synthesizer import ResearchSynthesizer, Synthesis
from mimosa.research.token_budget import (
    BudgetNegotiator,
    BudgetPlan,
    TokenBudget,
    count_tokens,
)

logger = logging.getLogger("mimosa.research.research_engine")

#: Default cap on sources fed into a single research task.
DEFAULT_MAX_SOURCES = 6


@dataclass
class ResearchReport:
    """The full result of a research task.

    Attributes:
        query: The research query.
        synthesis: The :class:`Synthesis` (answer + perspectives + citations).
        plan: The negotiated :class:`BudgetPlan`.
        sources: The sources actually included (post-budget).
        all_sources: Every source returned by search (pre-budget).
        is_private: Whether the privacy guard flagged the query sensitive.
        used_local: Whether a local provider was used for synthesis.
        perspectives: Perspective summary (present/missing/counts).
        metadata: Extra info (timings, token estimates, etc.).
    """

    query: str
    synthesis: Synthesis
    plan: BudgetPlan
    sources: List[Source] = field(default_factory=list)
    all_sources: List[Source] = field(default_factory=list)
    is_private: bool = False
    used_local: bool = False
    perspectives: Dict[str, object] = field(default_factory=dict)
    metadata: Dict[str, object] = field(default_factory=dict)

    @property
    def answer(self) -> str:
        """The synthesized answer text (what gets spoken/shown)."""
        return self.synthesis.answer

    def speakable(self, *, include_budget: bool = False) -> str:
        """A voice/chat-friendly rendering of the report."""
        text = self.synthesis.answer
        if include_budget:
            text = f"{self.plan.negotiation_message()}\n\n{text}"
        return text

    def to_dict(self) -> Dict[str, object]:
        return {
            "query": self.query,
            "synthesis": self.synthesis.to_dict(),
            "plan": self.plan.to_dict(),
            "sources": [s.to_dict() for s in self.sources],
            "is_private": self.is_private,
            "used_local": self.used_local,
            "perspectives": dict(self.perspectives),
            "metadata": dict(self.metadata),
        }


class ResearchEngine:
    """Orchestrate search -> budget -> synthesis for a research query.

    Args:
        search_client: The :class:`SearchClient` (offline if its backend is
            ``None``).
        llm_provider: Default LLM provider for synthesis. May be ``None``
            (extractive fallback).
        negotiator: Optional :class:`BudgetNegotiator`; one is built from
            ``budget`` if omitted.
        budget: Optional :class:`TokenBudget` used when building a negotiator.
        synthesizer: Optional explicit :class:`ResearchSynthesizer`. When given
            it is used as-is (privacy provider routing is then the caller's
            responsibility); otherwise the engine builds one per call.
        privacy_guard: Optional privacy guard for sensitive-query local routing.
        preference_learner: Optional M5.2 learner for cost-pattern learning
            (passed to the negotiator if it builds one).
        settings: Optional app config (passed to the guard for provider choice).
        max_sources: Default cap on sources per research task.
    """

    def __init__(
        self,
        search_client: Optional[SearchClient] = None,
        llm_provider=None,
        *,
        negotiator: Optional[BudgetNegotiator] = None,
        budget: Optional[TokenBudget] = None,
        synthesizer: Optional[ResearchSynthesizer] = None,
        privacy_guard=None,
        preference_learner=None,
        settings=None,
        max_sources: int = DEFAULT_MAX_SOURCES,
    ) -> None:
        self.search_client = search_client or SearchClient(backend=None)
        self.llm_provider = llm_provider
        self.negotiator = negotiator or BudgetNegotiator(
            budget=budget, preference_learner=preference_learner
        )
        self._explicit_synthesizer = synthesizer
        self.privacy_guard = privacy_guard
        self.settings = settings
        self.max_sources = max(1, int(max_sources))

    def research(
        self,
        query: str,
        *,
        context: Optional[List] = None,
        topic: Optional[str] = None,
        max_sources: Optional[int] = None,
        budget: Optional[TokenBudget] = None,
    ) -> ResearchReport:
        """Research ``query`` end-to-end and return a :class:`ResearchReport`.

        Never raises: each stage degrades gracefully. ``topic`` (defaults to a
        coarse label derived from the query) is the key under which cost
        patterns are learned.
        """
        query = (query or "").strip()
        cap = max_sources if max_sources is not None else self.max_sources
        topic = topic or self._topic_for(query)

        # 1. Search (M6.1)
        all_sources = self.search_client.search(query, max_results=max(cap * 2, cap))

        # 2. Budget negotiation (M6.3)
        plan = self.negotiator.plan(query, all_sources, budget=budget, max_sources=cap)
        included = [all_sources[i] for i in plan.included_indices]

        # Privacy routing (decide provider for synthesis)
        is_private, used_local, provider = self._select_provider(query)

        # 3. Synthesis (M6.2)
        synthesizer = self._explicit_synthesizer or ResearchSynthesizer(
            provider, max_tokens=plan.estimated_output_tokens or 600
        )
        synthesis = synthesizer.synthesize(
            query,
            included,
            per_source_tokens=plan.per_source_tokens or 200,
            context=context,
        )

        # Cost-pattern learning: record the actual spend for this topic.
        actual_tokens = self._actual_tokens(plan, synthesis)
        self.negotiator.record_usage(topic, actual_tokens)

        perspectives = summarize_perspectives(included)

        return ResearchReport(
            query=query,
            synthesis=synthesis,
            plan=plan,
            sources=included,
            all_sources=all_sources,
            is_private=is_private,
            used_local=used_local,
            perspectives=perspectives,
            metadata={
                "topic": topic,
                "actual_tokens": actual_tokens,
                "online": self.search_client.online,
            },
        )

    # -- helpers -----------------------------------------------------------

    def _select_provider(self, query: str):
        """Return ``(is_private, used_local, provider)`` for synthesis."""
        # If the caller injected a synthesizer, respect its provider entirely.
        if self._explicit_synthesizer is not None:
            is_private = False
            if self.privacy_guard is not None:
                try:
                    is_private = self.privacy_guard.is_private(query)
                except Exception:  # pragma: no cover - defensive
                    is_private = False
            return is_private, False, None

        if self.privacy_guard is None:
            return False, False, self.llm_provider

        try:
            is_private = self.privacy_guard.is_private(query)
        except Exception:  # pragma: no cover - defensive
            is_private = False

        if not is_private:
            return False, False, self.llm_provider

        # Sensitive query: route to a local provider via the guard.
        try:
            provider, use_local = self.privacy_guard.create_provider_for(query)
            return True, bool(use_local), provider
        except Exception as exc:  # noqa: BLE001 - never fail research on routing
            logger.warning("Privacy provider routing failed, staying offline: %s", exc)
            # Fail safe: do NOT use the (possibly cloud) default provider for a
            # private query -- fall back to extractive (no provider).
            return True, True, None

    @staticmethod
    def _actual_tokens(plan: BudgetPlan, synthesis: Synthesis) -> int:
        """Best-effort actual token spend = prompt estimate + answer tokens."""
        answer_tokens = count_tokens(synthesis.answer)
        return plan.estimated_prompt_tokens + answer_tokens

    @staticmethod
    def _topic_for(query: str) -> str:
        """Derive a coarse topic key for cost learning (first salient word)."""
        words = [w for w in (query or "").lower().split() if len(w) > 3]
        return words[0] if words else "general"

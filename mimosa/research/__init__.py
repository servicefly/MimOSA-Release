"""MimOSA research capabilities (Milestone 6).

This package gives MimOSA the ability to *research* a topic: search the web,
aggregate sources from across the spectrum (mainstream, alternative, social,
video, think-tank, academic, official), synthesize a **balanced** answer that
labels each perspective, and do it all under an explicit **token budget** the
user can negotiate.

Design principles (continuous with the rest of MimOSA)
------------------------------------------------------
* **Privacy-first / local-first.** Only *text* (the query and fetched snippets)
  ever crosses the network, and only when the user has allowed an outbound
  provider. The synthesis step honours the Privacy Guard: a query flagged
  private is synthesized by a *local* model. No telemetry, ever.
* **Graceful degradation.** Every external dependency is optional. No network →
  search returns nothing and the engine says so cleanly. No LLM → synthesis
  falls back to a deterministic extractive summary. No ``tiktoken`` → token
  counting falls back to a character heuristic.
* **Dependency injection & testability.** Search backends, the LLM provider,
  the clock, and the preference learner are all injected, so the whole pipeline
  runs offline and deterministically in tests.

Sub-milestones
--------------
* **M6.1** -- :mod:`mimosa.research.search` + :mod:`mimosa.research.sources`:
  web search and source aggregation with category / perspective labeling.
* **M6.2** -- :mod:`mimosa.research.synthesizer`: multi-source synthesis with
  balanced perspective labeling.
* **M6.3** -- :mod:`mimosa.research.token_budget`: token estimation, budget
  negotiation, and cost-pattern learning.
* **M6.4** -- :mod:`mimosa.research.research_engine`: the orchestrator that ties
  search, budgeting, and synthesis together (used by ``ResearchSkill``).
"""

from __future__ import annotations

from mimosa.research.sources import (
    SourceCategory,
    Source,
    PERSPECTIVE_LABELS,
    classify_domain,
    classify_url,
    perspective_label,
    summarize_perspectives,
)
from mimosa.research.token_budget import (
    BudgetNegotiator,
    BudgetPlan,
    TokenBudget,
    count_tokens,
    estimate_tokens,
    HAS_TIKTOKEN,
)
from mimosa.research.search import (
    SearchClient,
    SearchResult,
    SearchBackend,
    DuckDuckGoBackend,
    StaticBackend,
    HAS_REQUESTS,
)
from mimosa.research.synthesizer import (
    ResearchSynthesizer,
    Synthesis,
    PerspectiveGroup,
)
from mimosa.research.research_engine import (
    ResearchEngine,
    ResearchReport,
)

__all__ = [
    # sources (M6.1)
    "SourceCategory",
    "Source",
    "PERSPECTIVE_LABELS",
    "classify_domain",
    "classify_url",
    "perspective_label",
    "summarize_perspectives",
    # search (M6.1)
    "SearchClient",
    "SearchResult",
    "SearchBackend",
    "DuckDuckGoBackend",
    "StaticBackend",
    "HAS_REQUESTS",
    # token budget (M6.3)
    "BudgetNegotiator",
    "BudgetPlan",
    "TokenBudget",
    "count_tokens",
    "estimate_tokens",
    "HAS_TIKTOKEN",
    # synthesis (M6.2)
    "ResearchSynthesizer",
    "Synthesis",
    "PerspectiveGroup",
    # engine (M6.4)
    "ResearchEngine",
    "ResearchReport",
]

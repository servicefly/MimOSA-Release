"""Token-budget estimation & negotiation, with cost-pattern learning (M6.3).

Research can be token-hungry: every source you feed the model costs prompt
tokens, and a thorough answer costs completion tokens. Rather than silently
spending (latency + money +, for cloud models, data exposure), MimOSA makes the
cost **explicit and negotiable**:

* :func:`count_tokens` / :func:`estimate_tokens` -- count tokens with
  ``tiktoken`` when available, else a deterministic character heuristic
  (graceful degradation -- no hard dependency).
* :class:`TokenBudget` -- a cap plus reservations (system prompt, answer
  headroom) and helpers to see what's left for *evidence*.
* :class:`BudgetPlan` -- the concrete, human-readable result of negotiation:
  how many sources fit, how much excerpt each gets, the estimated spend, and
  whether it fit under budget.
* :class:`BudgetNegotiator` -- turns a query + candidate sources + budget into a
  :class:`BudgetPlan`, trimming excerpts and dropping low-value sources (while
  preserving *perspective diversity*) until it fits. It can also **learn cost
  patterns** via the M5.2 preference learner, so repeat research on a topic kind
  proposes a sensible default budget next time.

Everything is local and deterministic. The negotiator never makes a network or
LLM call; it just plans the spend.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

logger = logging.getLogger("mimosa.research.token_budget")

# Optional precise tokenizer. We degrade to a char heuristic when unavailable so
# the feature never hard-depends on tiktoken.
try:  # pragma: no cover - exercised indirectly; availability is environmental
    import tiktoken  # type: ignore

    HAS_TIKTOKEN = True
except Exception:  # pragma: no cover - tiktoken missing is a valid state
    tiktoken = None  # type: ignore
    HAS_TIKTOKEN = False

#: Rough characters-per-token used by the heuristic fallback. ~4 chars/token is
#: the well-known approximation for English text with GPT-style BPE tokenizers.
_CHARS_PER_TOKEN = 4

#: Default model hint used to pick a tiktoken encoding.
_DEFAULT_ENCODING = "cl100k_base"

# Cache encodings so we don't rebuild them on every call.
_ENCODING_CACHE: Dict[str, object] = {}


def _get_encoding(model: Optional[str]):
    """Return a cached tiktoken encoding, or ``None`` if unavailable."""
    if not HAS_TIKTOKEN:
        return None
    key = model or _DEFAULT_ENCODING
    if key in _ENCODING_CACHE:
        return _ENCODING_CACHE[key]
    enc = None
    try:
        if model:
            try:
                enc = tiktoken.encoding_for_model(model)  # type: ignore
            except Exception:
                enc = tiktoken.get_encoding(_DEFAULT_ENCODING)  # type: ignore
        else:
            enc = tiktoken.get_encoding(_DEFAULT_ENCODING)  # type: ignore
    except Exception:  # pragma: no cover - defensive
        enc = None
    _ENCODING_CACHE[key] = enc
    return enc


def count_tokens(text: str, *, model: Optional[str] = None) -> int:
    """Count tokens in ``text``.

    Uses ``tiktoken`` when available for an accurate count; otherwise falls back
    to a ``ceil(len/4)`` character heuristic. Always returns ``>= 0`` and is
    deterministic for a given environment.
    """
    if not text:
        return 0
    enc = _get_encoding(model)
    if enc is not None:
        try:
            return len(enc.encode(text))
        except Exception:  # pragma: no cover - defensive
            pass
    return estimate_tokens(text)


def estimate_tokens(text: str) -> int:
    """Estimate tokens with the character heuristic (no tiktoken needed)."""
    if not text:
        return 0
    return max(1, math.ceil(len(text) / _CHARS_PER_TOKEN))


def tokens_to_chars(tokens: int) -> int:
    """Inverse of the heuristic: approx characters for ``tokens`` tokens."""
    return max(0, int(tokens) * _CHARS_PER_TOKEN)


@dataclass
class TokenBudget:
    """A negotiable cap on tokens for a single research task.

    Attributes:
        max_total: Hard ceiling on total tokens (prompt + completion).
        reserve_output: Tokens held back for the model's answer.
        reserve_overhead: Tokens held back for the system prompt, the question,
            formatting, and per-message overhead.
        model: Optional model hint for accurate tokenization.
    """

    max_total: int = 3000
    reserve_output: int = 600
    reserve_overhead: int = 300
    model: Optional[str] = None

    def __post_init__(self) -> None:
        self.max_total = max(1, int(self.max_total))
        self.reserve_output = max(0, int(self.reserve_output))
        self.reserve_overhead = max(0, int(self.reserve_overhead))

    @property
    def evidence_budget(self) -> int:
        """Tokens available for source excerpts after reservations.

        Never negative: if reservations exceed ``max_total`` this is 0.
        """
        return max(0, self.max_total - self.reserve_output - self.reserve_overhead)

    def with_max(self, max_total: int) -> "TokenBudget":
        """Return a copy with a different ``max_total`` (used by negotiation)."""
        return TokenBudget(
            max_total=max_total,
            reserve_output=self.reserve_output,
            reserve_overhead=self.reserve_overhead,
            model=self.model,
        )


@dataclass
class BudgetPlan:
    """A concrete, negotiated spending plan for a research task.

    Attributes:
        query: The research query the plan is for.
        num_sources: How many sources the plan includes.
        per_source_tokens: Token cap applied to each source's excerpt.
        estimated_prompt_tokens: Estimated prompt spend (overhead + query +
            included excerpts).
        estimated_output_tokens: Tokens reserved for the answer.
        estimated_total: ``prompt + output`` estimate.
        max_total: The ceiling this plan was negotiated against.
        within_budget: Whether ``estimated_total <= max_total``.
        included_indices: Indices (into the candidate list) that were kept.
        dropped_indices: Indices that were dropped to fit the budget.
        categories: Perspective categories represented by included sources.
        notes: Human-readable notes about the negotiation decisions.
    """

    query: str
    num_sources: int
    per_source_tokens: int
    estimated_prompt_tokens: int
    estimated_output_tokens: int
    estimated_total: int
    max_total: int
    within_budget: bool
    included_indices: List[int] = field(default_factory=list)
    dropped_indices: List[int] = field(default_factory=list)
    categories: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    def negotiation_message(self) -> str:
        """A short, speakable summary suitable for asking the user to proceed."""
        src_word = "source" if self.num_sources == 1 else "sources"
        msg = (
            f"This research will use about {self.estimated_total} tokens "
            f"across {self.num_sources} {src_word}"
        )
        if self.categories:
            persp = len(self.categories)
            persp_word = "perspective" if persp == 1 else "perspectives"
            msg += f" spanning {persp} {persp_word}"
        msg += f" (budget {self.max_total})."
        if not self.within_budget:
            msg += " That still exceeds the budget, so the answer may be trimmed."
        return msg

    def to_dict(self) -> Dict[str, object]:
        return {
            "query": self.query,
            "num_sources": self.num_sources,
            "per_source_tokens": self.per_source_tokens,
            "estimated_prompt_tokens": self.estimated_prompt_tokens,
            "estimated_output_tokens": self.estimated_output_tokens,
            "estimated_total": self.estimated_total,
            "max_total": self.max_total,
            "within_budget": self.within_budget,
            "included_indices": list(self.included_indices),
            "dropped_indices": list(self.dropped_indices),
            "categories": list(self.categories),
            "notes": list(self.notes),
        }


#: Preference-learner category under which research cost patterns are stored.
COST_LEARNING_CATEGORY = "research_cost"

#: Default minimum tokens an included source excerpt should get; below this the
#: excerpt is too short to be useful, so we'd rather drop the source.
_MIN_PER_SOURCE_TOKENS = 40


def _cost_bucket(tokens: int) -> str:
    """Bucket an actual token spend into a coarse, learnable label."""
    if tokens <= 1000:
        return "small"
    if tokens <= 2500:
        return "medium"
    if tokens <= 5000:
        return "large"
    return "xlarge"

#: Representative token budget per cost bucket, used when proposing a default.
_BUCKET_BUDGET = {
    "small": 1500,
    "medium": 3000,
    "large": 4500,
    "xlarge": 6000,
}


class BudgetNegotiator:
    """Plan and negotiate the token spend of a research task.

    The negotiator is deliberately conservative: it estimates the full cost,
    and if it would exceed the budget it first **shrinks per-source excerpts**,
    then **drops the least valuable sources** -- but it tries to preserve
    *perspective diversity*, keeping at least one source per represented
    category for as long as it can.

    Args:
        budget: The :class:`TokenBudget` to plan against.
        preference_learner: Optional M5.2 ``PreferenceLearner`` used to record
            and recall cost patterns (cost-pattern learning). Injected to keep
            this module decoupled and testable.
    """

    def __init__(
        self,
        budget: Optional[TokenBudget] = None,
        *,
        preference_learner=None,
    ) -> None:
        self.budget = budget or TokenBudget()
        self.preference_learner = preference_learner

    # -- planning ----------------------------------------------------------

    def plan(
        self,
        query: str,
        sources: Sequence,
        *,
        budget: Optional[TokenBudget] = None,
        max_sources: Optional[int] = None,
    ) -> BudgetPlan:
        """Negotiate a :class:`BudgetPlan` for ``query`` over ``sources``.

        Args:
            query: The research query.
            sources: Candidate sources (objects with ``.text`` and ``.category``
                attributes -- e.g. :class:`mimosa.research.sources.Source`).
                Assumed to be ordered best-first.
            budget: Override the negotiator's default budget for this call.
            max_sources: Optional hard cap on how many sources to include.

        Returns:
            A :class:`BudgetPlan`. Always returns a plan, even for zero sources.
        """
        budget = budget or self.budget
        model = budget.model
        notes: List[str] = []

        overhead = budget.reserve_overhead + count_tokens(query, model=model)
        evidence_budget = budget.evidence_budget

        candidates = list(sources)
        if max_sources is not None:
            candidates = candidates[: max(0, int(max_sources))]

        if not candidates or evidence_budget <= 0:
            if not candidates:
                notes.append("No sources available to include.")
            if evidence_budget <= 0:
                notes.append("Budget too small to include any evidence.")
            return BudgetPlan(
                query=query,
                num_sources=0,
                per_source_tokens=0,
                estimated_prompt_tokens=overhead,
                estimated_output_tokens=budget.reserve_output,
                estimated_total=overhead + budget.reserve_output,
                max_total=budget.max_total,
                within_budget=(overhead + budget.reserve_output) <= budget.max_total,
                included_indices=[],
                dropped_indices=list(range(len(candidates))),
                categories=[],
                notes=notes,
            )

        # Full token cost of each candidate's text (uncapped).
        costs = [count_tokens(getattr(s, "text", str(s)), model=model) for s in candidates]

        # Start by trying to include all candidates, then shrink/drop to fit.
        included = list(range(len(candidates)))

        def per_source_cap(n: int) -> int:
            if n <= 0:
                return 0
            return max(_MIN_PER_SOURCE_TOKENS, evidence_budget // n)

        # Iteratively drop the least valuable source while the per-source cap
        # would fall below the useful minimum.
        while included:
            cap = per_source_cap(len(included))
            if cap >= _MIN_PER_SOURCE_TOKENS or len(included) == 1:
                break
            drop = self._least_valuable(included, candidates, costs)
            included.remove(drop)
            notes.append(f"Dropped source #{drop} to keep excerpts useful.")

        cap = per_source_cap(len(included))
        # Evidence spend is the sum of min(cost, cap) over included sources.
        evidence_spend = sum(min(costs[i], cap) for i in included)

        # If still over the evidence budget (e.g. one giant source), drop more.
        while included and evidence_spend > evidence_budget and len(included) > 1:
            drop = self._least_valuable(included, candidates, costs)
            included.remove(drop)
            notes.append(f"Dropped source #{drop} to fit the token budget.")
            cap = per_source_cap(len(included))
            evidence_spend = sum(min(costs[i], cap) for i in included)

        prompt_tokens = overhead + evidence_spend
        total = prompt_tokens + budget.reserve_output
        within = total <= budget.max_total

        included.sort()
        dropped = [i for i in range(len(candidates)) if i not in included]
        categories = self._distinct_categories(included, candidates)

        if within:
            notes.append("Plan fits within the token budget.")
        else:
            notes.append("Plan exceeds the budget even after trimming; answer may be shortened.")

        return BudgetPlan(
            query=query,
            num_sources=len(included),
            per_source_tokens=cap,
            estimated_prompt_tokens=prompt_tokens,
            estimated_output_tokens=budget.reserve_output,
            estimated_total=total,
            max_total=budget.max_total,
            within_budget=within,
            included_indices=included,
            dropped_indices=dropped,
            categories=categories,
            notes=notes,
        )

    def _least_valuable(
        self, included: List[int], candidates: Sequence, costs: List[int]
    ) -> int:
        """Pick the index to drop.

        Preference order for *dropping*:
        1. Prefer dropping a source whose category is over-represented (so we
           preserve perspective diversity).
        2. Among equally (un)diverse choices, drop the lowest-ranked (largest
           original index), breaking ties toward the most expensive.
        """
        # Count categories among the currently-included sources.
        cat_counts: Dict[str, int] = {}
        for i in included:
            cat = self._category_value(candidates[i])
            cat_counts[cat] = cat_counts.get(cat, 0) + 1

        def drop_key(i: int):
            cat = self._category_value(candidates[i])
            # over-represented categories (count > 1) are safer to drop
            over_represented = 1 if cat_counts.get(cat, 0) > 1 else 0
            return (over_represented, i, costs[i])

        # We want to drop the "most droppable": highest over_represented, then
        # highest index (lowest rank), then highest cost.
        return max(included, key=drop_key)

    @staticmethod
    def _category_value(source) -> str:
        cat = getattr(source, "category", None)
        if cat is None:
            return "other"
        return getattr(cat, "value", str(cat))

    def _distinct_categories(self, included: List[int], candidates: Sequence) -> List[str]:
        seen: List[str] = []
        for i in included:
            cat = self._category_value(candidates[i])
            if cat not in seen:
                seen.append(cat)
        return seen

    # -- cost-pattern learning (builds on M5.2) ----------------------------

    def record_usage(self, topic: str, actual_tokens: int) -> None:
        """Record an actual research spend so future budgets self-calibrate.

        Stores a coarse cost *bucket* for the topic via the preference learner.
        No-op when no learner is wired (graceful degradation).
        """
        if self.preference_learner is None:
            return
        bucket = _cost_bucket(max(0, int(actual_tokens)))
        try:
            self.preference_learner.observe(COST_LEARNING_CATEGORY, topic, bucket)
        except Exception:  # pragma: no cover - learning must never break research
            logger.exception("Failed to record research cost pattern")

    def suggest_budget(self, topic: str, *, default: Optional[int] = None) -> int:
        """Suggest a ``max_total`` for ``topic`` from learned cost patterns.

        Returns the budget associated with the learned cost bucket if a
        confident pattern exists, else ``default`` (or the negotiator's current
        ``max_total``).
        """
        fallback = default if default is not None else self.budget.max_total
        if self.preference_learner is None:
            return fallback
        try:
            predicted = self.preference_learner.predict_value(
                COST_LEARNING_CATEGORY, topic
            )
        except Exception:  # pragma: no cover - defensive
            return fallback
        if not predicted:
            return fallback
        return _BUCKET_BUDGET.get(predicted, fallback)

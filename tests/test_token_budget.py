"""Tests for token-budget estimation & negotiation (M6.3).

Offline and deterministic. Exercises token counting (tiktoken when present,
heuristic otherwise), the TokenBudget reservations, the BudgetNegotiator's
trimming/dropping logic (including perspective-diversity preservation), and
cost-pattern learning via an in-memory PreferenceLearner.
"""

from __future__ import annotations

import pytest

from mimosa.memory.preference_learner import PreferenceLearner
from mimosa.research.sources import Source, SourceCategory
from mimosa.research.token_budget import (
    COST_LEARNING_CATEGORY,
    BudgetNegotiator,
    BudgetPlan,
    TokenBudget,
    count_tokens,
    estimate_tokens,
    tokens_to_chars,
)


# ---------------------------------------------------------------------------
# token counting
# ---------------------------------------------------------------------------

def test_count_tokens_empty():
    assert count_tokens("") == 0
    assert count_tokens(None) == 0  # type: ignore[arg-type]


def test_count_tokens_positive():
    assert count_tokens("hello world this is a sentence") > 0


def test_estimate_tokens_heuristic():
    # ~4 chars per token, ceil, min 1.
    assert estimate_tokens("a") == 1
    assert estimate_tokens("abcd") == 1
    assert estimate_tokens("abcde") == 2
    assert estimate_tokens("") == 0


def test_tokens_to_chars_inverse():
    assert tokens_to_chars(10) == 40
    assert tokens_to_chars(0) == 0
    assert tokens_to_chars(-5) == 0


def test_count_tokens_monotonic_with_length():
    short = count_tokens("hello")
    long = count_tokens("hello " * 50)
    assert long > short


# ---------------------------------------------------------------------------
# TokenBudget
# ---------------------------------------------------------------------------

def test_evidence_budget_subtracts_reservations():
    b = TokenBudget(max_total=3000, reserve_output=600, reserve_overhead=300)
    assert b.evidence_budget == 2100


def test_evidence_budget_never_negative():
    b = TokenBudget(max_total=100, reserve_output=600, reserve_overhead=300)
    assert b.evidence_budget == 0


def test_budget_post_init_clamps():
    b = TokenBudget(max_total=-5, reserve_output=-1, reserve_overhead=-1)
    assert b.max_total == 1
    assert b.reserve_output == 0
    assert b.reserve_overhead == 0


def test_with_max_copies_reservations():
    b = TokenBudget(max_total=3000, reserve_output=500, reserve_overhead=200, model="m")
    b2 = b.with_max(1000)
    assert b2.max_total == 1000
    assert b2.reserve_output == 500
    assert b2.reserve_overhead == 200
    assert b2.model == "m"


# ---------------------------------------------------------------------------
# BudgetNegotiator.plan
# ---------------------------------------------------------------------------

def _sources(n, *, cat=SourceCategory.MAINSTREAM, words=20):
    out = []
    for i in range(n):
        out.append(
            Source(
                title=f"Source {i}",
                url=f"https://example{i}.com/a",
                snippet=" ".join(["word"] * words),
                category=cat,
                rank=i,
            )
        )
    return out


def test_plan_zero_sources():
    neg = BudgetNegotiator(TokenBudget(max_total=3000))
    plan = neg.plan("topic", [])
    assert plan.num_sources == 0
    assert plan.included_indices == []
    assert "No sources" in " ".join(plan.notes)


def test_plan_fits_within_budget():
    neg = BudgetNegotiator(TokenBudget(max_total=3000, reserve_output=300, reserve_overhead=200))
    plan = neg.plan("electric cars", _sources(3, words=10))
    assert plan.num_sources == 3
    assert plan.within_budget
    assert plan.estimated_total <= plan.max_total
    assert set(plan.included_indices) == {0, 1, 2}


def test_plan_drops_sources_when_budget_tiny():
    # Very small budget should force dropping some sources.
    neg = BudgetNegotiator(TokenBudget(max_total=300, reserve_output=100, reserve_overhead=100))
    plan = neg.plan("topic", _sources(8, words=50))
    assert plan.num_sources < 8
    assert plan.dropped_indices


def test_plan_respects_max_sources_cap():
    neg = BudgetNegotiator(TokenBudget(max_total=10000))
    plan = neg.plan("topic", _sources(10, words=5), max_sources=3)
    assert plan.num_sources <= 3


def test_plan_preserves_perspective_diversity_when_dropping():
    # One source per several categories + extra mainstream ones. Under pressure
    # the negotiator should drop the over-represented mainstream first.
    sources = (
        _sources(4, cat=SourceCategory.MAINSTREAM, words=60)
        + _sources(1, cat=SourceCategory.ACADEMIC, words=60)
        + _sources(1, cat=SourceCategory.SOCIAL, words=60)
    )
    # fix unique urls
    for i, s in enumerate(sources):
        s.url = f"https://u{i}.com/x"
        s.domain = f"u{i}.com"
    neg = BudgetNegotiator(TokenBudget(max_total=500, reserve_output=100, reserve_overhead=100))
    plan = neg.plan("topic", sources)
    kept_cats = {sources[i].category for i in plan.included_indices}
    # academic & social (the rare perspectives) should survive if anything does.
    assert SourceCategory.ACADEMIC in kept_cats
    assert SourceCategory.SOCIAL in kept_cats


def test_plan_single_giant_source_still_returns_plan():
    neg = BudgetNegotiator(TokenBudget(max_total=200, reserve_output=50, reserve_overhead=50))
    plan = neg.plan("topic", _sources(1, words=500))
    assert plan.num_sources == 1  # never drops the last one
    assert isinstance(plan, BudgetPlan)


def test_negotiation_message_mentions_sources_and_budget():
    neg = BudgetNegotiator(TokenBudget(max_total=3000))
    plan = neg.plan("electric cars", _sources(3, words=10))
    msg = plan.negotiation_message()
    assert "token" in msg.lower()
    assert str(plan.max_total) in msg


def test_plan_to_dict_keys():
    neg = BudgetNegotiator(TokenBudget(max_total=3000))
    plan = neg.plan("x", _sources(2, words=5))
    d = plan.to_dict()
    for key in ("query", "num_sources", "per_source_tokens", "estimated_total",
                "within_budget", "included_indices", "categories", "notes"):
        assert key in d


# ---------------------------------------------------------------------------
# cost-pattern learning
# ---------------------------------------------------------------------------

@pytest.fixture
def learner():
    pl = PreferenceLearner(":memory:", evidence_saturation=2)
    yield pl
    pl.close()


def test_record_usage_noop_without_learner():
    neg = BudgetNegotiator(TokenBudget())
    # should not raise
    neg.record_usage("topic", 1234)


def test_record_and_suggest_budget(learner):
    neg = BudgetNegotiator(TokenBudget(max_total=3000), preference_learner=learner)
    # Repeatedly record a "large" spend for the topic.
    for _ in range(4):
        neg.record_usage("vaccines", 4000)  # -> "large" bucket
    suggested = neg.suggest_budget("vaccines")
    # large bucket maps to a representative budget (4500 in the table).
    assert suggested >= 3000


def test_suggest_budget_default_when_unknown(learner):
    neg = BudgetNegotiator(TokenBudget(max_total=2222), preference_learner=learner)
    assert neg.suggest_budget("never-seen-topic") == 2222
    assert neg.suggest_budget("never-seen", default=999) == 999


def test_record_usage_stored_under_cost_category(learner):
    neg = BudgetNegotiator(TokenBudget(), preference_learner=learner)
    neg.record_usage("solar", 500)  # "small"
    prefs = learner.get_preferences(COST_LEARNING_CATEGORY, "solar")
    assert prefs
    assert prefs[0].value == "small"


def test_suggest_budget_noop_without_learner():
    neg = BudgetNegotiator(TokenBudget(max_total=1500))
    assert neg.suggest_budget("anything") == 1500

"""Tests for the research orchestrator (M6.4).

Offline end-to-end: search via StaticBackend, synthesis via FakeLLM or the
extractive fallback, privacy routing via a fake guard, and cost recording via
an in-memory PreferenceLearner.
"""

from __future__ import annotations

import pytest

from mimosa.llm.base_provider import ChatResponse, LLMError
from mimosa.memory.preference_learner import PreferenceLearner
from mimosa.research.research_engine import ResearchEngine, ResearchReport
from mimosa.research.search import SearchClient, StaticBackend
from mimosa.research.sources import SourceCategory
from mimosa.research.token_budget import BudgetNegotiator, TokenBudget


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------

class FakeLLM:
    name = "fake"
    is_local = False

    def __init__(self, content="Synthesized answer.", raise_error=False, is_local=False):
        self.content = content
        self.raise_error = raise_error
        self.is_local = is_local
        self.calls = []

    def chat(self, messages, *, temperature=0.7, max_tokens=None, **kwargs):
        self.calls.append(list(messages))
        if self.raise_error:
            raise LLMError("boom")
        return ChatResponse(content=self.content, model="fake", provider=self.name)

    def health_check(self):
        return True


class FakeGuard:
    """Minimal privacy guard double: flags queries containing 'private'."""

    def __init__(self, local_provider=None):
        self.local_provider = local_provider
        self.routed = []

    def is_private(self, text, *, allow_llm=True):
        return "private" in (text or "").lower()

    def create_provider_for(self, text, *, allow_llm=True, **options):
        self.routed.append(text)
        return self.local_provider, True


_RESULTS = [
    {"title": "BBC report", "url": "https://bbc.co.uk/a", "snippet": "mainstream view on the topic"},
    {"title": "Reddit thread", "url": "https://reddit.com/b", "snippet": "social discussion of the topic"},
    {"title": "ArXiv study", "url": "https://arxiv.org/c", "snippet": "academic analysis of the topic"},
    {"title": "Brookings", "url": "https://brookings.edu/d", "snippet": "think tank policy view"},
]


def _online_engine(llm=None, **kw):
    client = SearchClient(backend=StaticBackend(_RESULTS))
    return ResearchEngine(client, llm_provider=llm, **kw)


# ---------------------------------------------------------------------------
# end-to-end
# ---------------------------------------------------------------------------

def test_offline_engine_returns_no_sources():
    engine = ResearchEngine()  # default offline (no backend)
    report = engine.research("electric cars")
    assert isinstance(report, ResearchReport)
    assert report.sources == []
    assert report.metadata["online"] is False


def test_online_engine_extractive_path():
    engine = _online_engine(llm=None)
    report = engine.research("the topic")
    assert report.sources
    assert report.synthesis.used_llm is False
    assert report.synthesis.balanced is True
    assert report.metadata["online"] is True


def test_online_engine_llm_path():
    llm = FakeLLM(content="A balanced synthesis.")
    engine = _online_engine(llm=llm)
    report = engine.research("the topic")
    assert report.synthesis.used_llm is True
    assert report.answer == "A balanced synthesis."


def test_max_sources_cap_applied():
    engine = _online_engine(llm=None, max_sources=2)
    report = engine.research("the topic")
    assert len(report.sources) <= 2


def test_report_speakable_with_budget_note():
    engine = _online_engine(llm=None)
    report = engine.research("the topic")
    plain = report.speakable()
    with_budget = report.speakable(include_budget=True)
    assert len(with_budget) > len(plain)
    assert "token" in with_budget.lower()


def test_report_to_dict():
    engine = _online_engine(llm=None)
    d = engine.research("the topic").to_dict()
    for k in ("query", "synthesis", "plan", "sources", "is_private", "perspectives"):
        assert k in d


# ---------------------------------------------------------------------------
# privacy routing
# ---------------------------------------------------------------------------

def test_public_query_uses_default_provider():
    llm = FakeLLM(content="public answer")
    guard = FakeGuard()
    engine = _online_engine(llm=llm, privacy_guard=guard)
    report = engine.research("electric cars")
    assert report.is_private is False
    assert report.used_local is False
    assert guard.routed == []  # no local routing for public query


def test_private_query_routes_to_local_provider():
    local_llm = FakeLLM(content="local answer", is_local=True)
    guard = FakeGuard(local_provider=local_llm)
    cloud_llm = FakeLLM(content="cloud answer")
    engine = _online_engine(llm=cloud_llm, privacy_guard=guard)
    report = engine.research("my private medical condition topic")
    assert report.is_private is True
    assert report.used_local is True
    # local provider produced the answer, not the cloud one
    assert report.answer == "local answer"
    assert cloud_llm.calls == []


def test_private_query_routing_failure_falls_back_to_extractive():
    class _BoomGuard(FakeGuard):
        def create_provider_for(self, text, *, allow_llm=True, **options):
            raise RuntimeError("routing failed")

    engine = _online_engine(llm=FakeLLM(), privacy_guard=_BoomGuard())
    report = engine.research("private topic here")
    assert report.is_private is True
    # fell back to extractive (no provider) rather than leaking to cloud
    assert report.synthesis.used_llm is False


# ---------------------------------------------------------------------------
# cost-pattern learning
# ---------------------------------------------------------------------------

def test_engine_records_cost_pattern():
    pl = PreferenceLearner(":memory:", evidence_saturation=2)
    try:
        neg = BudgetNegotiator(TokenBudget(max_total=3000), preference_learner=pl)
        engine = _online_engine(llm=None, negotiator=neg)
        engine.research("renewable energy policy", topic="energy")
        prefs = pl.get_preferences("research_cost", "energy")
        assert prefs  # a cost bucket was recorded
    finally:
        pl.close()


def test_engine_never_raises_on_empty_query():
    engine = _online_engine(llm=None)
    report = engine.research("")
    assert isinstance(report, ResearchReport)

"""Tests for web search & source aggregation (M6.1).

Hermetic: no real network. The DuckDuckGo backend is exercised against a
static HTML fixture via a fake session, and the offline path is the default.
"""

from __future__ import annotations

import pytest

from mimosa.research.search import (
    DuckDuckGoBackend,
    SearchClient,
    SearchResult,
    StaticBackend,
)
from mimosa.research.sources import Source, SourceCategory


# ---------------------------------------------------------------------------
# StaticBackend
# ---------------------------------------------------------------------------

def test_static_backend_flat_list_of_dicts():
    backend = StaticBackend(
        [
            {"title": "A", "url": "https://bbc.co.uk/a", "snippet": "sa"},
            {"title": "B", "url": "https://reddit.com/b", "snippet": "sb"},
        ]
    )
    results = backend.search("anything")
    assert len(results) == 2
    assert results[0].title == "A"
    assert results[0].rank == 0
    assert results[1].rank == 1


def test_static_backend_tuples():
    backend = StaticBackend([("T", "https://x.com/a", "snip")])
    r = backend.search("q")[0]
    assert r.title == "T"
    assert r.url == "https://x.com/a"
    assert r.snippet == "snip"


def test_static_backend_accepts_sources_and_results():
    backend = StaticBackend(
        [
            Source(title="S", url="https://bbc.co.uk/x", snippet="z"),
            SearchResult(title="R", url="https://reddit.com/y", snippet="w"),
        ]
    )
    out = backend.search("q")
    assert {r.title for r in out} == {"S", "R"}


def test_static_backend_by_query():
    backend = StaticBackend(
        by_query={
            "cats": [{"title": "Cats", "url": "https://x.com/cats"}],
            "dogs": [{"title": "Dogs", "url": "https://x.com/dogs"}],
        }
    )
    assert backend.search("Cats")[0].title == "Cats"
    assert backend.search("dogs")[0].title == "Dogs"
    assert backend.search("unknown") == []


def test_static_backend_max_results():
    backend = StaticBackend([{"title": str(i), "url": f"https://x{i}.com"} for i in range(10)])
    assert len(backend.search("q", max_results=3)) == 3


# ---------------------------------------------------------------------------
# SearchClient
# ---------------------------------------------------------------------------

def test_client_offline_returns_empty():
    client = SearchClient(backend=None)
    assert client.online is False
    assert client.search("anything") == []


def test_client_enriches_to_sources():
    backend = StaticBackend(
        [
            {"title": "A", "url": "https://bbc.co.uk/a"},
            {"title": "B", "url": "https://arxiv.org/b"},
        ]
    )
    client = SearchClient(backend=backend)
    assert client.online is True
    sources = client.search("q")
    assert all(isinstance(s, Source) for s in sources)
    cats = {s.category for s in sources}
    assert SourceCategory.MAINSTREAM in cats
    assert SourceCategory.ACADEMIC in cats


def test_client_empty_query_returns_empty():
    client = SearchClient(backend=StaticBackend([{"title": "A", "url": "https://x.com"}]))
    assert client.search("   ") == []


def test_client_dedupes_by_url():
    backend = StaticBackend(
        [
            {"title": "A", "url": "https://bbc.co.uk/a"},
            {"title": "A dup", "url": "https://bbc.co.uk/a/"},  # trailing slash
            {"title": "B", "url": "https://bbc.co.uk/b"},
        ]
    )
    sources = SearchClient(backend=backend).search("q")
    urls = [s.url for s in sources]
    assert len(urls) == 2


def test_client_caps_per_category():
    backend = StaticBackend(
        [{"title": f"m{i}", "url": f"https://bbc.co.uk/{i}"} for i in range(5)]
        + [{"title": "soc", "url": "https://reddit.com/x"}]
    )
    client = SearchClient(backend=backend, per_category_cap=2)
    sources = client.search("q")
    mains = [s for s in sources if s.category == SourceCategory.MAINSTREAM]
    assert len(mains) == 2
    assert any(s.category == SourceCategory.SOCIAL for s in sources)


def test_client_reranks_sequentially():
    backend = StaticBackend([{"title": f"{i}", "url": f"https://x{i}.com"} for i in range(3)])
    sources = SearchClient(backend=backend).search("q")
    assert [s.rank for s in sources] == [0, 1, 2]


class _RaisingBackend(StaticBackend):
    def search(self, query, *, max_results=10):
        raise RuntimeError("boom")


def test_client_swallows_backend_errors():
    client = SearchClient(backend=_RaisingBackend([]))
    assert client.search("q") == []


# ---------------------------------------------------------------------------
# DuckDuckGoBackend (no real network)
# ---------------------------------------------------------------------------

_FIXTURE_HTML = """
<html><body>
<div class="result">
  <a class="result__a" href="https://www.bbc.co.uk/news/world-1">First Result</a>
  <a class="result__snippet">A snippet about the first result.</a>
</div>
<div class="result">
  <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Freddit.com%2Fr%2Fnews">Second</a>
  <a class="result__snippet">Reddit discussion.</a>
</div>
</body></html>
"""


class _FakeResponse:
    def __init__(self, text, status=200):
        self.text = text
        self.status_code = status


class _FakeSession:
    def __init__(self, response):
        self._response = response
        self.calls = []

    def post(self, url, data=None, headers=None, timeout=None):
        self.calls.append((url, data))
        return self._response


def test_ddg_parses_fixture_html():
    session = _FakeSession(_FakeResponse(_FIXTURE_HTML))
    backend = DuckDuckGoBackend(session=session)
    results = backend.search("news")
    assert len(results) == 2
    assert results[0].title == "First Result"
    assert results[0].url == "https://www.bbc.co.uk/news/world-1"
    # second URL went through the DDG redirect unwrap
    assert results[1].url == "https://reddit.com/r/news"
    assert session.calls  # the query was actually posted


def test_ddg_empty_query():
    backend = DuckDuckGoBackend(session=_FakeSession(_FakeResponse(_FIXTURE_HTML)))
    assert backend.search("   ") == []


def test_ddg_http_error_returns_empty():
    backend = DuckDuckGoBackend(session=_FakeSession(_FakeResponse("", status=503)))
    assert backend.search("q") == []


def test_ddg_request_exception_returns_empty():
    class _BoomSession:
        def post(self, *a, **k):
            raise OSError("network down")

    backend = DuckDuckGoBackend(session=_BoomSession())
    assert backend.search("q") == []


def test_ddg_is_network_flag():
    assert DuckDuckGoBackend().is_network is True
    assert StaticBackend([]).is_network is False


def test_ddg_max_results_truncates():
    session = _FakeSession(_FakeResponse(_FIXTURE_HTML))
    backend = DuckDuckGoBackend(session=session)
    assert len(backend.search("q", max_results=1)) == 1


def test_client_with_ddg_backend_end_to_end():
    session = _FakeSession(_FakeResponse(_FIXTURE_HTML))
    client = SearchClient(backend=DuckDuckGoBackend(session=session))
    sources = client.search("news")
    assert len(sources) == 2
    assert sources[0].category == SourceCategory.MAINSTREAM
    assert sources[1].category == SourceCategory.SOCIAL

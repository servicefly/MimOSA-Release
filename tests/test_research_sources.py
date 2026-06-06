"""Tests for research source classification & perspective labeling (M6.1).

Fully offline and deterministic -- no network, no LLM. Exercises the curated
domain table, suffix heuristics, Source enrichment, and the perspective
summary (including gap reporting).
"""

from __future__ import annotations

import pytest

from mimosa.research.sources import (
    PERSPECTIVE_LABELS,
    Source,
    SourceCategory,
    classify_domain,
    classify_url,
    perspective_label,
    summarize_perspectives,
)


# ---------------------------------------------------------------------------
# classify_domain / classify_url
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "domain,expected",
    [
        ("bbc.co.uk", SourceCategory.MAINSTREAM),
        ("www.bbc.co.uk", SourceCategory.MAINSTREAM),
        ("reuters.com", SourceCategory.MAINSTREAM),
        ("nytimes.com", SourceCategory.MAINSTREAM),
        ("substack.com", SourceCategory.ALTERNATIVE),
        ("medium.com", SourceCategory.ALTERNATIVE),
        ("reddit.com", SourceCategory.SOCIAL),
        ("twitter.com", SourceCategory.SOCIAL),
        ("x.com", SourceCategory.SOCIAL),
        ("youtube.com", SourceCategory.VIDEO),
        ("youtu.be", SourceCategory.VIDEO),
        ("brookings.edu", SourceCategory.THINK_TANK),
        ("rand.org", SourceCategory.THINK_TANK),
        ("arxiv.org", SourceCategory.ACADEMIC),
        ("nature.com", SourceCategory.ACADEMIC),
        ("wikipedia.org", SourceCategory.REFERENCE),
        ("en.wikipedia.org", SourceCategory.REFERENCE),
    ],
)
def test_classify_domain_table(domain, expected):
    assert classify_domain(domain) == expected


def test_think_tank_overrides_edu_suffix():
    # brookings.edu is explicitly a think tank even though .edu -> academic.
    assert classify_domain("brookings.edu") == SourceCategory.THINK_TANK


@pytest.mark.parametrize(
    "domain,expected",
    [
        ("whitehouse.gov", SourceCategory.OFFICIAL),
        ("nasa.gov", SourceCategory.OFFICIAL),
        ("parliament.gov.uk", SourceCategory.OFFICIAL),
        ("army.mil", SourceCategory.OFFICIAL),
        ("who.int", SourceCategory.OFFICIAL),
        ("mit.edu", SourceCategory.ACADEMIC),
        ("ox.ac.uk", SourceCategory.ACADEMIC),
    ],
)
def test_classify_domain_suffix_rules(domain, expected):
    assert classify_domain(domain) == expected


def test_classify_unknown_is_other():
    assert classify_domain("some-random-blog-xyz.example") == SourceCategory.OTHER


def test_classify_empty_is_other():
    assert classify_domain("") == SourceCategory.OTHER
    assert classify_domain(None) == SourceCategory.OTHER  # type: ignore[arg-type]


def test_classify_url_strips_scheme_path_port():
    assert classify_url("https://www.bbc.co.uk/news/world-123") == SourceCategory.MAINSTREAM
    assert classify_url("http://reddit.com:443/r/news") == SourceCategory.SOCIAL
    assert classify_url("https://user@nytimes.com/article") == SourceCategory.MAINSTREAM


def test_subdomain_matches_registered_domain():
    assert classify_domain("news.bbc.co.uk") == SourceCategory.MAINSTREAM
    assert classify_domain("blog.medium.com") == SourceCategory.ALTERNATIVE


# ---------------------------------------------------------------------------
# perspective_label / category label
# ---------------------------------------------------------------------------

def test_perspective_label_known():
    assert perspective_label(SourceCategory.MAINSTREAM) == "Mainstream media"
    assert perspective_label(SourceCategory.ACADEMIC) == "Academic & research"


def test_every_category_has_a_label():
    for cat in SourceCategory:
        assert cat in PERSPECTIVE_LABELS
        assert PERSPECTIVE_LABELS[cat]
        assert cat.label  # property


# ---------------------------------------------------------------------------
# Source enrichment
# ---------------------------------------------------------------------------

def test_source_autoclassifies_category_and_domain():
    s = Source(title="Climate report", url="https://www.bbc.co.uk/news/x")
    assert s.category == SourceCategory.MAINSTREAM
    assert s.domain == "www.bbc.co.uk"
    assert s.perspective == "Mainstream media"


def test_source_explicit_category_respected():
    s = Source(title="t", url="https://bbc.co.uk", category=SourceCategory.OTHER)
    assert s.category == SourceCategory.OTHER


def test_source_text_combines_title_and_snippet():
    s = Source(title="Title", snippet="Snippet")
    assert "Title" in s.text and "Snippet" in s.text
    s2 = Source(title="OnlyTitle")
    assert s2.text == "OnlyTitle"


def test_source_to_dict_roundtrip_fields():
    s = Source(title="t", url="https://reddit.com/r/x", snippet="sn", rank=3)
    d = s.to_dict()
    assert d["category"] == "social"
    assert d["url"] == "https://reddit.com/r/x"
    assert d["rank"] == 3
    assert d["domain"] == "reddit.com"


# ---------------------------------------------------------------------------
# summarize_perspectives
# ---------------------------------------------------------------------------

def _mk(url):
    return Source(title="t", url=url)


def test_summarize_counts_and_diversity():
    sources = [
        _mk("https://bbc.co.uk/a"),
        _mk("https://reuters.com/b"),  # also mainstream
        _mk("https://reddit.com/c"),
        _mk("https://arxiv.org/d"),
    ]
    summary = summarize_perspectives(sources)
    assert summary["total"] == 4
    assert summary["counts"]["mainstream"] == 2
    assert summary["counts"]["social"] == 1
    assert summary["counts"]["academic"] == 1
    assert summary["diversity"] == 3
    assert "mainstream" in summary["present"]


def test_summarize_reports_missing_perspectives():
    # Only mainstream present -> alternative/social/think_tank/academic/official missing.
    summary = summarize_perspectives([_mk("https://bbc.co.uk/a")])
    assert "alternative" in summary["missing"]
    assert "academic" in summary["missing"]
    assert "official" in summary["missing"]
    assert "mainstream" not in summary["missing"]


def test_summarize_custom_desired_set():
    summary = summarize_perspectives(
        [_mk("https://bbc.co.uk/a")],
        desired=[SourceCategory.VIDEO, SourceCategory.MAINSTREAM],
    )
    assert summary["missing"] == ["video"]


def test_summarize_empty():
    summary = summarize_perspectives([])
    assert summary["total"] == 0
    assert summary["diversity"] == 0
    assert summary["counts"] == {}
    # all default-desired are missing
    assert "mainstream" in summary["missing"]

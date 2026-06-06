"""Tests for the research skill (M6.4).

Offline. The skill is exercised with injected engines (online via
StaticBackend, and the default offline engine) so no network is touched.
"""

from __future__ import annotations

import pytest

from mimosa.research.research_engine import ResearchEngine
from mimosa.research.search import SearchClient, StaticBackend
from mimosa.skills.base_skill import SkillResult
from mimosa.skills.research_skill import ResearchSkill, extract_topic


# ---------------------------------------------------------------------------
# extract_topic
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "text,expected",
    [
        ("research electric cars", "electric cars"),
        ("research on climate change", "climate change"),
        ("look into the housing market", "the housing market"),
        ("investigate vaccine safety", "vaccine safety"),
        ("find out about quantum computing", "quantum computing"),
        ("do some research on tariffs", "tariffs"),
        ("please research nuclear energy", "nuclear energy"),
        ("can you look up renewable energy", "renewable energy"),
    ],
)
def test_extract_topic(text, expected):
    assert extract_topic(text).lower() == expected


def test_extract_topic_bare_passthrough():
    assert extract_topic("electric cars") == "electric cars"


def test_extract_topic_empty():
    assert extract_topic("") == ""


def test_extract_topic_strips_trailing_question_mark():
    assert extract_topic("research electric cars?") == "electric cars"


# ---------------------------------------------------------------------------
# handle -- online engine injected
# ---------------------------------------------------------------------------

_RESULTS = [
    {"title": "BBC report", "url": "https://bbc.co.uk/a", "snippet": "mainstream view"},
    {"title": "Reddit", "url": "https://reddit.com/b", "snippet": "social discussion"},
    {"title": "ArXiv", "url": "https://arxiv.org/c", "snippet": "academic analysis"},
]


def _online_engine():
    return ResearchEngine(SearchClient(backend=StaticBackend(_RESULTS)), llm_provider=None)


def test_handle_with_online_engine():
    skill = ResearchSkill(engine=_online_engine())
    result = skill.handle("research the topic")
    assert isinstance(result, SkillResult)
    assert result.success
    assert result.metadata["num_sources"] == 3
    assert result.metadata["balanced"] is True
    assert "mainstream" in result.metadata["perspectives_present"]
    assert result.metadata["citations"]


def test_handle_metadata_fields():
    skill = ResearchSkill(engine=_online_engine())
    result = skill.handle("research the topic")
    for key in ("topic", "num_sources", "perspectives_present", "perspectives_missing",
                "balanced", "used_llm", "is_private", "used_local", "estimated_tokens"):
        assert key in result.metadata


def test_handle_empty_topic():
    skill = ResearchSkill(engine=_online_engine())
    result = skill.handle("")
    # empty input -> prompt for a topic
    assert result.success is False
    assert "what would you like" in result.text.lower()
    assert result.metadata.get("reason") == "empty_topic"


# ---------------------------------------------------------------------------
# handle -- offline / no sources
# ---------------------------------------------------------------------------

def test_handle_offline_engine_explains():
    # default engine is offline (no backend) -> graceful "enable web search".
    skill = ResearchSkill(engine=ResearchEngine())
    result = skill.handle("research electric cars")
    assert result.success is False
    assert "web search" in result.text.lower()
    assert result.metadata["online"] is False


def test_handle_online_but_no_results():
    engine = ResearchEngine(SearchClient(backend=StaticBackend([])), llm_provider=None)
    skill = ResearchSkill(engine=engine)
    result = skill.handle("research something obscure")
    assert result.success is False
    assert "couldn't find sources" in result.text.lower()


# ---------------------------------------------------------------------------
# default lazy engine is offline (no surprise network)
# ---------------------------------------------------------------------------

def test_default_engine_is_offline():
    skill = ResearchSkill()
    assert skill.engine.search_client.online is False


def test_skill_identity():
    skill = ResearchSkill()
    assert skill.name == "research"
    assert "research" in skill.intents
    assert skill.uses_llm is True


def test_run_wraps_handle_safely():
    # BaseSkill.run should never raise even if the engine errors.
    class _BoomEngine:
        def research(self, *a, **k):
            raise RuntimeError("kaboom")

    skill = ResearchSkill(engine=_BoomEngine())
    result = skill.run("research the topic")
    assert isinstance(result, SkillResult)
    assert result.success is False

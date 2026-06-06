"""Tests for multi-source synthesis & balanced perspective labeling (M6.2).

Offline. The LLM path is exercised with a FakeLLM; the extractive fallback is
deterministic and needs no model.
"""

from __future__ import annotations

import pytest

from mimosa.llm.base_provider import ChatResponse, LLMError
from mimosa.research.sources import Source, SourceCategory
from mimosa.research.synthesizer import (
    PerspectiveGroup,
    ResearchSynthesizer,
    SYNTHESIS_SYSTEM_PROMPT,
    Synthesis,
)


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------

class FakeLLM:
    name = "fake"
    is_local = False

    def __init__(self, content="LLM synthesis.", raise_error=False):
        self.content = content
        self.raise_error = raise_error
        self.calls = []

    def chat(self, messages, *, temperature=0.7, max_tokens=None, **kwargs):
        self.calls.append(list(messages))
        if self.raise_error:
            raise LLMError("boom")
        return ChatResponse(content=self.content, model="fake", provider=self.name)

    def health_check(self):
        return True


def _sources():
    return [
        Source(title="Main story", url="https://bbc.co.uk/a", snippet="electric cars are growing"),
        Source(title="Reddit thread", url="https://reddit.com/b", snippet="people debate electric cars"),
        Source(title="Study", url="https://arxiv.org/c", snippet="analysis of electric vehicle adoption"),
    ]


# ---------------------------------------------------------------------------
# grouping
# ---------------------------------------------------------------------------

def test_group_by_perspective():
    groups = ResearchSynthesizer.group_by_perspective(_sources())
    cats = [g.category for g in groups]
    assert SourceCategory.MAINSTREAM in cats
    assert SourceCategory.SOCIAL in cats
    assert SourceCategory.ACADEMIC in cats
    assert all(isinstance(g, PerspectiveGroup) for g in groups)
    assert sum(g.count for g in groups) == 3


def test_group_preserves_first_seen_order():
    groups = ResearchSynthesizer.group_by_perspective(_sources())
    assert groups[0].category == SourceCategory.MAINSTREAM


# ---------------------------------------------------------------------------
# extractive fallback (no LLM)
# ---------------------------------------------------------------------------

def test_extractive_synthesis_no_llm():
    synth = ResearchSynthesizer(llm_provider=None)
    result = synth.synthesize("electric cars", _sources())
    assert isinstance(result, Synthesis)
    assert result.used_llm is False
    assert result.balanced is True  # 3 perspectives
    assert "Mainstream media" in result.answer
    assert "Social media & forums" in result.answer
    assert len(result.citations) == 3


def test_extractive_is_deterministic():
    synth = ResearchSynthesizer(llm_provider=None)
    a = synth.synthesize("electric cars", _sources()).answer
    b = synth.synthesize("electric cars", _sources()).answer
    assert a == b


def test_extractive_notes_missing_perspectives():
    # Only mainstream -> the answer should call out missing perspectives.
    sources = [Source(title="t", url="https://bbc.co.uk/a", snippet="x")]
    result = ResearchSynthesizer(None).synthesize("topic", sources)
    assert result.balanced is False
    assert result.perspectives_missing
    assert "not represented" in result.answer.lower()


def test_no_sources_returns_graceful_message():
    result = ResearchSynthesizer(None).synthesize("topic", [])
    assert "couldn't find any sources" in result.answer.lower()
    assert result.used_llm is False
    assert result.citations == []
    assert result.metadata.get("reason") == "no_sources"


# ---------------------------------------------------------------------------
# LLM path
# ---------------------------------------------------------------------------

def test_llm_path_used_when_provider_present():
    llm = FakeLLM(content="A balanced synthesis of the topic.")
    result = ResearchSynthesizer(llm).synthesize("electric cars", _sources())
    assert result.used_llm is True
    assert result.answer == "A balanced synthesis of the topic."
    # system prompt was sent
    sent = llm.calls[0]
    assert any(SYNTHESIS_SYSTEM_PROMPT in getattr(m, "content", "") for m in sent)


def test_llm_failure_falls_back_to_extractive():
    llm = FakeLLM(raise_error=True)
    result = ResearchSynthesizer(llm).synthesize("electric cars", _sources())
    assert result.used_llm is False
    # extractive answer contains perspective headings
    assert "Mainstream media" in result.answer


def test_llm_empty_response_falls_back():
    llm = FakeLLM(content="   ")
    result = ResearchSynthesizer(llm).synthesize("electric cars", _sources())
    assert result.used_llm is False


def test_llm_prompt_includes_perspective_headings():
    llm = FakeLLM()
    ResearchSynthesizer(llm).synthesize("electric cars", _sources())
    user_msg = llm.calls[0][-1].content
    assert "## Mainstream media" in user_msg
    assert "## Social media & forums" in user_msg
    assert "Question: electric cars" in user_msg


def test_synthesis_to_dict():
    result = ResearchSynthesizer(None).synthesize("topic", _sources())
    d = result.to_dict()
    for k in ("query", "answer", "perspectives_present", "perspectives_missing",
              "citations", "used_llm", "balanced", "metadata"):
        assert k in d


def test_per_source_token_truncation_applied():
    # a long snippet should be truncated in the extractive answer
    long_snip = "word " * 200
    sources = [Source(title="t", url="https://bbc.co.uk/a", snippet=long_snip)]
    result = ResearchSynthesizer(None).synthesize("topic", sources, per_source_tokens=10)
    # 10 tokens ~ 40 chars; answer should be far shorter than the raw snippet
    assert len(result.answer) < len(long_snip)

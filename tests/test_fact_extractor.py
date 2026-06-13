"""Tests for onboarding fact extraction (M3).

The LLM is always stubbed; the heuristic fallback runs with no LLM at all.
"""

from __future__ import annotations

from mimosa.llm.base_provider import ChatResponse
from mimosa.onboarding.fact_extractor import FactExtractor
from mimosa.onboarding.question_bank import get_topic


class StubLLM:
    """Minimal LLM stub returning a fixed ``content`` string."""

    def __init__(self, content):
        self._content = content
        self.calls = 0

    def chat(self, messages, **kwargs):
        self.calls += 1
        return ChatResponse(content=self._content, model="stub", provider="stub")


# -- LLM path ------------------------------------------------------------

def test_llm_json_extraction():
    content = (
        '[{"field":"name","value":"Alex"},'
        '{"field":"skills","value":"python"},'
        '{"field":"tools","key":"editor","value":"vim"}]'
    )
    fe = FactExtractor(llm=StubLLM(content))
    facts = fe.extract("hi", None, None)
    fields = {(f["field"], f.get("key"), f["value"]) for f in facts}
    assert ("name", None, "Alex") in fields
    assert ("skills", None, "python") in fields
    assert ("tools", "editor", "vim") in fields


def test_llm_json_with_code_fence():
    content = '```json\n[{"field":"occupation","value":"designer"}]\n```'
    fe = FactExtractor(llm=StubLLM(content))
    facts = fe.extract("hi", None, None)
    assert facts == [{"field": "occupation", "value": "designer"}]


def test_llm_json_with_prose_around():
    content = 'Sure! Here you go: [{"field":"name","value":"Sam"}] hope that helps'
    fe = FactExtractor(llm=StubLLM(content))
    facts = fe.extract("hi", None, None)
    assert {"field": "name", "value": "Sam"} in facts


def test_llm_garbage_falls_back_to_heuristic():
    topic = get_topic("introduction")
    q = topic.questions[0]  # asks for the name
    fe = FactExtractor(llm=StubLLM("totally not json"))
    facts = fe.extract("Alex", topic, q)
    assert any(f["field"] == "name" and f["value"] == "Alex" for f in facts)


def test_llm_invalid_field_filtered():
    content = '[{"field":"weird","value":"x"},{"field":"name","value":"Al"}]'
    fe = FactExtractor(llm=StubLLM(content))
    facts = fe.extract("hi", None, None)
    assert all(f["field"] != "weird" for f in facts)


# -- heuristic path ------------------------------------------------------

def test_heuristic_name():
    fe = FactExtractor(llm=None)
    topic = get_topic("introduction")
    q = topic.questions[0]
    facts = fe.extract("My name is Alex", topic, q)
    assert any(f["field"] == "name" and f["value"] == "Alex" for f in facts)


def test_heuristic_occupation():
    fe = FactExtractor(llm=None)
    topic = get_topic("professional_life")
    q = topic.questions[0]
    facts = fe.extract("I work as a UX designer", topic, q)
    assert any(f["field"] == "occupation" for f in facts)


def test_heuristic_list_split():
    fe = FactExtractor(llm=None)
    topic = get_topic("professional_life")
    q = topic.questions[1]  # skills/tools
    facts = fe.extract("python, react and docker", topic, q)
    skills = [f["value"] for f in facts if f["field"] == "skills"]
    assert "python" in skills and "docker" in skills


def test_empty_response_no_facts():
    fe = FactExtractor(llm=None)
    assert fe.extract("", None, None) == []


def test_extract_never_raises_on_bad_llm():
    class Boom:
        def chat(self, *a, **k):
            raise RuntimeError("nope")

    fe = FactExtractor(llm=Boom())
    topic = get_topic("introduction")
    # Should fall back to heuristic without raising.
    facts = fe.extract("Alex", topic, topic.questions[0])
    assert isinstance(facts, list)

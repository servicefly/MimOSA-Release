"""Tests for the MimOSA intent router, skills, and conversation manager (M1.3).

These tests run **fully offline**: the LLM provider is always mocked (a
``FakeLLM``), and the only skill that would touch the network (weather) is
tested with ``requests`` monkeypatched. No API key, audio, or ML deps required.

Run with:  pytest -q tests/test_intent_router.py
"""

from __future__ import annotations

import pytest

from mimosa.core.conversation_manager import ConversationManager, Turn
from mimosa.core.intent_router import (
    INTENT_CALCULATOR,
    INTENT_GREETING,
    INTENT_QUESTION,
    INTENT_TIME,
    INTENT_WEATHER,
    IntentRouter,
)
from mimosa.llm.base_provider import ChatResponse, LLMError, Message, Role
from mimosa.skills.base_skill import BaseSkill, SkillResult
from mimosa.skills.calculator_skill import CalculatorSkill, safe_eval, CalculatorError
from mimosa.skills.greeting_skill import GreetingSkill
from mimosa.skills.question_skill import QuestionSkill
from mimosa.skills.time_skill import TimeSkill
from mimosa.skills.weather_skill import WeatherSkill, _extract_location


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------

class FakeLLM:
    """A mock LLM provider returning canned content (or raising)."""

    name = "fake"
    is_local = False

    def __init__(self, content="ok", raise_error=False):
        self.content = content
        self.raise_error = raise_error
        self.calls = []

    def chat(self, messages, *, temperature=0.7, max_tokens=None, **kwargs):
        self.calls.append(list(messages))
        if self.raise_error:
            raise LLMError("simulated failure")
        return ChatResponse(content=self.content, model="fake-model", provider=self.name)

    def health_check(self):
        return not self.raise_error


# ---------------------------------------------------------------------------
# Calculator skill (local, safety-critical)
# ---------------------------------------------------------------------------

class TestCalculatorSafety:
    def test_basic_arithmetic(self):
        assert safe_eval("25 * 17") == 425
        assert safe_eval("100 / 4") == 25
        assert safe_eval("2 ** 10") == 1024
        assert safe_eval("-5 + 3") == -2

    def test_rejects_code_injection(self):
        # The whole point: no arbitrary code execution.
        for malicious in [
            "__import__('os').system('ls')",
            "open('/etc/passwd').read()",
            "eval('2+2')",
            "x + 1",          # bare name
            "(1).__class__",  # attribute access
        ]:
            with pytest.raises(CalculatorError):
                safe_eval(malicious)

    def test_division_by_zero(self):
        with pytest.raises(CalculatorError):
            safe_eval("1 / 0")

    def test_empty_expression(self):
        with pytest.raises(CalculatorError):
            safe_eval("   ")

    def test_skill_natural_language(self):
        skill = CalculatorSkill()
        r = skill.run("what is 25 times 17?")
        assert r.success and "425" in r.text

        r2 = skill.run("calculate 100 divided by 4")
        assert r2.success and "25" in r2.text

    def test_skill_handles_garbage(self):
        skill = CalculatorSkill()
        r = skill.run("tell me a story")
        assert r.success is False


# ---------------------------------------------------------------------------
# Time skill (local)
# ---------------------------------------------------------------------------

class TestTimeSkill:
    def test_time_query(self):
        r = TimeSkill().run("what time is it?")
        assert r.success and ("AM" in r.text or "PM" in r.text)

    def test_date_query(self):
        r = TimeSkill().run("what's today's date?")
        assert r.success and "Today is" in r.text

    def test_day_query(self):
        r = TimeSkill().run("what day is it?")
        assert r.success and "Today is" in r.text

    def test_no_llm_used(self):
        assert TimeSkill().uses_llm is False


# ---------------------------------------------------------------------------
# Weather skill (network mocked)
# ---------------------------------------------------------------------------

class TestWeatherSkill:
    def test_extract_location(self):
        assert _extract_location("what's the weather in Tokyo") == "Tokyo"
        assert _extract_location("weather for New York") == "New York"
        assert _extract_location("what's the weather?") is None

    def test_weather_success(self, monkeypatch):
        class _Resp:
            status_code = 200
            def json(self):
                return {
                    "current_condition": [
                        {"temp_C": "20", "temp_F": "68", "FeelsLikeC": "19",
                         "weatherDesc": [{"value": "Sunny"}]}
                    ],
                    "nearest_area": [{"areaName": [{"value": "Tokyo"}]}],
                }

        import mimosa.skills.weather_skill as ws
        monkeypatch.setattr(ws.requests, "get", lambda *a, **k: _Resp())
        r = WeatherSkill().run("weather in Tokyo")
        assert r.success
        assert "Tokyo" in r.text and "20" in r.text and "sunny" in r.text.lower()

    def test_weather_network_failure(self, monkeypatch):
        import mimosa.skills.weather_skill as ws

        def _boom(*a, **k):
            raise ws.requests.RequestException("no network")

        monkeypatch.setattr(ws.requests, "get", _boom)
        r = WeatherSkill().run("what's the weather?")
        assert r.success is False
        assert "couldn't get the weather" in r.text.lower()

    def test_weather_http_error(self, monkeypatch):
        class _Resp:
            status_code = 503
            text = "down"
            def json(self):
                return {}

        import mimosa.skills.weather_skill as ws
        monkeypatch.setattr(ws.requests, "get", lambda *a, **k: _Resp())
        r = WeatherSkill().run("weather in Mars")
        assert r.success is False


# ---------------------------------------------------------------------------
# LLM-backed skills
# ---------------------------------------------------------------------------

class TestQuestionSkill:
    def test_answer_via_llm(self):
        llm = FakeLLM(content="Paris is the capital of France.")
        r = QuestionSkill(llm_provider=llm).run("What is the capital of France?")
        assert r.success and "Paris" in r.text
        # System prompt should be present in the messages sent.
        sent = llm.calls[0]
        assert sent[0].role == Role.SYSTEM

    def test_no_llm_configured(self):
        r = QuestionSkill(llm_provider=None).run("Who was Einstein?")
        assert r.success is False
        assert "no language model" in r.text.lower()

    def test_llm_error_graceful(self):
        llm = FakeLLM(raise_error=True)
        r = QuestionSkill(llm_provider=llm).run("Why is the sky blue?")
        assert r.success is False
        assert "trouble" in r.text.lower()

    def test_context_included(self):
        llm = FakeLLM(content="answer")
        context = [Message(role=Role.USER, content="earlier"),
                   Message(role=Role.ASSISTANT, content="reply")]
        QuestionSkill(llm_provider=llm).run("follow up?", context=context)
        # The earlier context should be spliced before the new user message.
        contents = [m.content for m in llm.calls[0]]
        assert "earlier" in contents and "follow up?" in contents


class TestGreetingSkill:
    def test_greeting_via_llm(self):
        llm = FakeLLM(content="Hey there, great to see you!")
        r = GreetingSkill(llm_provider=llm).run("hello")
        assert r.success and "Hey there" in r.text
        assert r.metadata.get("source") == "llm"

    def test_greeting_fallback_without_llm(self):
        r = GreetingSkill(llm_provider=None).run("hi")
        assert r.success and r.text  # some friendly fallback
        assert r.metadata.get("source") == "fallback"

    def test_greeting_fallback_on_error(self):
        llm = FakeLLM(raise_error=True)
        r = GreetingSkill(llm_provider=llm).run("good morning")
        assert r.success and r.metadata.get("source") == "fallback"


# ---------------------------------------------------------------------------
# Intent classification
# ---------------------------------------------------------------------------

class TestClassification:
    def test_heuristic_time(self):
        router = IntentRouter(llm_provider=None)
        for q in ["what time is it?", "what's today's date?", "what day is it?"]:
            c = router.classify(q)
            assert c.intent == INTENT_TIME and c.source == "heuristic"

    def test_heuristic_calculator(self):
        router = IntentRouter(llm_provider=None)
        for q in ["what is 25 times 17?", "calculate 100 / 4", "3 + 4"]:
            assert router.classify(q).intent == INTENT_CALCULATOR

    def test_heuristic_weather(self):
        router = IntentRouter(llm_provider=None)
        assert router.classify("what's the weather?").intent == INTENT_WEATHER

    def test_heuristic_greeting(self):
        router = IntentRouter(llm_provider=None)
        for q in ["hello", "good morning", "how are you?"]:
            assert router.classify(q).intent == INTENT_GREETING

    def test_heuristic_question_shape(self):
        # Question-shaped utterances are caught locally (no LLM call needed).
        router = IntentRouter(llm_provider=None)
        c = router.classify("who was Albert Einstein?")
        assert c.intent == INTENT_QUESTION and c.source == "heuristic"

    def test_no_llm_calls_for_local_intents(self):
        llm = FakeLLM()
        router = IntentRouter(llm_provider=llm)
        for q in ["what time is it?", "5 * 5", "hello", "what is the capital of France?"]:
            router.classify(q)
        # All of these are caught by heuristics -> the LLM is never asked to classify.
        assert llm.calls == []

    def test_llm_classification_for_ambiguous(self):
        llm = FakeLLM(content='{"intent": "weather", "confidence": 0.9}')
        router = IntentRouter(llm_provider=llm)
        # A statement that no heuristic matches -> falls to LLM tier.
        c = router.classify("I wonder about the sky conditions outside")
        assert c.source == "llm" and c.intent == INTENT_WEATHER

    def test_llm_classification_failure_falls_back(self):
        llm = FakeLLM(raise_error=True)
        router = IntentRouter(llm_provider=llm)
        c = router.classify("mumble mumble random statement")
        # LLM failed -> unknown with low confidence (no crash).
        assert c.intent == "unknown"

    def test_parse_classification_tolerates_noise(self):
        router = IntentRouter(llm_provider=None)
        intent, conf = router._parse_classification('Sure! {"intent":"time","confidence":0.8}')
        assert intent == INTENT_TIME and conf == pytest.approx(0.8)


# ---------------------------------------------------------------------------
# Routing & dispatch
# ---------------------------------------------------------------------------

class TestRouting:
    def test_route_time_local(self):
        router = IntentRouter(llm_provider=None)
        r = router.route("what time is it?")
        assert r.metadata["intent"] == INTENT_TIME and r.success

    def test_route_calculator_local(self):
        router = IntentRouter(llm_provider=None)
        r = router.route("what is 9 times 9?")
        assert "81" in r.text and r.metadata["intent"] == INTENT_CALCULATOR

    def test_route_question_uses_llm(self):
        llm = FakeLLM(content="A neutron star is very dense.")
        router = IntentRouter(llm_provider=llm)
        r = router.route("what is a neutron star?")
        assert "neutron star" in r.text.lower()
        assert r.metadata["intent"] == INTENT_QUESTION

    def test_empty_input(self):
        router = IntentRouter(llm_provider=None)
        r = router.route("   ")
        assert r.success is False

    def test_low_confidence_falls_back_to_question(self):
        # Force a very low LLM confidence -> router should fall back to question.
        llm = FakeLLM(content='{"intent": "weather", "confidence": 0.1}')
        router = IntentRouter(llm_provider=llm, confidence_threshold=0.7)
        r = router.route("some really ambiguous statement here")
        assert r.metadata["intent"] == INTENT_QUESTION

    def test_custom_skill_registration(self):
        class _CustomSkill(BaseSkill):
            name = "custom"
            intents = ["custom"]
            def handle(self, text, context=None):
                return SkillResult(text="custom!", skill=self.name)

        router = IntentRouter(llm_provider=None, skills=[_CustomSkill()])
        assert "custom" in [s.name for s in router.skills]

    def test_confidence_threshold_from_arg(self):
        router = IntentRouter(llm_provider=None, confidence_threshold=0.9)
        assert router.confidence_threshold == 0.9


# ---------------------------------------------------------------------------
# Conversation manager
# ---------------------------------------------------------------------------

class TestConversationManager:
    def test_add_and_count(self):
        cm = ConversationManager(max_history=3)
        cm.add_turn("hi", "hello", intent="greeting")
        assert cm.turn_count == 1
        assert cm.last_intent() == "greeting"

    def test_history_bound(self):
        cm = ConversationManager(max_history=2)
        for i in range(5):
            cm.add_turn(f"u{i}", f"a{i}")
        assert cm.turn_count == 2
        # Oldest evicted; newest retained.
        assert cm.turns[-1].user_text == "u4"

    def test_context_messages(self):
        cm = ConversationManager()
        cm.add_turn("question one", "answer one")
        msgs = cm.get_context_messages()
        assert len(msgs) == 2
        assert msgs[0].role == Role.USER and msgs[1].role == Role.ASSISTANT

    def test_context_max_messages(self):
        cm = ConversationManager()
        for i in range(5):
            cm.add_turn(f"u{i}", f"a{i}")
        msgs = cm.get_context_messages(max_messages=2)
        assert len(msgs) == 2

    def test_update_last_response(self):
        cm = ConversationManager()
        cm.add_turn("hello")
        cm.update_last_response("hi there", intent="greeting")
        assert cm.turns[-1].assistant_text == "hi there"
        assert cm.last_intent() == "greeting"

    def test_reset_session(self):
        cm = ConversationManager()
        old = cm.session_id
        cm.add_turn("x", "y")
        new = cm.reset_session()
        assert new != old and cm.turn_count == 0

    def test_memory_records_shape(self):
        cm = ConversationManager()
        cm.add_turn("u", "a", intent="time")
        records = cm.to_memory_records()
        assert records[0]["user_text"] == "u"
        assert records[0]["intent"] == "time"
        assert "session_id" in records[0] and "timestamp" in records[0]



# ---------------------------------------------------------------------------
# Research intent classification & routing (M6)
# ---------------------------------------------------------------------------

class TestResearchIntent:
    @pytest.mark.parametrize(
        "text",
        [
            "research electric cars",
            "do some research on tariffs",
            "look into the housing market",
            "investigate vaccine safety",
            "dig into the climate debate",
            "find out about quantum computing",
            "what are people saying about remote work?",
            "give me a balanced overview of the minimum wage debate",
            "what are the different perspectives on nuclear energy",
            "search the web for AI regulation news",
        ],
    )
    def test_research_queries_classified_as_research(self, text):
        from mimosa.core.intent_router import INTENT_RESEARCH

        router = IntentRouter()
        assert router.classify(text).intent == INTENT_RESEARCH

    @pytest.mark.parametrize(
        "text,intent",
        [
            ("what time is it?", INTENT_TIME),
            ("what is 2 + 2?", INTENT_CALCULATOR),
            ("what is the capital of France?", INTENT_QUESTION),
            ("what's the weather in Paris?", INTENT_WEATHER),
        ],
    )
    def test_non_research_questions_unaffected(self, text, intent):
        router = IntentRouter()
        assert router.classify(text).intent == intent

    def test_research_skill_registered_by_default(self):
        from mimosa.core.intent_router import INTENT_RESEARCH

        router = IntentRouter()
        assert INTENT_RESEARCH in router._by_intent

    def test_route_to_research_skill_offline(self):
        # Default research engine is offline -> graceful message, success False.
        router = IntentRouter()
        result = router.route("research electric cars")
        assert result.skill == "research"
        assert "web search" in result.text.lower()

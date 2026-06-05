"""Tests for user-defined custom skills (M4.1).

These run **fully offline**: the LLM provider is a ``FakeLLM`` double, and no
network/audio/GTK is required. They cover the spec dataclass (validation,
serialisation, usability), the :class:`CustomSkill` matching/handling, the
``build_custom_skills`` factory, router integration, and the config-level
custom-skill management helpers.
"""

from __future__ import annotations

import pytest

from mimosa.core.intent_router import IntentRouter
from mimosa.llm.base_provider import ChatResponse, LLMError, Message, Role
from mimosa.skills.base_skill import SkillResult
from mimosa.skills.custom_skill import (
    CUSTOM_INTENT_PREFIX,
    CustomSkill,
    CustomSkillError,
    CustomSkillSpec,
    build_custom_skills,
    normalize_custom_spec,
    slugify,
)
from mimosa.utils.config import AppConfig, SkillsSettings


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------

class FakeLLM:
    name = "fake"
    is_local = False

    def __init__(self, content="generated reply", raise_error=False):
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
# slugify
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("Open Notes", "open_notes"),
    ("  Hello   World!! ", "hello_world"),
    ("already_a_slug", "already_a_slug"),
    ("Café-Bar 2", "caf_bar_2"),
    ("", ""),
    ("***", ""),
])
def test_slugify(raw, expected):
    assert slugify(raw) == expected


# ---------------------------------------------------------------------------
# CustomSkillSpec
# ---------------------------------------------------------------------------

def test_spec_derives_id_from_name():
    spec = CustomSkillSpec(name="Tell A Joke", triggers=["joke"], response="ha").validate()
    assert spec.id == "tell_a_joke"
    assert spec.intent_label == f"{CUSTOM_INTENT_PREFIX}tell_a_joke"


def test_spec_dedups_and_strips_triggers():
    spec = CustomSkillSpec(
        name="X", triggers=[" hi ", "HI", "hello", "hello", ""], response="r"
    ).validate()
    assert spec.triggers == ["hi", "hello"]


def test_spec_string_trigger_coerced_to_list():
    spec = CustomSkillSpec(name="X", triggers="solo", response="r").validate()
    assert spec.triggers == ["solo"]


def test_spec_invalid_match_mode_and_response_type_reset():
    spec = CustomSkillSpec(
        name="X", triggers=["t"], response="r",
        match_mode="bogus", response_type="exec",
    ).validate()
    assert spec.match_mode == "any"
    assert spec.response_type == "text"


def test_spec_priority_coercion():
    spec = CustomSkillSpec(name="X", triggers=["t"], response="r", priority="oops").validate()
    assert spec.priority == 100


def test_spec_is_usable_text():
    assert CustomSkillSpec(name="X", triggers=["t"], response="hi").validate().is_usable()
    assert not CustomSkillSpec(name="X", triggers=["t"], response="  ").validate().is_usable()
    assert not CustomSkillSpec(name="X", triggers=[], response="hi").validate().is_usable()


def test_spec_is_usable_llm():
    spec = CustomSkillSpec(
        name="X", triggers=["t"], response_type="llm", llm_prompt="answer {input}"
    ).validate()
    assert spec.is_usable()
    # llm with neither prompt nor fallback text is not usable
    spec2 = CustomSkillSpec(name="X", triggers=["t"], response_type="llm").validate()
    assert not spec2.is_usable()


def test_spec_roundtrip_dict():
    spec = CustomSkillSpec(name="Greeter", triggers=["yo"], response="hey").validate()
    data = spec.to_dict()
    rebuilt = CustomSkillSpec.from_dict(data)
    assert rebuilt.to_dict() == data


def test_from_dict_ignores_unknown_keys():
    spec = CustomSkillSpec.from_dict(
        {"name": "X", "triggers": ["t"], "response": "r", "bogus": 1, "exec": "rm -rf"}
    )
    assert not hasattr(spec, "bogus")
    assert spec.is_usable()


# ---------------------------------------------------------------------------
# normalize_custom_spec (raising validator)
# ---------------------------------------------------------------------------

def test_normalize_requires_name():
    with pytest.raises(CustomSkillError):
        normalize_custom_spec({"triggers": ["t"], "response": "r"})


def test_normalize_requires_trigger():
    with pytest.raises(CustomSkillError):
        normalize_custom_spec({"name": "X", "response": "r"})


def test_normalize_requires_response():
    with pytest.raises(CustomSkillError):
        normalize_custom_spec({"name": "X", "triggers": ["t"]})


def test_normalize_rejects_bad_regex():
    with pytest.raises(CustomSkillError):
        normalize_custom_spec(
            {"name": "X", "triggers": ["("], "response": "r", "match_mode": "regex"}
        )


def test_normalize_accepts_valid_spec():
    spec = normalize_custom_spec({"name": "Wave", "triggers": ["wave"], "response": "hi"})
    assert isinstance(spec, CustomSkillSpec)
    assert spec.id == "wave"


# ---------------------------------------------------------------------------
# CustomSkill matching
# ---------------------------------------------------------------------------

def _skill(**kw):
    base = dict(name="T", triggers=["coffee"], response="Brewing!")
    base.update(kw)
    return CustomSkill(CustomSkillSpec(**base))


def test_match_any_substring():
    s = _skill(triggers=["coffee", "espresso"], match_mode="any")
    assert s.matches("make me a coffee please")
    assert s.matches("ESPRESSO now")
    assert not s.matches("tea time")


def test_match_all():
    s = _skill(triggers=["turn", "lights"], match_mode="all")
    assert s.matches("turn off the lights")
    assert not s.matches("turn off the music")


def test_match_exact():
    s = _skill(triggers=["status report"], match_mode="exact")
    assert s.matches("Status Report")
    assert not s.matches("give me a status report")


def test_match_regex():
    s = _skill(triggers=[r"^order \d+ coffees?$"], match_mode="regex")
    assert s.matches("order 3 coffees")
    assert s.matches("ORDER 1 coffee")
    assert not s.matches("order some coffee")


def test_match_disabled_skill_never_matches():
    s = _skill(enabled=False)
    assert not s.matches("coffee")


def test_match_empty_text():
    s = _skill()
    assert not s.matches("")
    assert not s.matches("   ")


# ---------------------------------------------------------------------------
# CustomSkill handling
# ---------------------------------------------------------------------------

def test_handle_text_response():
    s = _skill(response="Here you go.")
    res = s.run("coffee")
    assert isinstance(res, SkillResult)
    assert res.success
    assert res.text == "Here you go."
    assert res.metadata["source"] == "custom_text"
    assert res.skill == s.name


def test_handle_llm_response_uses_provider():
    llm = FakeLLM(content="A great joke!")
    s = CustomSkill(
        CustomSkillSpec(name="Joke", triggers=["joke"], response_type="llm",
                        llm_prompt="Tell a joke about {input}", response="fallback"),
        llm_provider=llm,
    )
    res = s.run("cats")
    assert res.text == "A great joke!"
    assert res.metadata["source"] == "custom_llm"
    # prompt template substitution happened in the system message
    sys_msg = llm.calls[0][0]
    assert sys_msg.role == Role.SYSTEM
    assert "cats" in sys_msg.content


def test_handle_llm_falls_back_to_text_without_provider():
    s = CustomSkill(
        CustomSkillSpec(name="Joke", triggers=["joke"], response_type="llm",
                        llm_prompt="Tell a joke", response="offline joke"),
        llm_provider=None,
    )
    res = s.run("joke")
    assert res.text == "offline joke"
    assert res.metadata["source"] == "custom_text"


def test_handle_llm_error_falls_back():
    llm = FakeLLM(raise_error=True)
    s = CustomSkill(
        CustomSkillSpec(name="Joke", triggers=["joke"], response_type="llm",
                        llm_prompt="Tell a joke", response="safe fallback"),
        llm_provider=llm,
    )
    res = s.run("joke")
    assert res.text == "safe fallback"
    assert res.metadata["source"] == "fallback"


# ---------------------------------------------------------------------------
# build_custom_skills
# ---------------------------------------------------------------------------

def test_build_skips_disabled_and_unusable():
    specs = [
        {"name": "Good", "triggers": ["g"], "response": "ok"},
        {"name": "Off", "triggers": ["o"], "response": "x", "enabled": False},
        {"name": "Empty", "triggers": [], "response": ""},
    ]
    built = build_custom_skills(specs)
    assert [b.spec.id for b in built] == ["good"]


def test_build_dedups_by_id():
    specs = [
        {"id": "dup", "name": "A", "triggers": ["a"], "response": "1"},
        {"id": "dup", "name": "B", "triggers": ["b"], "response": "2"},
    ]
    built = build_custom_skills(specs)
    assert len(built) == 1


def test_build_sorts_by_priority():
    specs = [
        {"name": "Late", "triggers": ["l"], "response": "x", "priority": 50},
        {"name": "Early", "triggers": ["e"], "response": "y", "priority": 10},
    ]
    built = build_custom_skills(specs)
    assert [b.spec.id for b in built] == ["early", "late"]


def test_build_accepts_spec_objects_and_injects_llm():
    llm = FakeLLM()
    spec = CustomSkillSpec(name="X", triggers=["t"], response_type="llm",
                           llm_prompt="p", response="f")
    built = build_custom_skills([spec], llm_provider=llm)
    assert built[0].llm is llm


# ---------------------------------------------------------------------------
# Router integration
# ---------------------------------------------------------------------------

def test_router_routes_to_custom_skill():
    custom = build_custom_skills([
        {"name": "Coffee", "triggers": ["brew coffee"], "response": "Brewing now!"}
    ])
    router = IntentRouter(llm_provider=None, custom_skills=custom)
    res = router.route("please brew coffee")
    assert res.text == "Brewing now!"
    assert res.metadata["intent"] == "custom:coffee"
    assert res.metadata["classification_source"] == "custom"


def test_router_builtin_takes_priority_over_custom():
    # A custom skill triggering on "time" must not override the built-in time skill.
    custom = build_custom_skills([
        {"name": "FakeTime", "triggers": ["time"], "response": "nope"}
    ])
    router = IntentRouter(llm_provider=None, custom_skills=custom)
    res = router.route("what time is it")
    assert res.metadata["intent"] == "time"
    assert res.text != "nope"


def test_router_custom_before_question_fallback():
    custom = build_custom_skills([
        {"name": "Mood", "triggers": ["mood"], "response": "Feeling great."}
    ])
    router = IntentRouter(llm_provider=None, custom_skills=custom)
    res = router.route("what is your mood")  # question-shaped, but custom wins
    assert res.text == "Feeling great."


def test_router_custom_skills_listed():
    custom = build_custom_skills([
        {"name": "A", "triggers": ["a"], "response": "x"},
        {"name": "B", "triggers": ["b"], "response": "y"},
    ])
    router = IntentRouter(llm_provider=None, custom_skills=custom)
    assert {s.spec.id for s in router.custom_skills} == {"a", "b"}


def test_router_set_custom_skills_replaces_cleanly():
    router = IntentRouter(llm_provider=None, custom_skills=build_custom_skills([
        {"name": "Old", "triggers": ["oldtrigger"], "response": "old"}
    ]))
    assert router.route("oldtrigger").text == "old"

    router.set_custom_skills(build_custom_skills([
        {"name": "New", "triggers": ["newtrigger"], "response": "new"}
    ]))
    # Old custom skill is gone from skills list and intent index.
    assert all(s.name != "custom_old" for s in router.skills)
    assert router.route("newtrigger").text == "new"
    # Old trigger now falls through (no custom match) -> not "old".
    assert router.route("oldtrigger").text != "old"


def test_router_set_custom_skills_empty_clears():
    router = IntentRouter(llm_provider=None, custom_skills=build_custom_skills([
        {"name": "Z", "triggers": ["zzz"], "response": "z"}
    ]))
    router.set_custom_skills([])
    assert router.custom_skills == []


# ---------------------------------------------------------------------------
# Config integration (SkillsSettings)
# ---------------------------------------------------------------------------

def test_skills_settings_validate_drops_unusable_custom():
    s = SkillsSettings(custom=[
        {"name": "Good", "triggers": ["g"], "response": "ok"},
        {"name": "Bad", "triggers": [], "response": ""},
        "not a dict",
    ]).validate()
    ids = [c["id"] for c in s.custom]
    assert ids == ["good"]


def test_skills_settings_dedups_custom_by_id():
    s = SkillsSettings(custom=[
        {"id": "dup", "name": "A", "triggers": ["a"], "response": "1"},
        {"id": "dup", "name": "B", "triggers": ["b"], "response": "2"},
    ]).validate()
    assert len(s.custom) == 1


def test_skills_settings_add_custom_skill():
    s = SkillsSettings()
    stored = s.add_custom_skill({"name": "Wave", "triggers": ["wave"], "response": "hi"})
    assert stored["id"] == "wave"
    assert any(c["id"] == "wave" for c in s.custom)


def test_skills_settings_add_custom_skill_replaces_by_id():
    s = SkillsSettings()
    s.add_custom_skill({"name": "Wave", "triggers": ["wave"], "response": "hi"})
    s.add_custom_skill({"name": "Wave", "triggers": ["wave", "hello"], "response": "hey"})
    matches = [c for c in s.custom if c["id"] == "wave"]
    assert len(matches) == 1
    assert matches[0]["response"] == "hey"


def test_skills_settings_add_custom_skill_rejects_invalid():
    s = SkillsSettings()
    with pytest.raises(CustomSkillError):
        s.add_custom_skill({"name": "X", "triggers": []})


def test_skills_settings_remove_custom_skill():
    s = SkillsSettings()
    s.add_custom_skill({"name": "Wave", "triggers": ["wave"], "response": "hi"})
    assert s.remove_custom_skill("wave") is True
    assert s.remove_custom_skill("wave") is False


def test_skills_settings_custom_specs_returns_objects():
    s = SkillsSettings()
    s.add_custom_skill({"name": "Wave", "triggers": ["wave"], "response": "hi"})
    specs = s.custom_specs()
    assert len(specs) == 1
    assert isinstance(specs[0], CustomSkillSpec)
    assert specs[0].id == "wave"


def test_appconfig_roundtrip_preserves_custom_skills():
    cfg = AppConfig()
    cfg.skills.add_custom_skill({"name": "Wave", "triggers": ["wave"], "response": "hi"})
    data = cfg.to_dict()
    rebuilt = AppConfig.from_dict(data)
    assert any(c["id"] == "wave" for c in rebuilt.skills.custom)

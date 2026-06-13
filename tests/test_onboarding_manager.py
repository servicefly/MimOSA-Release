"""Tests for the onboarding manager: pause/resume, completion (M3). Hermetic."""

from __future__ import annotations

import os

import pytest

from mimosa.memory.profile_manager import ProfileManager
from mimosa.memory.vector_store import MemoryVectorStore
from mimosa.onboarding.onboarding_manager import OnboardingManager


@pytest.fixture()
def store():
    s = MemoryVectorStore(None, use_chroma=False)
    yield s
    s.close()


def _manager(tmp_path, store, **kw):
    pm = ProfileManager(path=str(tmp_path / "profile.json"), vector_store=store)
    return OnboardingManager(
        llm=None,
        profile_manager=pm,
        vector_store=store,
        state_path=str(tmp_path / "onboarding_state.json"),
        **kw,
    )


def test_begin_returns_prompt(tmp_path, store):
    mgr = _manager(tmp_path, store)
    prompt = mgr.begin()
    assert prompt.text


def test_submit_saves_state(tmp_path, store):
    mgr = _manager(tmp_path, store)
    mgr.begin()
    mgr.submit("My name is Alex")
    assert mgr.has_saved_state()
    assert os.path.exists(mgr.state_path)


def test_pause_and_resume(tmp_path, store):
    mgr = _manager(tmp_path, store)
    mgr.begin()
    mgr.submit("My name is Alex")
    mgr.submit("I am a writer")
    saved_index = mgr.conversation.topic_index
    state_path = mgr.state_path

    # Fresh manager pointing at the same state file should resume.
    pm2 = ProfileManager(path=str(tmp_path / "profile.json"), vector_store=store)
    mgr2 = OnboardingManager(
        llm=None,
        profile_manager=pm2,
        vector_store=store,
        state_path=state_path,
    )
    assert mgr2.has_saved_state()
    mgr2.begin()
    assert mgr2.profile_manager.profile.name == "Alex"
    assert mgr2.conversation.topic_index == saved_index
    assert len(mgr2.conversation.transcript) == 2


def test_complete_clears_state_and_saves_profile(tmp_path, store):
    mgr = _manager(tmp_path, store)
    mgr.begin()
    mgr.submit("My name is Alex")
    while not mgr.is_complete:
        mgr.skip_topic()
    assert mgr.is_complete
    assert not mgr.has_saved_state()
    assert os.path.exists(mgr.profile_manager.path)
    summary = mgr.summary()
    assert "message" in summary
    assert summary["fact_count"] >= 1


def test_should_run_default(tmp_path, store):
    mgr = _manager(tmp_path, store)
    # No config manager -> should run when not flagged complete.
    assert mgr.should_run() is True


def test_apply_profile_edits(tmp_path, store):
    mgr = _manager(tmp_path, store)
    mgr.apply_profile_edits({"name": "Sam", "skills": ["go"]})
    assert mgr.profile_manager.profile.name == "Sam"
    assert "go" in mgr.profile_manager.profile.skills


class _FakePersonality:
    def __init__(self, complete=False, pref="later"):
        self.onboarding_complete = complete
        self.onboarding_preference = pref


class _FakeConfig:
    def __init__(self, personality):
        self.personality = personality


class _FakeConfigManager:
    def __init__(self, personality):
        self._cfg = _FakeConfig(personality)
        self.updated = {}

    def get(self):
        return self._cfg

    def update_section(self, section, persist=True, **changes):
        self.updated[section] = changes
        for k, v in changes.items():
            setattr(self._cfg.personality, k, v)


def test_should_run_honours_config(tmp_path, store):
    cm = _FakeConfigManager(_FakePersonality(complete=True))
    mgr = _manager(tmp_path, store, config_manager=cm)
    assert mgr.should_run() is False


def test_should_run_skip_pref(tmp_path, store):
    cm = _FakeConfigManager(_FakePersonality(complete=False, pref="skip"))
    mgr = _manager(tmp_path, store, config_manager=cm)
    assert mgr.should_run() is False


def test_complete_marks_config(tmp_path, store):
    cm = _FakeConfigManager(_FakePersonality())
    mgr = _manager(tmp_path, store, config_manager=cm)
    mgr.begin()
    while not mgr.is_complete:
        mgr.skip_topic()
    assert cm.updated.get("personality", {}).get("onboarding_complete") is True

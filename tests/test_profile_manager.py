"""Tests for the user profile + ProfileManager (M3). Hermetic."""

from __future__ import annotations

import pytest

from mimosa.memory.profile_manager import ProfileManager, UserProfile
from mimosa.memory.vector_store import MemoryVectorStore


@pytest.fixture()
def vector_store():
    s = MemoryVectorStore(None, use_chroma=False)
    yield s
    s.close()


# -- UserProfile ---------------------------------------------------------

def test_empty_profile():
    p = UserProfile()
    assert p.is_empty()
    assert p.known_fact_count() == 0


def test_from_dict_normalises_and_dedups():
    p = UserProfile.from_dict(
        {"user_profile": {"name": "Alex", "skills": ["py", "py", "go"]}}
    )
    assert p.name == "Alex"
    assert p.skills == ["py", "go"]
    assert not p.is_empty()


def test_to_dict_round_trip():
    data = {
        "user_profile": {
            "name": "Sam",
            "occupation": "designer",
            "skills": ["figma"],
            "tools": {"editor": "vim"},
            "interests": ["art"],
            "goals": ["learn rust"],
        }
    }
    p = UserProfile.from_dict(data)
    again = UserProfile.from_dict(p.to_dict())
    assert again.name == "Sam"
    assert again.tools == {"editor": "vim"}
    assert again.goals == ["learn rust"]


def test_prompt_summary_includes_facts():
    p = UserProfile.from_dict(
        {"user_profile": {"name": "Alex", "interests": ["hiking"]}}
    )
    summary = p.to_prompt_summary()
    assert "Alex" in summary
    assert "hiking" in summary


def test_display_items():
    p = UserProfile.from_dict({"user_profile": {"name": "Alex", "skills": ["py"]}})
    items = p.display_items()
    labels = [label for label, _ in items]
    assert "Name" in labels
    assert "Skills" in labels


# -- ProfileManager ------------------------------------------------------

def test_set_scalar_and_list(tmp_path, vector_store):
    pm = ProfileManager(path=str(tmp_path / "profile.json"), vector_store=vector_store)
    pm.set_scalar("name", "Sam")
    pm.add_to_list("skills", ["go", "go", "rust"])
    assert pm.profile.name == "Sam"
    assert pm.profile.skills == ["go", "rust"]


def test_set_dict_value(tmp_path, vector_store):
    pm = ProfileManager(path=str(tmp_path / "profile.json"), vector_store=vector_store)
    pm.set_dict_value("tools", "shell", "bash")
    assert pm.profile.tools["shell"] == "bash"


def test_update_from_facts(tmp_path, vector_store):
    pm = ProfileManager(path=str(tmp_path / "profile.json"), vector_store=vector_store)
    applied = pm.update_from_facts(
        [
            {"field": "occupation", "value": "designer"},
            {"field": "interests", "value": "photography"},
            {"field": "tools", "key": "os", "value": "linux"},
            {"field": "bogus"},  # ignored
        ]
    )
    assert applied == 3
    assert pm.profile.occupation == "designer"
    assert "photography" in pm.profile.interests
    assert pm.profile.tools["os"] == "linux"


def test_update_from_facts_never_raises(tmp_path):
    pm = ProfileManager(path=str(tmp_path / "p.json"))
    assert pm.update_from_facts(None) == 0
    assert pm.update_from_facts(["not a dict"]) == 0


def test_save_and_reload(tmp_path, vector_store):
    path = str(tmp_path / "profile.json")
    pm = ProfileManager(path=path, vector_store=vector_store)
    pm.set_scalar("name", "Sam")
    pm.add_to_list("skills", ["rust"])
    pm.save()
    pm2 = ProfileManager(path=path)
    assert pm2.profile.name == "Sam"
    assert pm2.profile.skills == ["rust"]


def test_clear(tmp_path, vector_store):
    pm = ProfileManager(path=str(tmp_path / "p.json"), vector_store=vector_store)
    pm.set_scalar("name", "Sam")
    pm.clear()
    assert pm.profile.is_empty()


def test_vector_mirroring(tmp_path, vector_store):
    pm = ProfileManager(path=str(tmp_path / "p.json"), vector_store=vector_store)
    pm.set_scalar("name", "Alex")
    # The fact should have been mirrored into the user_profile collection.
    from mimosa.memory.vector_store import COLLECTION_USER_PROFILE

    assert vector_store.count(COLLECTION_USER_PROFILE) >= 1

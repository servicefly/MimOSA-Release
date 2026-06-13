"""Headless tests for the onboarding & profile UI shells (M3).

On the CI/dev machine GTK is unavailable, so the dialogs degrade to ``None``
after invoking their ``on_close`` callbacks. These tests verify that graceful
path plus the pure helper functions in the profile viewer.
"""

from __future__ import annotations

from mimosa.ui.onboarding_dialog import open_onboarding_dialog
from mimosa.ui.profile_viewer import (
    editable_to_profile,
    open_profile_viewer,
    profile_to_editable,
)


def test_open_onboarding_dialog_headless_calls_close():
    captured = []
    result = open_onboarding_dialog(object(), on_close=lambda c: captured.append(c))
    # Headless -> returns None and invokes on_close(False).
    assert result is None
    assert captured == [False]


def test_open_onboarding_dialog_headless_no_callback():
    # Should not raise even without a callback.
    assert open_onboarding_dialog(object()) is None


def test_open_profile_viewer_headless_calls_close():
    captured = []
    result = open_profile_viewer({}, on_close=lambda: captured.append(True))
    assert result is None
    assert captured == [True]


def test_profile_to_editable_round_trip():
    profile = {
        "user_profile": {
            "name": "Alex",
            "occupation": "engineer",
            "skills": ["python", "go"],
            "interests": ["hiking"],
            "goals": [],
            "tools": {"editor": "vim"},
            "preferences": {},
            "schedule": {},
            "relationships": {},
        }
    }
    editable = profile_to_editable(profile)
    assert editable["skills"] == "python, go"
    assert editable["tools"] == "editor: vim"

    back = editable_to_profile(editable)["user_profile"]
    assert back["name"] == "Alex"
    assert back["skills"] == ["python", "go"]
    assert back["tools"] == {"editor": "vim"}


def test_editable_to_profile_handles_blank_fields():
    editable = {
        "name": "",
        "occupation": "",
        "skills": "",
        "interests": "",
        "goals": "",
        "tools": "",
        "preferences": "",
        "schedule": "",
        "relationships": "",
    }
    prof = editable_to_profile(editable)["user_profile"]
    assert prof["skills"] == []
    assert prof["tools"] == {}

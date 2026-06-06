"""Tests for the first-run setup wizard controller (M4.2).

Hermetic: uses a temp config file via ``MIMOSA_CONFIG`` so nothing touches the
user's real settings; no GTK/network/audio required.
"""

from __future__ import annotations

import pytest

from mimosa.utils.config import AppConfig, AppConfigManager
from mimosa.ui.setup_wizard import (
    STEP_FINISH,
    STEP_PERSONALIZE,
    STEP_PRIVACY,
    STEP_SYSTEM,
    STEP_VOICE,
    STEP_WELCOME,
    SetupWizardController,
    WizardStep,
    build_wizard_steps,
)


@pytest.fixture()
def manager(tmp_path, monkeypatch):
    cfg_path = tmp_path / "settings.json"
    monkeypatch.setenv("MIMOSA_CONFIG", str(cfg_path))
    mgr = AppConfigManager(path=cfg_path)
    mgr.load()
    return mgr


# ---------------------------------------------------------------------------
# Step descriptors
# ---------------------------------------------------------------------------

def test_build_wizard_steps_order():
    steps = build_wizard_steps()
    assert [s.step_id for s in steps] == [
        STEP_WELCOME, STEP_PERSONALIZE, STEP_VOICE, STEP_PRIVACY,
        STEP_SYSTEM, STEP_FINISH
    ]
    assert all(isinstance(s, WizardStep) for s in steps)


def test_steps_have_titles_and_bodies():
    for s in build_wizard_steps():
        assert s.title
        assert s.body


# ---------------------------------------------------------------------------
# first-run detection
# ---------------------------------------------------------------------------

def test_should_run_true_on_fresh_config(manager):
    assert SetupWizardController.should_run(manager) is True
    assert manager.is_first_run() is True


def test_should_run_false_after_complete(manager):
    manager.mark_first_run_complete()
    assert SetupWizardController.should_run(manager) is False


# ---------------------------------------------------------------------------
# navigation
# ---------------------------------------------------------------------------

def test_navigation_forward_back(manager):
    w = SetupWizardController(manager)
    assert w.is_first
    assert w.current_step.step_id == STEP_WELCOME
    w.next()
    assert w.current_step.step_id == STEP_PERSONALIZE
    w.back()
    assert w.current_step.step_id == STEP_WELCOME


def test_navigation_clamps_at_ends(manager):
    w = SetupWizardController(manager)
    w.back()  # already first
    assert w.index == 0
    for _ in range(10):
        w.next()
    assert w.is_last
    assert w.current_step.step_id == STEP_FINISH


def test_goto(manager):
    w = SetupWizardController(manager)
    step = w.goto(STEP_PRIVACY)
    assert step.step_id == STEP_PRIVACY
    assert w.current_step.step_id == STEP_PRIVACY


def test_goto_unknown_raises(manager):
    w = SetupWizardController(manager)
    with pytest.raises(KeyError):
        w.goto("nope")


def test_progress(manager):
    w = SetupWizardController(manager)
    assert w.progress() == 0.0
    w.goto(STEP_FINISH)
    assert w.progress() == 1.0


# ---------------------------------------------------------------------------
# editing the working copy
# ---------------------------------------------------------------------------

def test_set_value_updates_working_copy(manager):
    w = SetupWizardController(manager)
    w.set_value("voice", "wake_word", "computer")
    assert w.get_value("voice", "wake_word") == "computer"


def test_set_value_validates_and_clamps(manager):
    w = SetupWizardController(manager)
    stored = w.set_value("voice", "wake_word_sensitivity", 9.9)
    assert stored <= 1.0  # clamped to MAX_WAKE_SENSITIVITY


def test_set_value_unknown_field_raises(manager):
    w = SetupWizardController(manager)
    with pytest.raises(KeyError):
        w.set_value("voice", "nope", 1)


def test_working_copy_isolated_from_manager(manager):
    w = SetupWizardController(manager)
    w.set_value("voice", "wake_word", "changed")
    # Manager unchanged until finish().
    assert manager.get().voice.wake_word != "changed"


# ---------------------------------------------------------------------------
# completion
# ---------------------------------------------------------------------------

def test_finish_commits_and_marks_complete(manager):
    w = SetupWizardController(manager)
    w.set_value("voice", "wake_word", "computer")
    w.set_value("privacy", "llm_provider", "none")
    cfg = w.finish()
    assert cfg.voice.wake_word == "computer"
    assert cfg.privacy.llm_provider == "none"
    assert cfg.first_run_complete is True
    assert w.finished
    assert manager.is_first_run() is False


def test_finish_persists_to_disk(manager):
    w = SetupWizardController(manager)
    w.set_value("voice", "wake_word", "jarvis")
    w.finish()
    # Re-load from disk via a fresh manager.
    fresh = AppConfigManager(path=manager.path)
    fresh.load()
    assert fresh.get().voice.wake_word == "jarvis"
    assert fresh.is_first_run() is False


def test_finish_idempotent(manager):
    w = SetupWizardController(manager)
    w.finish()
    w.finish()  # should not raise
    assert manager.is_first_run() is False


def test_cancel_discards_edits_but_marks_complete(manager):
    w = SetupWizardController(manager)
    w.set_value("voice", "wake_word", "discarded")
    w.cancel()
    assert manager.get().voice.wake_word != "discarded"
    assert manager.is_first_run() is False
    assert w.finished


def test_cancel_can_skip_marking_complete(manager):
    w = SetupWizardController(manager)
    w.cancel(mark_complete=False)
    assert manager.is_first_run() is True


def test_finish_without_persist(manager):
    w = SetupWizardController(manager, )
    w.set_value("voice", "wake_word", "ephemeral")
    w.finish(persist=False)
    assert manager.get().voice.wake_word == "ephemeral"
    # Not written to disk.
    fresh = AppConfigManager(path=manager.path)
    fresh.load()
    assert fresh.get().voice.wake_word != "ephemeral"



# ---------------------------------------------------------------------------
# "Get to Know MimOSA" personalisation step (M8.4a)
# ---------------------------------------------------------------------------

def test_personalize_step_present_and_has_fields():
    steps = {s.step_id: s for s in build_wizard_steps()}
    assert STEP_PERSONALIZE in steps
    step = steps[STEP_PERSONALIZE]
    assert step.title == "Get to Know MimOSA"
    names = {f.name for f in step.fields}
    assert {"user_name", "assistant_name", "verbosity", "greet_by_name"} <= names
    # Accessibility: every field carries a label and help text.
    for f in step.fields:
        assert f.label
        assert f.help


def test_personalize_values_persist_on_finish(manager):
    w = SetupWizardController(manager)
    w.goto(STEP_PERSONALIZE)
    w.set_value("personality", "user_name", "  Alex  ")
    w.set_value("personality", "assistant_name", "Ada")
    w.set_value("personality", "verbosity", "brief")
    w.finish()

    p = manager.get().personality
    assert p.user_name == "Alex"  # trimmed
    assert p.assistant_name == "Ada"
    assert p.verbosity == "brief"
    assert p.greeting() == "Hi Alex, I'm Ada."


def test_personalize_blank_name_uses_defaults(manager):
    w = SetupWizardController(manager)
    w.set_value("personality", "user_name", "")
    w.set_value("personality", "assistant_name", "")
    w.finish()
    p = manager.get().personality
    assert p.user_name == ""
    assert p.assistant_name == "MimOSA"  # default restored
    assert p.greeting() == "Hi, I'm MimOSA."


def test_personalize_invalid_verbosity_coerced(manager):
    w = SetupWizardController(manager)
    w.set_value("personality", "verbosity", "rambling")
    assert w.get_value("personality", "verbosity") == "balanced"

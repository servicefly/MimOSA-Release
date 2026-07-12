"""Integration: first-run setup wizard -> launch-ready config (item #11).

Verifies that walking the wizard controller end-to-end produces a persisted,
launch-ready configuration and that first-run state is recorded so the wizard
never reappears. Fully hermetic and headless.
"""

from __future__ import annotations

import pytest

from mimosa.utils.config import AppConfigManager
from mimosa.ui.setup_wizard import SetupWizardController


@pytest.fixture()
def manager(tmp_path, monkeypatch):
    cfg_path = tmp_path / "settings.json"
    monkeypatch.setenv("MIMOSA_CONFIG", str(cfg_path))
    mgr = AppConfigManager(path=cfg_path)
    mgr.load()
    return mgr


def test_should_run_then_finish_marks_complete(manager):
    assert SetupWizardController.should_run(manager) is True
    wizard = SetupWizardController(manager)
    wizard.set_selected_voice("en_US-amy-medium")
    wizard.select_avatar_preset("neutral")
    wizard.finish()

    # Reload from disk: first-run recorded and the wizard won't reappear.
    fresh = AppConfigManager(path=manager.path)
    cfg = fresh.load()
    assert cfg.first_run_complete is True
    assert SetupWizardController.should_run(fresh) is False


def test_wizard_choices_survive_reload(manager):
    wizard = SetupWizardController(manager)
    wizard.set_value("voice", "wake_word", "jarvis")
    wizard.set_selected_voice("en_US-ryan-medium")
    wizard.select_avatar_preset("masculine")
    wizard.finish()

    fresh = AppConfigManager(path=manager.path)
    cfg = fresh.load()
    assert cfg.voice.wake_word == "jarvis"
    assert cfg.avatar.enabled is True
    assert cfg.avatar.voice_id == "en_US-ryan-medium"


def test_cancel_still_marks_complete(manager):
    wizard = SetupWizardController(manager)
    wizard.cancel()
    fresh = AppConfigManager(path=manager.path)
    cfg = fresh.load()
    assert cfg.first_run_complete is True

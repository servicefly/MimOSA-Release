"""Integration: the avatar configuration + selection pipeline (item #11).

Covers the flow that ties together several v2.0.0 pieces:

* the capability detector's avatar-tier decision,
* new-install vs v1.x-upgrade avatar defaults in the config manager, and
* the setup-wizard controller's avatar-preset + voice pairing.

All hermetic and headless — no GTK, audio or GPU required.
"""

from __future__ import annotations

import json

import pytest

from mimosa.system.capability_detector import detect_avatar_tier
from mimosa.utils.config import AppConfigManager
from mimosa.ui.setup_wizard import SetupWizardController


@pytest.fixture()
def manager(tmp_path, monkeypatch):
    cfg_path = tmp_path / "settings.json"
    monkeypatch.setenv("MIMOSA_CONFIG", str(cfg_path))
    mgr = AppConfigManager(path=cfg_path)
    mgr.load()
    return mgr


def test_detect_avatar_tier_is_supported_value():
    tier = detect_avatar_tier()
    assert tier in ("2d", "circle_only")


def test_new_install_enables_avatar_with_detected_tier(tmp_path, monkeypatch):
    """A brand-new install should get the avatar enabled by default."""
    cfg_path = tmp_path / "settings.json"
    monkeypatch.setenv("MIMOSA_CONFIG", str(cfg_path))
    mgr = AppConfigManager(path=cfg_path)
    cfg = mgr.load()  # no file on disk => fresh install
    assert cfg.avatar.enabled is True
    assert cfg.avatar.tier in ("2d", "circle_only")


def test_v1x_upgrade_keeps_avatar_disabled(tmp_path, monkeypatch):
    """An existing v1.x config (no 'avatar' key) must keep the circle."""
    cfg_path = tmp_path / "settings.json"
    cfg_path.write_text(json.dumps({"version": 1, "first_run_complete": True}))
    monkeypatch.setenv("MIMOSA_CONFIG", str(cfg_path))
    mgr = AppConfigManager(path=cfg_path)
    cfg = mgr.load()
    assert cfg.avatar.enabled is False
    assert cfg.avatar.use_circle() is True


def test_wizard_preset_pairs_voice_and_persists(manager):
    """Selecting a preset enables the avatar, pairs a voice, and persists."""
    wizard = SetupWizardController(manager)
    wizard.select_avatar_preset("feminine")
    assert wizard.get_avatar_enabled() is True
    voice = wizard.get_selected_voice()
    assert voice is not None
    wizard.finish()

    fresh = AppConfigManager(path=manager.path)
    cfg = fresh.load()
    assert cfg.avatar.enabled is True
    assert cfg.avatar.voice_id == voice
    assert cfg.personality.gender == "female"


def test_wizard_circle_preset_disables_and_persists(manager):
    wizard = SetupWizardController(manager)
    wizard.select_avatar_preset("circle")
    wizard.finish()

    fresh = AppConfigManager(path=manager.path)
    cfg = fresh.load()
    assert cfg.avatar.enabled is False
    assert cfg.avatar.use_circle() is True

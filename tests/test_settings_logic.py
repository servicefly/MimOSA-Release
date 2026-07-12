"""Tests for the GTK-free settings controller (M3.3).

Exercises the page/field descriptors, working-copy edit/validate flow, dirty &
restart detection, skill enable/priority management, clear-history hook, and
apply/cancel commit semantics -- all without a display.
"""

import pytest

from mimosa.utils.config import AppConfigManager
from mimosa.ui.settings_logic import (
    PAGE_ABOUT,
    PAGE_PERSONALIZE,
    PAGE_PRIVACY,
    PAGE_RESEARCH,
    PAGE_SKILLS,
    PAGE_TASKS,
    PAGE_UI,
    PAGE_VOICE,
    SettingsController,
    build_page_specs,
)


@pytest.fixture
def manager(monkeypatch, tmp_path):
    monkeypatch.setenv("MIMOSA_CONFIG", str(tmp_path / "settings.json"))
    monkeypatch.setenv("MIMOSA_UI_CONFIG", str(tmp_path / "ui.json"))
    m = AppConfigManager()
    m.load()
    return m


@pytest.fixture
def controller(manager):
    return SettingsController(manager)


# -- page descriptors ---------------------------------------------------------


def test_page_specs_cover_all_required_pages():
    ids = [p.page_id for p in build_page_specs()]
    assert ids == [
        PAGE_VOICE, PAGE_PERSONALIZE, PAGE_SKILLS, "system", PAGE_PRIVACY,
        PAGE_TASKS, PAGE_RESEARCH, PAGE_UI, "avatar", "learning", PAGE_ABOUT,
    ]


def test_avatar_page_has_expected_fields():
    pages = {p.page_id: p for p in build_page_specs()}
    assert "avatar" in pages
    avatar = pages["avatar"]
    names = {(f.section, f.name) for f in avatar.fields}
    assert ("avatar", "enabled") in names
    assert ("avatar", "tier") in names
    assert ("avatar", "voice_id") in names
    # Every field carries a label + help for accessibility.
    for f in avatar.fields:
        assert f.label


def test_tasks_and_research_pages_have_toggles_and_help():
    pages = {p.page_id: p for p in build_page_specs()}
    tasks = pages[PAGE_TASKS]
    research = pages[PAGE_RESEARCH]
    task_fields = {f.name for f in tasks.fields}
    assert {"background_tasks_enabled", "resource_monitoring",
            "learn_error_fixes"} <= task_fields
    research_fields = {f.name for f in research.fields}
    assert "web_search_enabled" in research_fields
    # Accessibility: every field on these pages has a label and help text.
    for page in (tasks, research, pages[PAGE_PERSONALIZE]):
        for f in page.fields:
            assert f.label, f"{page.page_id}.{f.name} missing label"
            assert f.help, f"{page.page_id}.{f.name} missing help"


def test_all_fields_have_labels_for_accessibility():
    for page in build_page_specs():
        for f in page.fields:
            assert f.label, f"{page.page_id}.{f.name} has no accessible label"


def test_controller_exposes_pages(controller):
    assert controller.page(PAGE_VOICE) is not None
    assert controller.page("does-not-exist") is None
    voice_fields = {f.name for f in controller.page(PAGE_VOICE).fields}
    assert {"wake_word", "stt_model", "tts_speed"} <= voice_fields


# -- field editing ------------------------------------------------------------


def test_set_value_clamps_via_section_validate(controller):
    stored = controller.set_value("voice", "tts_speed", 99)
    assert stored == 2.0  # clamped to MAX_TTS_SPEED
    assert controller.get_value("voice", "tts_speed") == 2.0


def test_set_value_unknown_field_raises(controller):
    with pytest.raises(KeyError):
        controller.set_value("voice", "nope", 1)


def test_working_copy_is_isolated_until_apply(controller, manager):
    controller.set_value("privacy", "llm_provider", "none")
    # committed config unchanged until apply()
    assert manager.get().privacy.llm_provider == "abacus"
    assert controller.is_dirty() is True


# -- dirty / restart detection ------------------------------------------------


def test_changed_fields_and_restart_required(controller):
    controller.set_value("voice", "stt_model", "small")  # restart-sensitive
    changed = controller.changed_fields()
    assert ("voice", "stt_model") in changed
    assert controller.restart_required() is True


def test_non_restart_change_does_not_require_restart(controller):
    controller.set_value("ui", "opacity", 0.5)
    assert controller.is_dirty() is True
    assert controller.restart_required() is False


# -- skills -------------------------------------------------------------------


def test_skill_rows_in_priority_order(controller):
    rows = controller.skill_rows()
    assert rows[0].skill_id == "time"
    assert rows[0].priority == 0
    assert all(r.enabled for r in rows)


def test_set_skill_enabled_and_move(controller):
    controller.set_skill_enabled("weather", False)
    assert any(r.skill_id == "weather" and not r.enabled
               for r in controller.skill_rows())
    # move 'question' (last) up by 2
    assert controller.move_skill("question", -100) is True  # clamps to top
    assert controller.skill_rows()[0].skill_id == "question"
    # no-op move returns False
    assert controller.move_skill("question", -1) is False


def test_skills_provider_injection():
    class FakeSkill:
        def __init__(self, name, uses_llm=False):
            self.name = name
            self.uses_llm = uses_llm

    import os
    os.environ["MIMOSA_CONFIG"] = "/tmp/_sl_test.json"
    if os.path.exists("/tmp/_sl_test.json"):
        os.remove("/tmp/_sl_test.json")
    m = AppConfigManager()
    m.load()
    ctrl = SettingsController(
        m, skills_provider=lambda: [FakeSkill("custom_a"),
                                    FakeSkill("question", uses_llm=True)])
    ids = {r.skill_id for r in ctrl.skill_rows()}
    assert "custom_a" in ids
    assert any(r.skill_id == "question" and r.uses_llm for r in ctrl.skill_rows())


# -- clear history hook -------------------------------------------------------


def test_clear_history_invokes_hook(manager):
    calls = {"n": 0}

    def hook():
        calls["n"] += 1
        return 7

    ctrl = SettingsController(manager, on_clear_history=hook)
    assert ctrl.clear_history() == 7
    assert calls["n"] == 1


def test_clear_history_without_hook_returns_zero(controller):
    assert controller.clear_history() == 0


# -- apply / cancel -----------------------------------------------------------


def test_apply_commits_and_persists(controller, manager, tmp_path):
    controller.set_value("privacy", "llm_provider", "none")
    controller.set_skill_enabled("weather", False)
    assert controller.apply() is True
    assert controller.is_dirty() is False
    assert manager.get().privacy.llm_provider == "none"
    assert (tmp_path / "settings.json").exists()
    # reload from disk confirms persistence
    fresh = AppConfigManager()
    fresh.load()
    assert fresh.get().privacy.llm_provider == "none"
    assert fresh.get().skills.is_enabled("weather") is False


def test_cancel_discards_edits(controller, manager):
    controller.set_value("privacy", "llm_provider", "local")
    controller.cancel()
    assert controller.is_dirty() is False
    assert controller.get_value("privacy", "llm_provider") == "abacus"


def test_reset_defaults_on_working_copy(controller):
    controller.set_value("voice", "tts_speed", 1.8)
    controller.reset_defaults()
    assert controller.get_value("voice", "tts_speed") == 1.0


def test_reset_avatar_position(controller):
    controller.set_value("ui", "pos_x", 100)
    controller.reset_avatar_position()
    assert controller.get_value("ui", "pos_x") is None


def test_privacy_summary_reflects_working_copy(controller):
    controller.set_value("privacy", "llm_provider", "none")
    assert "skills-only" in controller.privacy_summary().lower()

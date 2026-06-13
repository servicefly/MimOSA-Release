"""Tests for LearningSettings config (M4)."""

from __future__ import annotations

from mimosa.utils.config import (
    AppConfig,
    LearningSettings,
    DEFAULT_QUESTION_FREQUENCY,
    QUESTION_FREQUENCY_LIMITS,
    VALID_QUESTION_FREQUENCIES,
)


def test_defaults():
    ls = LearningSettings()
    assert ls.allow_questions is True
    assert ls.question_frequency == DEFAULT_QUESTION_FREQUENCY
    assert ls.proactive_suggestions is True
    assert ls.learn_from_conversations is True


def test_daily_question_limit():
    ls = LearningSettings(question_frequency="rarely")
    assert ls.daily_question_limit() == QUESTION_FREQUENCY_LIMITS["rarely"]
    ls2 = LearningSettings(question_frequency="often")
    assert ls2.daily_question_limit() == QUESTION_FREQUENCY_LIMITS["often"]


def test_validate_normalises_bad_frequency():
    ls = LearningSettings(question_frequency="whenever")
    ls.validate()
    assert ls.question_frequency in VALID_QUESTION_FREQUENCIES


def test_appconfig_includes_learning():
    cfg = AppConfig()
    assert isinstance(cfg.learning, LearningSettings)


def test_appconfig_round_trip():
    cfg = AppConfig()
    cfg.learning.allow_questions = False
    cfg.learning.question_frequency = "often"
    data = cfg.to_dict()
    restored = AppConfig.from_dict(data)
    assert restored.learning.allow_questions is False
    assert restored.learning.question_frequency == "often"


def test_from_dict_backward_compat_missing_learning():
    # A v1.0.0 config dict has no "learning" key; should still load with defaults.
    cfg = AppConfig.from_dict({})
    assert isinstance(cfg.learning, LearningSettings)
    assert cfg.learning.allow_questions is True


def test_from_dict_ignores_unknown_keys():
    data = {"learning": {"allow_questions": False, "bogus_key": 123}}
    cfg = AppConfig.from_dict(data)
    assert cfg.learning.allow_questions is False



def test_learning_page_exists_in_settings():
    from mimosa.ui.settings_logic import build_page_specs, PAGE_LEARNING

    pages = {p.page_id: p for p in build_page_specs()}
    assert PAGE_LEARNING in pages
    fields = {f.name for f in pages[PAGE_LEARNING].fields}
    assert {
        "learn_from_conversations",
        "allow_questions",
        "question_frequency",
        "proactive_suggestions",
    } <= fields
    # Every field has help text (accessibility).
    for f in pages[PAGE_LEARNING].fields:
        assert f.help


def test_settings_controller_edits_learning_section():
    from mimosa.utils.config import AppConfigManager
    from mimosa.ui.settings_logic import SettingsController

    mgr = AppConfigManager(path=None) if _accepts_path() else AppConfigManager()
    controller = SettingsController(mgr)
    controller.set_value("learning", "allow_questions", False)
    assert controller.get_value("learning", "allow_questions") is False
    controller.set_value("learning", "question_frequency", "often")
    assert controller.get_value("learning", "question_frequency") == "often"


def _accepts_path():
    import inspect
    from mimosa.utils.config import AppConfigManager

    return "path" in inspect.signature(AppConfigManager.__init__).parameters

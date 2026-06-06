"""Tests for the unified application config (M3.3).

Covers the section dataclasses (defaults, validation/clamping), the
:class:`AppConfig` tree (serialize/round-trip, migration), and the thread-safe
:class:`AppConfigManager` (load/save, atomic persistence, observers, reset,
env-path override, ui.json mirroring, and concurrent access).

All tests are hermetic: they point ``MIMOSA_CONFIG`` / ``MIMOSA_UI_CONFIG`` at
``tmp_path`` so nothing touches the real ``~/.config``.
"""

import json
import threading

import pytest

from mimosa.utils.config import (
    AppConfig,
    AppConfigManager,
    CONFIG_VERSION,
    DEFAULT_LLM_PROVIDER,
    DEFAULT_SKILL_ORDER,
    PrivacySettings,
    ResearchSettings,
    SkillsSettings,
    SystemIntegrationSettings,
    VoiceSettings,
    default_config_path,
)


# -- path resolution ---------------------------------------------------------


def test_default_path_honors_env(monkeypatch, tmp_path):
    target = tmp_path / "custom.json"
    monkeypatch.setenv("MIMOSA_CONFIG", str(target))
    assert default_config_path() == target


def test_default_path_uses_xdg(monkeypatch, tmp_path):
    monkeypatch.delenv("MIMOSA_CONFIG", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert default_config_path() == tmp_path / "mimosa" / "settings.json"


# -- section validation -------------------------------------------------------


def test_voice_settings_clamps_and_defaults():
    v = VoiceSettings(wake_word_sensitivity=5.0, tts_speed=99, stt_model="bogus",
                      wake_word="   ")
    v.validate()
    assert v.wake_word_sensitivity == 1.0
    assert v.tts_speed == 2.0
    assert v.stt_model == "base"          # invalid -> default
    assert v.wake_word == "hey mimosa"    # blank -> default


def test_privacy_settings_validation_and_summary():
    p = PrivacySettings(llm_provider="nonsense", conversation_history_limit=-5)
    p.validate()
    assert p.llm_provider == DEFAULT_LLM_PROVIDER
    assert p.conversation_history_limit == 1   # clamped to min
    # provider 'none' is valid and reflected in the summary
    p2 = PrivacySettings(llm_provider="none").validate()
    assert "skills-only" in p2.privacy_summary().lower()
    assert "no telemetry" in p2.privacy_summary().lower()


def test_system_safe_mode_forces_confirmations():
    s = SystemIntegrationSettings(safe_mode=True, confirm_destructive=False,
                                  confirm_system_controls=False)
    s.validate()
    assert s.confirm_destructive is True
    assert s.confirm_system_controls is True


def test_skills_settings_seeds_defaults():
    s = SkillsSettings(enabled={"weather": False}, order=["weather"])
    s.validate()
    # every default skill gets an enabled entry + order slot
    for sid in DEFAULT_SKILL_ORDER:
        assert sid in s.enabled
        assert sid in s.order
    assert s.is_enabled("weather") is False
    assert s.is_enabled("time") is True
    assert s.priority_of("weather") == 0


# -- AppConfig tree -----------------------------------------------------------


def test_appconfig_roundtrip():
    cfg = AppConfig()
    cfg.voice.tts_speed = 1.5
    cfg.privacy.llm_provider = "local"
    cfg.validate()  # seeds skill defaults, as the manager always does
    data = cfg.to_dict()
    restored = AppConfig.from_dict(data)
    assert restored.to_dict() == data
    assert restored.version == CONFIG_VERSION


def test_appconfig_ignores_unknown_keys():
    data = {"version": 1, "voice": {"tts_speed": 1.2, "bogus": 9},
            "extra_section": {"x": 1}}
    cfg = AppConfig.from_dict(data)
    assert cfg.voice.tts_speed == 1.2


def test_migration_from_flat_uiconfig():
    # A pre-versioned flat UIConfig dump should be nested under "ui".
    legacy = {"size": 320, "opacity": 0.7, "theme": "ember",
              "animation_style": "rings"}
    cfg = AppConfig.from_dict(legacy)
    assert cfg.version == CONFIG_VERSION
    assert cfg.ui.size == 320
    assert cfg.ui.theme == "ember"


# -- manager ------------------------------------------------------------------


@pytest.fixture
def manager(monkeypatch, tmp_path):
    monkeypatch.setenv("MIMOSA_CONFIG", str(tmp_path / "settings.json"))
    monkeypatch.setenv("MIMOSA_UI_CONFIG", str(tmp_path / "ui.json"))
    m = AppConfigManager()
    m.load()
    return m


def test_manager_load_defaults_when_missing(manager):
    cfg = manager.get()
    assert cfg.version == CONFIG_VERSION
    assert cfg.privacy.llm_provider == DEFAULT_LLM_PROVIDER


def test_manager_save_and_reload(manager, tmp_path):
    manager.update_section("voice", tts_speed=1.7)
    assert (tmp_path / "settings.json").exists()
    # mirrored ui.json should also be written
    assert (tmp_path / "ui.json").exists()

    fresh = AppConfigManager()
    fresh.load()
    assert fresh.get().voice.tts_speed == 1.7


def test_manager_persisted_json_is_valid(manager, tmp_path):
    manager.save()
    data = json.loads((tmp_path / "settings.json").read_text())
    assert set(data) >= {"version", "voice", "skills", "system", "privacy", "ui"}


def test_manager_update_section_rejects_unknown_field(manager):
    with pytest.raises(KeyError):
        manager.update_section("voice", not_a_field=1)
    with pytest.raises(KeyError):
        manager.update_section("nope", x=1)


def test_manager_reset(manager):
    manager.update_section("privacy", llm_provider="none")
    manager.reset()
    assert manager.get().privacy.llm_provider == DEFAULT_LLM_PROVIDER


def test_manager_observers_fire_on_save(manager):
    seen = []
    manager.add_observer(lambda cfg: seen.append(cfg.voice.tts_speed))
    manager.update_section("voice", tts_speed=1.3)
    assert seen and seen[-1] == 1.3
    # removable
    cb = seen.append
    manager.add_observer(cb)
    manager.remove_observer(cb)


def test_manager_corrupt_file_degrades_to_defaults(monkeypatch, tmp_path):
    target = tmp_path / "settings.json"
    target.write_text("{ this is not json")
    monkeypatch.setenv("MIMOSA_CONFIG", str(target))
    m = AppConfigManager()
    m.load()
    assert m.get().version == CONFIG_VERSION  # no exception, defaults used


def test_manager_thread_safe_updates(manager):
    errors = []

    def worker(n):
        try:
            for _ in range(50):
                manager.update_section("voice", tts_speed=1.0 + (n % 5) * 0.1,
                                        persist=False)
                _ = manager.get().to_dict()
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors


def test_manager_replace_without_persist(manager, tmp_path):
    target = tmp_path / "settings.json"
    if target.exists():
        target.unlink()
    new_cfg = AppConfig()
    new_cfg.privacy.llm_provider = "local"
    manager.replace(new_cfg, persist=False)
    assert manager.get().privacy.llm_provider == "local"
    assert not target.exists()  # not written when persist=False



def test_privacy_memory_flags_default_on():
    p = PrivacySettings()
    assert p.persist_conversations is True
    assert p.learn_preferences is True
    assert p.semantic_memory is True
    assert p.auto_private_mode is True


def test_privacy_memory_flags_coerced_to_bool():
    p = PrivacySettings(
        persist_conversations=0,
        learn_preferences="",
        semantic_memory=1,
        auto_private_mode="yes",
    ).validate()
    assert p.persist_conversations is False
    assert p.learn_preferences is False
    assert p.semantic_memory is True
    assert p.auto_private_mode is True


def test_privacy_memory_flags_roundtrip():
    cfg = AppConfig()
    cfg.privacy.persist_conversations = False
    cfg.privacy.auto_private_mode = False
    restored = AppConfig.from_dict(cfg.to_dict())
    assert restored.privacy.persist_conversations is False
    assert restored.privacy.auto_private_mode is False



# -- research settings (M6) --------------------------------------------------

def test_research_settings_defaults_privacy_first():
    r = ResearchSettings()
    # web search OFF by default -> no surprise network on fresh install.
    assert r.web_search_enabled is False
    assert r.max_sources == 6
    assert r.token_budget == 3000
    assert r.learn_cost_patterns is True


def test_research_settings_clamps_and_coerces():
    r = ResearchSettings(
        web_search_enabled=1,
        backend="bogus",
        max_sources=999,
        per_category_cap=0,
        token_budget=1,
    ).validate()
    assert r.web_search_enabled is True
    assert r.backend == "duckduckgo"  # unknown backend -> default
    assert r.max_sources == 25  # clamped to max
    assert r.per_category_cap == 1  # clamped to min
    assert r.token_budget == 256  # clamped to min


def test_research_settings_backend_none_allowed():
    r = ResearchSettings(backend="none").validate()
    assert r.backend == "none"


def test_appconfig_includes_research_section():
    cfg = AppConfig().validate()
    assert isinstance(cfg.research, ResearchSettings)
    d = cfg.to_dict()
    assert "research" in d
    assert d["research"]["web_search_enabled"] is False


def test_appconfig_research_roundtrip():
    cfg = AppConfig()
    cfg.research.web_search_enabled = True
    cfg.research.max_sources = 8
    cfg.validate()
    data = cfg.to_dict()
    restored = AppConfig.from_dict(data)
    assert restored.research.web_search_enabled is True
    assert restored.research.max_sources == 8
    assert restored.to_dict() == data


def test_appconfig_old_payload_without_research_gets_defaults():
    # An on-disk config from before M6 (no research key) still loads cleanly.
    cfg = AppConfig.from_dict({"version": 1, "voice": {"tts_speed": 1.1}})
    assert cfg.research.web_search_enabled is False
    assert cfg.research.backend == "duckduckgo"


def test_research_in_default_skill_order():
    assert "research" in DEFAULT_SKILL_ORDER

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
    TasksSettings,
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


# --------------------------------------------------------------------------
# TasksSettings (M7)
# --------------------------------------------------------------------------

def test_tasks_settings_defaults():
    t = TasksSettings()
    assert t.background_tasks_enabled is True
    assert t.max_concurrent == 2
    assert t.resource_monitoring is True
    assert t.cpu_threshold == 85.0
    assert t.mem_threshold == 85.0
    assert t.learn_error_fixes is True


def test_tasks_settings_clamps_and_coerces():
    t = TasksSettings(
        background_tasks_enabled=1,
        max_concurrent=999,
        cpu_threshold=1.0,
        mem_threshold=999.0,
    ).validate()
    assert t.background_tasks_enabled is True
    assert t.max_concurrent == 8  # clamped to max
    assert t.cpu_threshold == 10.0  # clamped to min
    assert t.mem_threshold == 100.0  # clamped to max


def test_tasks_settings_min_concurrent():
    t = TasksSettings(max_concurrent=0).validate()
    assert t.max_concurrent == 1  # clamped to min


def test_appconfig_includes_tasks_section():
    cfg = AppConfig().validate()
    assert isinstance(cfg.tasks, TasksSettings)
    d = cfg.to_dict()
    assert "tasks" in d
    assert d["tasks"]["background_tasks_enabled"] is True


def test_appconfig_tasks_roundtrip():
    cfg = AppConfig()
    cfg.tasks.max_concurrent = 4
    cfg.tasks.learn_error_fixes = False
    cfg.validate()
    data = cfg.to_dict()
    restored = AppConfig.from_dict(data)
    assert restored.tasks.max_concurrent == 4
    assert restored.tasks.learn_error_fixes is False
    assert restored.to_dict() == data


def test_appconfig_old_payload_without_tasks_gets_defaults():
    # An on-disk config from before M7 (no tasks key) still loads cleanly.
    cfg = AppConfig.from_dict({"version": 1, "voice": {"tts_speed": 1.1}})
    assert cfg.tasks.background_tasks_enabled is True
    assert cfg.tasks.max_concurrent == 2


def test_tasks_in_default_skill_order():
    assert "tasks" in DEFAULT_SKILL_ORDER


# ---------------------------------------------------------------------------
# PersonalitySettings ("Get to Know MimOSA", M8.4a)
# ---------------------------------------------------------------------------

def test_personality_defaults():
    cfg = AppConfig()
    assert cfg.personality.user_name == ""
    assert cfg.personality.assistant_name == "MimOSA"
    assert cfg.personality.verbosity == "balanced"
    assert cfg.personality.greet_by_name is True


def test_personality_validate_trims_and_coerces():
    from mimosa.utils.config import PersonalitySettings

    p = PersonalitySettings(
        user_name="  Sam  ",
        assistant_name="   ",
        verbosity="LOUD",
    ).validate()
    assert p.user_name == "Sam"
    assert p.assistant_name == "MimOSA"  # blank -> default
    assert p.verbosity == "balanced"  # invalid -> default


def test_personality_long_value_trimmed():
    from mimosa.utils.config import PersonalitySettings, MAX_PERSONALIZATION_LEN

    p = PersonalitySettings(user_name="x" * 500).validate()
    assert len(p.user_name) == MAX_PERSONALIZATION_LEN


def test_personality_round_trip():
    cfg = AppConfig()
    cfg.personality.user_name = "Robin"
    cfg.personality.assistant_name = "Echo"
    cfg.validate()
    data = cfg.to_dict()
    restored = AppConfig.from_dict(data)
    assert restored.personality.user_name == "Robin"
    assert restored.personality.assistant_name == "Echo"
    assert restored.to_dict() == data


def test_personality_old_payload_gets_defaults():
    cfg = AppConfig.from_dict({"version": 1, "voice": {}})
    assert cfg.personality.assistant_name == "MimOSA"
    assert cfg.personality.verbosity == "balanced"


def test_personality_greeting_variants():
    from mimosa.utils.config import PersonalitySettings

    p = PersonalitySettings(user_name="Lee", assistant_name="Nova").validate()
    assert p.greeting() == "Hi Lee, I'm Nova."
    p.greet_by_name = False
    assert p.greeting() == "Hi, I'm Nova."
    p2 = PersonalitySettings().validate()
    assert p2.display_user() == "there"


# ---------------------------------------------------------------------------
# Gender / voice-style preference (Milestone 1, req #8)
# ---------------------------------------------------------------------------

def test_personality_gender_defaults_neutral():
    cfg = AppConfig()
    assert cfg.personality.gender == "neutral"


def test_personality_gender_validates():
    from mimosa.utils.config import PersonalitySettings

    assert PersonalitySettings(gender="FEMALE").validate().gender == "female"
    assert PersonalitySettings(gender="  Male ").validate().gender == "male"
    assert PersonalitySettings(gender="banana").validate().gender == "neutral"
    assert PersonalitySettings(gender="").validate().gender == "neutral"


def test_personality_gender_round_trip():
    cfg = AppConfig()
    cfg.personality.gender = "female"
    cfg.validate()
    data = cfg.to_dict()
    restored = AppConfig.from_dict(data)
    assert restored.personality.gender == "female"
    assert restored.to_dict() == data


def test_personality_old_payload_gets_default_gender():
    cfg = AppConfig.from_dict({"version": 1, "personality": {"user_name": "Sam"}})
    assert cfg.personality.gender == "neutral"


# ---------------------------------------------------------------------------
# HardwareSettings / capability detection (Milestone 1, req #7)
# ---------------------------------------------------------------------------

def test_hardware_defaults():
    cfg = AppConfig()
    assert cfg.hardware.capability_level == "unknown"
    assert cfg.hardware.detected is False
    assert cfg.hardware.gpu_available is False


def test_hardware_validate_coerces():
    from mimosa.utils.config import HardwareSettings

    hw = HardwareSettings(
        capability_level="GPU",
        ram_gb="16",
        disk_free_gb="100",
        cpu_cores="8",
        gpu_available=1,
        gpu_kind=None,
        detected=1,
    ).validate()
    assert hw.capability_level == "gpu"
    assert hw.ram_gb == 16.0
    assert hw.cpu_cores == 8
    assert hw.gpu_available is True
    assert hw.gpu_kind == ""
    assert hw.detected is True


def test_hardware_invalid_level_defaults_unknown():
    from mimosa.utils.config import HardwareSettings

    assert HardwareSettings(capability_level="bogus").validate().capability_level == "unknown"


def test_hardware_update_from_report():
    from mimosa.utils.config import HardwareSettings
    from mimosa.system.capability_detector import CapabilityReport

    report = CapabilityReport(
        level="cpu", ram_gb=8.0, disk_free_gb=50.0, cpu_cores=4,
        gpu_available=False, gpu_kind="",
    )
    hw = HardwareSettings().update_from_report(report)
    assert hw.capability_level == "cpu"
    assert hw.detected is True
    assert hw.can_train() is True


def test_hardware_can_train_false_when_insufficient():
    from mimosa.utils.config import HardwareSettings

    hw = HardwareSettings(capability_level="insufficient").validate()
    assert hw.can_train() is False


def test_hardware_round_trip():
    cfg = AppConfig()
    cfg.hardware.capability_level = "gpu"
    cfg.hardware.gpu_available = True
    cfg.hardware.gpu_kind = "cuda"
    cfg.hardware.detected = True
    cfg.validate()
    data = cfg.to_dict()
    restored = AppConfig.from_dict(data)
    assert restored.hardware.capability_level == "gpu"
    assert restored.hardware.gpu_kind == "cuda"
    assert restored.to_dict() == data


def test_appconfig_old_payload_without_hardware_gets_defaults():
    cfg = AppConfig.from_dict({"version": 1, "voice": {}})
    assert cfg.hardware.capability_level == "unknown"
    assert cfg.hardware.detected is False

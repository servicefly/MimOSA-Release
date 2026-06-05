"""Tests for mimosa.ui.ui_config -- UI preference validation & persistence.

Fully hermetic: uses tmp_path for the config file and monkeypatches env vars.
No GTK involved.
"""

import json

import pytest

from mimosa.ui.ui_config import (
    COLOR_THEMES,
    DEFAULT_OPACITY,
    DEFAULT_SIZE,
    DEFAULT_THEME,
    MAX_OPACITY,
    MAX_SIZE,
    MIN_OPACITY,
    MIN_SIZE,
    UIConfig,
    default_config_path,
)


class TestDefaults:
    def test_fresh_config_has_sane_defaults(self):
        c = UIConfig()
        assert c.size == DEFAULT_SIZE
        assert c.opacity == DEFAULT_OPACITY
        assert c.theme == DEFAULT_THEME
        assert c.always_on_top is True
        assert c.pos_x is None and c.pos_y is None

    def test_theme_colors_returns_active_theme(self):
        c = UIConfig(theme="ember")
        assert c.theme_colors() == COLOR_THEMES["ember"]

    def test_theme_colors_falls_back_for_unknown(self):
        c = UIConfig()
        c.theme = "does-not-exist"  # bypass validate
        assert c.theme_colors() == COLOR_THEMES[DEFAULT_THEME]


class TestValidate:
    def test_clamps_size_high_and_low(self):
        assert UIConfig(size=99999).validate().size == MAX_SIZE
        assert UIConfig(size=1).validate().size == MIN_SIZE

    def test_clamps_opacity(self):
        assert UIConfig(opacity=9.0).validate().opacity == MAX_OPACITY
        assert UIConfig(opacity=0.0).validate().opacity == MIN_OPACITY

    def test_unknown_theme_resets_to_default(self):
        assert UIConfig(theme="rainbow").validate().theme == DEFAULT_THEME

    def test_unknown_animation_style_resets(self):
        assert UIConfig(animation_style="explode").validate().animation_style == "pulse"

    def test_garbage_numbers_fall_back(self):
        c = UIConfig(size="abc", opacity="xyz", animation_speed=None)  # type: ignore
        c.validate()
        assert c.size == DEFAULT_SIZE
        assert c.opacity == DEFAULT_OPACITY

    def test_bad_positions_become_none(self):
        c = UIConfig(pos_x="nope", pos_y="nan").validate()  # type: ignore
        assert c.pos_x is None and c.pos_y is None

    def test_fps_clamped(self):
        assert UIConfig(target_fps=999).validate().target_fps == 60
        assert UIConfig(target_fps=1).validate().target_fps == 5

    def test_negative_monitor_clamped_to_zero(self):
        assert UIConfig(monitor=-3).validate().monitor == 0


class TestPersistence:
    def test_roundtrip(self, tmp_path):
        path = tmp_path / "ui.json"
        c = UIConfig(size=240, opacity=0.8, theme="mono", pos_x=10, pos_y=20)
        assert c.save(path) is True
        loaded = UIConfig.load(path)
        assert loaded.size == 240
        assert loaded.opacity == 0.8
        assert loaded.theme == "mono"
        assert loaded.pos_x == 10 and loaded.pos_y == 20

    def test_load_missing_file_returns_defaults(self, tmp_path):
        loaded = UIConfig.load(tmp_path / "nope.json")
        assert loaded.size == DEFAULT_SIZE

    def test_load_corrupt_file_returns_defaults(self, tmp_path):
        path = tmp_path / "bad.json"
        path.write_text("{ this is not json ]")
        loaded = UIConfig.load(path)
        assert loaded.size == DEFAULT_SIZE

    def test_load_non_object_json_returns_defaults(self, tmp_path):
        path = tmp_path / "list.json"
        path.write_text("[1, 2, 3]")
        loaded = UIConfig.load(path)
        assert loaded.theme == DEFAULT_THEME

    def test_load_ignores_unknown_keys(self, tmp_path):
        path = tmp_path / "extra.json"
        path.write_text(json.dumps({"size": 150, "bogus_key": "ignored"}))
        loaded = UIConfig.load(path)
        assert loaded.size == 150
        assert not hasattr(loaded, "bogus_key")

    def test_save_creates_parent_dirs(self, tmp_path):
        path = tmp_path / "nested" / "deep" / "ui.json"
        assert UIConfig(size=180).save(path) is True
        assert path.is_file()

    def test_saved_file_is_valid_json(self, tmp_path):
        path = tmp_path / "ui.json"
        UIConfig(size=210).save(path)
        data = json.loads(path.read_text())
        assert data["size"] == 210

    def test_save_validates_before_writing(self, tmp_path):
        path = tmp_path / "ui.json"
        UIConfig(size=99999).save(path)
        data = json.loads(path.read_text())
        assert data["size"] == MAX_SIZE


class TestDefaultPath:
    def test_env_override(self, monkeypatch, tmp_path):
        target = tmp_path / "custom.json"
        monkeypatch.setenv("MIMOSA_UI_CONFIG", str(target))
        assert default_config_path() == target

    def test_xdg_config_home(self, monkeypatch, tmp_path):
        monkeypatch.delenv("MIMOSA_UI_CONFIG", raising=False)
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        p = default_config_path()
        assert p == tmp_path / "mimosa" / "ui.json"

    def test_load_uses_default_path(self, monkeypatch, tmp_path):
        target = tmp_path / "ui.json"
        monkeypatch.setenv("MIMOSA_UI_CONFIG", str(target))
        UIConfig(size=175).save()  # no explicit path -> default path
        assert target.is_file()
        assert UIConfig.load().size == 175

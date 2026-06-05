"""Tests for mimosa.ui.environment & mimosa.ui.avatar_assets.

Headless-detection logic is verified by monkeypatching env vars and the
``gtk_available`` probe. Asset location is tested against tmp dirs and the
bundled default.svg.
"""

import pytest

from mimosa.ui import environment as env
from mimosa.ui.avatar_assets import AvatarAssets


class TestHasDisplay:
    def test_x11_display(self, monkeypatch):
        monkeypatch.setenv("DISPLAY", ":0")
        monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
        assert env.has_display() is True

    def test_wayland_display(self, monkeypatch):
        monkeypatch.delenv("DISPLAY", raising=False)
        monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
        assert env.has_display() is True

    def test_no_display(self, monkeypatch):
        monkeypatch.delenv("DISPLAY", raising=False)
        monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
        assert env.has_display() is False


class TestIsGuiAvailable:
    def test_requires_both_display_and_gtk(self, monkeypatch):
        monkeypatch.setattr(env, "has_display", lambda: True)
        monkeypatch.setattr(env, "gtk_available", lambda: True)
        assert env.is_gui_available() is True

    def test_headless_when_no_display(self, monkeypatch):
        monkeypatch.setattr(env, "has_display", lambda: False)
        monkeypatch.setattr(env, "gtk_available", lambda: True)
        assert env.is_gui_available() is False

    def test_headless_when_no_gtk(self, monkeypatch):
        monkeypatch.setattr(env, "has_display", lambda: True)
        monkeypatch.setattr(env, "gtk_available", lambda: False)
        assert env.is_gui_available() is False

    def test_describe_mentions_state(self, monkeypatch):
        monkeypatch.setattr(env, "has_display", lambda: False)
        monkeypatch.setattr(env, "gtk_available", lambda: False)
        desc = env.describe_environment()
        assert "headless" in desc
        assert "display=no" in desc


class TestGtkAvailableProbe:
    def test_returns_bool(self):
        assert isinstance(env.gtk_available(), bool)


class TestAvatarAssets:
    def test_default_dir_finds_bundled_svg(self):
        assets = AvatarAssets()
        # The repo ships data/avatars/default.svg
        assert assets.exists()
        assert assets.default_svg_path() is not None
        assert assets.default_svg_path().name == "default.svg"

    def test_env_override(self, monkeypatch, tmp_path):
        monkeypatch.setenv("MIMOSA_AVATAR_DIR", str(tmp_path))
        assets = AvatarAssets()
        assert assets.directory == tmp_path

    def test_missing_default_returns_none(self, tmp_path):
        assets = AvatarAssets(directory=tmp_path)
        assert assets.default_svg_path() is None

    def test_list_assets(self, tmp_path):
        (tmp_path / "a.svg").write_text("<svg/>")
        (tmp_path / "b.png").write_bytes(b"\x89PNG")
        (tmp_path / "c.txt").write_text("ignore me")
        assets = AvatarAssets(directory=tmp_path)
        names = [p.name for p in assets.list_assets()]
        assert names == ["a.svg", "b.png"]

    def test_list_assets_missing_dir(self, tmp_path):
        assets = AvatarAssets(directory=tmp_path / "nope")
        assert assets.list_assets() == []

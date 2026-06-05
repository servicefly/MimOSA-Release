"""Tests for on-device data-path resolution (M5)."""

from __future__ import annotations

from pathlib import Path

from mimosa.memory import paths


def test_mimosa_data_env_wins(monkeypatch, tmp_path):
    monkeypatch.setenv("MIMOSA_DATA", str(tmp_path / "custom"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    assert paths.default_data_dir() == (tmp_path / "custom")


def test_xdg_data_home_used_when_no_override(monkeypatch, tmp_path):
    monkeypatch.delenv("MIMOSA_DATA", raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    assert paths.default_data_dir() == (tmp_path / "xdg" / "mimosa")


def test_fallback_to_local_share(monkeypatch):
    monkeypatch.delenv("MIMOSA_DATA", raising=False)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    d = paths.default_data_dir()
    assert d.name == "mimosa"
    assert d.parent.name == "share"


def test_db_path_helpers(monkeypatch, tmp_path):
    monkeypatch.setenv("MIMOSA_DATA", str(tmp_path))
    assert paths.conversations_db_path() == tmp_path / "conversations.db"
    assert paths.preferences_db_path() == tmp_path / "preferences.db"
    assert paths.private_db_path() == tmp_path / "private.db"
    assert paths.semantic_store_dir() == tmp_path / "semantic"


def test_ensure_data_dir_creates(monkeypatch, tmp_path):
    target = tmp_path / "nested" / "data"
    monkeypatch.setenv("MIMOSA_DATA", str(target))
    out = paths.ensure_data_dir()
    assert out == target
    assert target.is_dir()


def test_default_data_dir_no_side_effects(monkeypatch, tmp_path):
    # Merely resolving the path must not create directories.
    target = tmp_path / "should_not_exist"
    monkeypatch.setenv("MIMOSA_DATA", str(target))
    paths.default_data_dir()
    assert not target.exists()


def test_expanduser_applied(monkeypatch):
    monkeypatch.setenv("MIMOSA_DATA", "~/mimosa_test_dir")
    resolved = paths.default_data_dir()
    assert "~" not in str(resolved)
    assert isinstance(resolved, Path)

"""Tests for the M2.2 application registry and ApplicationSkill.

Fully hermetic: the application search directory is redirected to a pytest
``tmp_path`` via the ``MIMOSA_APP_DIRS`` environment variable and filled with
fixture ``.desktop`` files, while process spawning, process discovery, and
termination are injected fakes -- so no real applications are launched or
killed.

Run with:  pytest -q tests/test_application.py
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from mimosa.system.app_registry import (
    AppEntry,
    AppRegistry,
    parse_desktop_file,
    strip_field_codes,
)
from mimosa.skills.application import ApplicationSkill
from mimosa.core.intent_router import IntentRouter, INTENT_APPLICATION


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _write_desktop(directory: Path, filename: str, **fields: str) -> Path:
    """Write a minimal .desktop file with the given [Desktop Entry] fields."""
    directory.mkdir(parents=True, exist_ok=True)
    lines = ["[Desktop Entry]"]
    for key, value in fields.items():
        lines.append(f"{key}={value}")
    path = directory / filename
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


@pytest.fixture
def app_dir(tmp_path, monkeypatch):
    """A temp applications directory wired up as the only registry source."""
    apps = tmp_path / "applications"
    apps.mkdir()
    _write_desktop(
        apps, "firefox.desktop",
        Name="Firefox", Exec="firefox %u", Icon="firefox",
        Categories="Network;WebBrowser;", Comment="Browse the web",
    )
    _write_desktop(
        apps, "org.kde.kwrite.desktop",
        Name="KWrite", Exec="kwrite %F", Icon="kwrite",
        Categories="Utility;TextEditor;", Keywords="text;editor;",
    )
    _write_desktop(
        apps, "org.kde.dolphin.desktop",
        Name="Dolphin", Exec="dolphin %u", Icon="system-file-manager",
        Categories="System;FileManager;Utility;",
    )
    # A hidden entry that should never surface.
    _write_desktop(
        apps, "hidden.desktop",
        Name="Secret", Exec="secret", NoDisplay="true",
    )
    # A non-application type that should be ignored.
    _write_desktop(
        apps, "link.desktop",
        Name="A Link", Type="Link", URL="https://example.com",
    )
    # A malformed file (no [Desktop Entry]) -- must be skipped, not crash.
    (apps / "broken.desktop").write_text("this is not = valid\n[oops", encoding="utf-8")
    monkeypatch.setenv("MIMOSA_APP_DIRS", str(apps))
    return apps


@pytest.fixture
def registry(app_dir):
    return AppRegistry()


@pytest.fixture
def spawned():
    return []


@pytest.fixture
def proc_table():
    """Mutable list of fake running-process dicts the skill will read."""
    return []


@pytest.fixture
def terminated():
    return []


@pytest.fixture
def skill(registry, spawned, proc_table, terminated):
    """An ApplicationSkill with all side-effecting backends faked."""
    def fake_spawn(argv):
        spawned.append(argv)
        pid = 4321
        proc_table.append({"pid": pid, "name": os.path.basename(argv[0]).lower(),
                           "exe": argv[0], "cmdline": " ".join(argv).lower()})
        return pid

    def fake_lister():
        return list(proc_table)

    def fake_terminator(pid, force):
        terminated.append((pid, force))
        return True

    return ApplicationSkill(
        registry=registry,
        spawn=fake_spawn,
        process_lister=fake_lister,
        terminator=fake_terminator,
    )


# ---------------------------------------------------------------------------
# .desktop parsing
# ---------------------------------------------------------------------------

class TestDesktopParsing:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("firefox %u", "firefox"),
            ("kwrite %F", "kwrite"),
            ("env FOO=1 app %U --flag", "env FOO=1 app --flag"),
            ("gimp %f %i %c", "gimp"),
            ("printf 100%%", "printf 100%"),
            ("", ""),
        ],
    )
    def test_strip_field_codes(self, raw, expected):
        assert strip_field_codes(raw) == expected

    def test_parse_valid(self, app_dir):
        entry = parse_desktop_file(app_dir / "firefox.desktop")
        assert entry is not None
        assert entry.name == "Firefox"
        assert entry.exec_command == "firefox"
        assert "WebBrowser" in entry.categories
        assert entry.argv() == ["firefox"]

    def test_parse_hidden_returns_none(self, app_dir):
        assert parse_desktop_file(app_dir / "hidden.desktop") is None

    def test_parse_non_application_returns_none(self, app_dir):
        assert parse_desktop_file(app_dir / "link.desktop") is None

    def test_parse_malformed_returns_none(self, app_dir):
        # Should not raise.
        assert parse_desktop_file(app_dir / "broken.desktop") is None

    def test_parse_missing_file_returns_none(self, tmp_path):
        assert parse_desktop_file(tmp_path / "nope.desktop") is None


# ---------------------------------------------------------------------------
# Registry: discovery, caching, categories, fuzzy match
# ---------------------------------------------------------------------------

class TestAppRegistry:
    def test_discovers_visible_apps_only(self, registry):
        names = {a.name for a in registry.all_apps()}
        assert names == {"Firefox", "KWrite", "Dolphin"}

    def test_len(self, registry):
        assert len(registry) == 3

    def test_get_by_id(self, registry):
        assert registry.get("firefox").name == "Firefox"
        assert registry.get("does-not-exist") is None

    def test_by_category(self, registry):
        editors = registry.by_category("TextEditor")
        assert [a.name for a in editors] == ["KWrite"]
        # Case-insensitive.
        assert registry.by_category("texteditor")[0].name == "KWrite"
        assert registry.by_category("") == []

    def test_find_exact(self, registry):
        assert registry.find("Firefox").app_id == "firefox"

    def test_find_case_insensitive(self, registry):
        assert registry.find("firefox").app_id == "firefox"

    def test_find_fuzzy_misheard(self, registry):
        # Slight mishearing should still resolve.
        assert registry.find("fire fox").app_id == "firefox"

    def test_find_by_keyword(self, registry):
        # KWrite declares the "editor" keyword.
        match = registry.find("editor")
        assert match is not None and match.app_id == "org.kde.kwrite"

    def test_find_unknown_returns_none(self, registry):
        assert registry.find("photoshop") is None

    def test_rank_returns_scores(self, registry):
        ranked = registry.rank("dolphin")
        assert ranked[0][0].name == "Dolphin"
        assert ranked[0][1] > 0.6

    def test_caching_and_refresh(self, registry, app_dir):
        _ = registry.all_apps()  # build cache
        _write_desktop(app_dir, "vlc.desktop", Name="VLC", Exec="vlc %U",
                       Categories="AudioVideo;Player;")
        # Not visible until refresh.
        assert registry.find("VLC") is None
        registry.refresh()
        assert registry.find("VLC").name == "VLC"

    def test_explicit_search_dirs(self, app_dir):
        reg = AppRegistry(search_dirs=[app_dir])
        assert reg.find("Firefox") is not None


# ---------------------------------------------------------------------------
# ApplicationSkill: launching
# ---------------------------------------------------------------------------

class TestLaunch:
    def test_launch_by_name(self, skill, spawned):
        res = skill.handle("open Firefox")
        assert res.success
        assert "Opening Firefox" in res.text
        assert spawned == [["firefox"]]

    def test_launch_start_verb(self, skill, spawned):
        res = skill.handle("start Dolphin")
        assert res.success and spawned == [["dolphin"]]

    def test_launch_bare_name(self, skill, spawned):
        res = skill.handle("Firefox")
        assert res.success and spawned == [["firefox"]]

    def test_launch_unknown_app_suggests(self, skill, spawned):
        res = skill.handle("open Photoshop")
        assert not res.success
        assert "couldn't find" in res.text.lower()
        assert spawned == []

    def test_launch_fuzzy(self, skill, spawned):
        res = skill.handle("launch fire fox")
        assert res.success and spawned == [["firefox"]]

    def test_launch_spawn_error(self, registry):
        def boom(argv):
            raise FileNotFoundError("no binary")
        s = ApplicationSkill(registry=registry, spawn=boom,
                             process_lister=lambda: [], terminator=lambda p, f: True)
        res = s.handle("open Firefox")
        assert not res.success
        assert "wasn't found" in res.text


# ---------------------------------------------------------------------------
# ApplicationSkill: running status & close (with confirmation)
# ---------------------------------------------------------------------------

class TestStatusAndClose:
    def test_is_running_true(self, skill, proc_table):
        proc_table.append({"pid": 10, "name": "firefox", "exe": "/usr/bin/firefox",
                           "cmdline": "firefox"})
        res = skill.handle("is Firefox running?")
        assert res.success and res.metadata["running"] is True
        assert "is running" in res.text

    def test_is_running_false(self, skill):
        res = skill.handle("is Firefox running?")
        assert res.success and res.metadata["running"] is False
        assert "isn't running" in res.text

    def test_close_requires_confirmation(self, skill, proc_table, terminated):
        proc_table.append({"pid": 55, "name": "firefox", "exe": "/usr/bin/firefox",
                           "cmdline": "firefox"})
        res = skill.handle("close Firefox")
        assert res.metadata["operation"] == "confirm_required"
        assert skill.has_pending_confirmation()
        assert terminated == []  # nothing killed yet
        # Confirm.
        res2 = skill.handle("yes")
        assert res2.success and "Closed Firefox" in res2.text
        assert terminated == [(55, False)]
        assert not skill.has_pending_confirmation()

    def test_close_cancel(self, skill, proc_table, terminated):
        proc_table.append({"pid": 55, "name": "firefox", "exe": "/usr/bin/firefox",
                           "cmdline": "firefox"})
        skill.handle("close Firefox")
        res = skill.handle("no")
        assert "leave it" in res.text.lower()
        assert terminated == []
        assert not skill.has_pending_confirmation()

    def test_close_not_running(self, skill):
        res = skill.handle("close Firefox")
        assert not res.success
        assert "doesn't seem to be running" in res.text

    def test_force_kill_flag(self, skill, proc_table, terminated):
        proc_table.append({"pid": 7, "name": "firefox", "exe": "/usr/bin/firefox",
                           "cmdline": "firefox"})
        skill.handle("force close Firefox")
        skill.handle("yes")
        assert terminated == [(7, True)]


# ---------------------------------------------------------------------------
# ApplicationSkill: listing
# ---------------------------------------------------------------------------

class TestList:
    def test_list_all(self, skill):
        res = skill.handle("list my applications")
        assert res.success and res.metadata["count"] == 3

    def test_list_browsers(self, skill):
        res = skill.handle("what browsers do I have")
        assert res.success
        assert res.metadata["category"] == "WebBrowser"
        assert res.metadata["apps"] == ["Firefox"]

    def test_list_editors(self, skill):
        res = skill.handle("list my text editors")
        assert res.metadata["apps"] == ["KWrite"]


# ---------------------------------------------------------------------------
# Routing integration
# ---------------------------------------------------------------------------

class TestRouting:
    def test_router_routes_app_intent(self, app_dir):
        router = IntentRouter()
        c = router.classify("open Firefox")
        assert c.intent == INTENT_APPLICATION

    def test_router_dispatches_to_skill(self, app_dir, spawned):
        proc_table = []

        def fake_spawn(argv):
            spawned.append(argv)
            proc_table.append({"pid": 999, "name": os.path.basename(argv[0]).lower(),
                               "exe": argv[0], "cmdline": " ".join(argv).lower()})
            return 999

        skill = ApplicationSkill(
            registry=AppRegistry(),
            spawn=fake_spawn,
            process_lister=lambda: list(proc_table),
            terminator=lambda p, f: True,
        )
        router = IntentRouter(skills=[skill])
        res = router.route("open Firefox")
        assert res.success and spawned == [["firefox"]]

    def test_router_pending_confirmation_routes_back(self, app_dir):
        proc = [{"pid": 3, "name": "firefox", "exe": "/usr/bin/firefox", "cmdline": "firefox"}]
        killed = []
        skill = ApplicationSkill(
            registry=AppRegistry(),
            spawn=lambda argv: 1,
            process_lister=lambda: list(proc),
            terminator=lambda p, f: killed.append((p, f)) or True,
        )
        router = IntentRouter(skills=[skill])
        router.route("close Firefox")  # queues confirmation
        router.route("yes")            # should resolve via pending path
        assert killed == [(3, False)]

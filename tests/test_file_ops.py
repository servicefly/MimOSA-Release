"""Tests for the M2.1 file-operations skill and safety layer.

These tests run **fully offline and hermetically**: every test redirects the
file sandbox to a pytest ``tmp_path`` via the ``MIMOSA_FILE_ROOT`` environment
variable, and the desktop opener / Trash backends are injected fakes -- so no
real files outside the temp dir are touched and no GUI apps are launched.

Run with:  pytest -q tests/test_file_ops.py
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from mimosa.skills.file_ops import (
    FILE_TYPE_EXTENSIONS,
    FileMatch,
    FileOperationsSkill,
)
from mimosa.system import file_safety
from mimosa.system.file_safety import (
    FileSafetyError,
    SafetyDecision,
    is_blacklisted,
    validate_path,
)
from mimosa.core.intent_router import IntentRouter, INTENT_FILE


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """Point the file sandbox at an isolated temp dir for the duration of a test."""
    monkeypatch.setenv("MIMOSA_FILE_ROOT", str(tmp_path))
    return tmp_path.resolve()


@pytest.fixture
def opened():
    """Collect paths passed to the fake opener."""
    return []


@pytest.fixture
def trashed():
    """Collect paths passed to the fake Trash backend."""
    return []


@pytest.fixture
def skill(opened, trashed):
    """A FileOperationsSkill with injected fake opener/trash (no real I/O)."""
    return FileOperationsSkill(
        opener=lambda p: opened.append(p) or 0,
        trash=lambda p: trashed.append(p),
    )


def _touch(path: Path, content: str = "x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Safety layer
# ---------------------------------------------------------------------------

class TestFileSafety:
    def test_home_root_honours_override(self, sandbox):
        assert file_safety.get_home_root() == sandbox

    @pytest.mark.parametrize(
        "bad",
        ["/etc/passwd", "/bin/bash", "/sys/kernel", "/proc/1", "/boot/grub",
         "/root/.bashrc", "/usr/lib/x", "/var/log/syslog"],
    )
    def test_blacklisted_system_paths_rejected(self, sandbox, bad):
        decision = validate_path(bad)
        assert decision.allowed is False
        assert decision.reason == "blacklisted"

    def test_is_blacklisted_prefix_not_substring(self, sandbox):
        # /etcetera is NOT inside /etc.
        assert is_blacklisted(Path("/etcetera/file")) is False
        assert is_blacklisted(Path("/etc/hosts")) is True

    def test_path_outside_sandbox_rejected(self, sandbox, tmp_path):
        outside = tmp_path.parent / "somewhere_else_xyz"
        decision = validate_path(str(outside))
        assert decision.allowed is False
        assert decision.reason == "outside_sandbox"

    def test_traversal_escape_blocked(self, sandbox):
        # ../../etc/passwd resolves outside the sandbox -> rejected.
        decision = validate_path("../../../../etc/passwd")
        assert decision.allowed is False

    def test_valid_relative_path_allowed(self, sandbox):
        decision = validate_path("notes.txt")
        assert decision.allowed is True
        assert decision.path == (sandbox / "notes.txt")

    def test_sensitive_dotfile_flagged(self, sandbox):
        decision = validate_path(".ssh/id_rsa")
        assert decision.allowed is True
        assert decision.sensitive is True

    def test_require_safe_raises_on_blacklist(self, sandbox):
        with pytest.raises(FileSafetyError):
            file_safety.require_safe("/etc/passwd")

    def test_empty_path_raises(self, sandbox):
        with pytest.raises(FileSafetyError):
            file_safety.resolve_path("   ")


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------

class TestCreate:
    def test_create_directory(self, sandbox, skill):
        res = skill.create_directory("Projects")
        assert res.success
        assert (sandbox / "Projects").is_dir()

    def test_create_nested_directory(self, sandbox, skill):
        res = skill.create_directory("a/b/c")
        assert res.success
        assert (sandbox / "a/b/c").is_dir()

    def test_create_directory_conflict(self, sandbox, skill):
        (sandbox / "Projects").mkdir()
        res = skill.create_directory("Projects")
        assert res.success is False
        assert "already exists" in res.text

    def test_create_file_with_content(self, sandbox, skill):
        res = skill.create_file("notes.txt", "hello world")
        assert res.success
        assert (sandbox / "notes.txt").read_text() == "hello world"
        assert res.metadata["bytes"] == len("hello world")

    def test_create_file_conflict(self, sandbox, skill):
        _touch(sandbox / "notes.txt")
        res = skill.create_file("notes.txt")
        assert res.success is False

    def test_create_outside_sandbox_rejected(self, sandbox, skill):
        res = skill.create_directory("/etc/evil")
        assert res.success is False
        assert res.metadata["operation"] == "create"

    def test_nl_create_folder(self, sandbox, skill):
        res = skill.handle("create a folder called Taxes")
        assert res.success
        assert (sandbox / "Taxes").is_dir()

    def test_nl_create_file_in_location(self, sandbox, skill):
        skill.create_directory("Docs")
        res = skill.handle("create a file called report.txt in Docs")
        assert res.success
        assert (sandbox / "Docs/report.txt").exists()


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

class TestSearch:
    def test_search_by_name_case_insensitive(self, sandbox, skill):
        _touch(sandbox / "Budget2026.xlsx")
        _touch(sandbox / "notes.txt")
        results = skill.search_files("budget")
        assert len(results) == 1
        assert results[0].path.name == "Budget2026.xlsx"

    def test_search_by_file_type(self, sandbox, skill):
        _touch(sandbox / "a.png")
        _touch(sandbox / "b.jpg")
        _touch(sandbox / "c.txt")
        results = skill.search_files(file_type="images")
        names = sorted(r.path.name for r in results)
        assert names == ["a.png", "b.jpg"]

    def test_search_respects_limit(self, sandbox, skill):
        for i in range(15):
            _touch(sandbox / f"file{i}.txt")
        results = skill.search_files("file", limit=5)
        assert len(results) == 5

    def test_search_skips_hidden_dirs(self, sandbox, skill):
        _touch(sandbox / ".config/secret.txt")
        _touch(sandbox / "visible.txt")
        results = skill.search_files("")
        names = [r.path.name for r in results]
        assert "secret.txt" not in names
        assert "visible.txt" in names

    def test_search_sorted_newest_first(self, sandbox, skill):
        old = _touch(sandbox / "old.txt")
        new = _touch(sandbox / "new.txt")
        os.utime(old, (1_000_000, 1_000_000))
        os.utime(new, (2_000_000, 2_000_000))
        results = skill.search_files("")
        assert results[0].path.name == "new.txt"

    def test_search_with_preview(self, sandbox, skill):
        _touch(sandbox / "doc.txt", "first line here\nsecond")
        results = skill.search_files("doc", with_preview=True)
        assert results[0].preview == "first line here"

    def test_search_root_outside_sandbox_raises(self, sandbox, skill):
        with pytest.raises(FileSafetyError):
            skill.search_files("x", root="/etc")

    def test_nl_search_no_results(self, sandbox, skill):
        res = skill.handle("find my photos")
        assert res.success
        assert res.metadata["count"] == 0

    def test_nl_search_reports_count(self, sandbox, skill):
        _touch(sandbox / "vacation.png")
        res = skill.handle("find my photos")
        assert res.metadata["count"] == 1
        assert res.metadata["file_type"] == "images"


class TestFileMatch:
    def test_human_size_units(self, sandbox):
        m = FileMatch(path=Path("/x"), is_dir=False, size=2048, modified=0)
        assert m.human_size() == "2.0 KB"
        d = FileMatch(path=Path("/x"), is_dir=True, size=0, modified=0)
        assert d.human_size() == "folder"

    def test_human_modified_format(self, sandbox):
        m = FileMatch(path=Path("/x"), is_dir=False, size=1, modified=1_700_000_000)
        assert len(m.human_modified()) == 10  # YYYY-MM-DD


# ---------------------------------------------------------------------------
# Open
# ---------------------------------------------------------------------------

class TestOpen:
    def test_open_existing(self, sandbox, skill, opened):
        _touch(sandbox / "notes.txt")
        res = skill.open_path("notes.txt")
        assert res.success
        assert opened == [str(sandbox / "notes.txt")]

    def test_open_missing(self, sandbox, skill):
        res = skill.open_path("nope.txt")
        assert res.success is False

    def test_open_blacklisted(self, sandbox, skill):
        res = skill.open_path("/etc/passwd")
        assert res.success is False

    def test_open_nonzero_returncode(self, sandbox, opened):
        s = FileOperationsSkill(opener=lambda p: 1, trash=lambda p: None)
        _touch(sandbox / "x.txt")
        res = s.open_path("x.txt")
        assert res.success is False

    def test_nl_open(self, sandbox, skill, opened):
        _touch(sandbox / "song.mp3")
        res = skill.handle("open song.mp3")
        assert res.success
        assert opened[0].endswith("song.mp3")


# ---------------------------------------------------------------------------
# Move / rename
# ---------------------------------------------------------------------------

class TestMove:
    def test_rename(self, sandbox, skill):
        _touch(sandbox / "a.txt")
        res = skill.move_path("a.txt", "b.txt")
        assert res.success
        assert (sandbox / "b.txt").exists()
        assert not (sandbox / "a.txt").exists()

    def test_move_into_directory(self, sandbox, skill):
        _touch(sandbox / "a.txt")
        (sandbox / "Docs").mkdir()
        res = skill.move_path("a.txt", "Docs")
        assert res.success
        assert (sandbox / "Docs/a.txt").exists()

    def test_move_conflict_refused(self, sandbox, skill):
        _touch(sandbox / "a.txt")
        _touch(sandbox / "b.txt")
        res = skill.move_path("a.txt", "b.txt")
        assert res.success is False
        assert res.metadata.get("conflict") is True

    def test_move_overwrite(self, sandbox, skill):
        _touch(sandbox / "a.txt", "new")
        _touch(sandbox / "b.txt", "old")
        res = skill.move_path("a.txt", "b.txt", overwrite=True)
        assert res.success
        assert (sandbox / "b.txt").read_text() == "new"

    def test_move_missing_source(self, sandbox, skill):
        res = skill.move_path("ghost.txt", "x.txt")
        assert res.success is False

    def test_move_source_blacklisted(self, sandbox, skill):
        res = skill.move_path("/etc/passwd", "x.txt")
        assert res.success is False

    def test_nl_move_conflict_then_confirm(self, sandbox, skill):
        _touch(sandbox / "a.txt", "new")
        _touch(sandbox / "b.txt", "old")
        prompt = skill.handle("move a.txt to b.txt")
        assert skill.has_pending_confirmation()
        assert "already exists" in prompt.text
        done = skill.handle("yes")
        assert done.success
        assert (sandbox / "b.txt").read_text() == "new"


# ---------------------------------------------------------------------------
# Delete (confirmation + trash/permanent)
# ---------------------------------------------------------------------------

class TestDelete:
    def test_delete_to_trash(self, sandbox, skill, trashed):
        f = _touch(sandbox / "junk.txt")
        res = skill.delete_path("junk.txt")
        assert res.success
        assert res.metadata["permanent"] is False
        assert trashed == [str(f)]

    def test_delete_permanent(self, sandbox, skill):
        _touch(sandbox / "junk.txt")
        res = skill.delete_path("junk.txt", permanent=True)
        assert res.success
        assert not (sandbox / "junk.txt").exists()

    def test_delete_permanent_directory(self, sandbox, skill):
        (sandbox / "olddir").mkdir()
        _touch(sandbox / "olddir/inner.txt")
        res = skill.delete_path("olddir", permanent=True)
        assert res.success
        assert not (sandbox / "olddir").exists()

    def test_delete_missing(self, sandbox, skill):
        res = skill.delete_path("ghost.txt")
        assert res.success is False

    def test_delete_blacklisted(self, sandbox, skill):
        res = skill.delete_path("/etc/passwd")
        assert res.success is False

    def test_delete_no_trash_backend_falls_back_permanent(self, sandbox):
        s = FileOperationsSkill(opener=lambda p: 0, trash=None)
        _touch(sandbox / "junk.txt")
        res = s.delete_path("junk.txt")
        assert res.success
        assert not (sandbox / "junk.txt").exists()

    def test_nl_delete_requires_confirmation(self, sandbox, skill, trashed):
        _touch(sandbox / "junk.txt")
        prompt = skill.handle("delete junk.txt")
        assert prompt.metadata["operation"] == "confirm_required"
        assert skill.has_pending_confirmation()
        assert trashed == []  # nothing deleted yet
        done = skill.handle("yes")
        assert done.success
        assert trashed and trashed[0].endswith("junk.txt")
        assert not skill.has_pending_confirmation()

    def test_nl_delete_cancel(self, sandbox, skill, trashed):
        _touch(sandbox / "junk.txt")
        skill.handle("delete junk.txt")
        res = skill.handle("no")
        assert "cancelled" in res.text.lower()
        assert trashed == []
        assert not skill.has_pending_confirmation()

    def test_nl_delete_unclear_reprompt(self, sandbox, skill):
        _touch(sandbox / "junk.txt")
        skill.handle("delete junk.txt")
        res = skill.handle("maybe later")
        assert res.success is False
        assert skill.has_pending_confirmation()  # still waiting

    def test_nl_permanent_delete(self, sandbox, skill):
        _touch(sandbox / "junk.txt")
        prompt = skill.handle("permanently delete junk.txt")
        assert "permanently" in prompt.text
        done = skill.handle("yes")
        assert done.success
        assert not (sandbox / "junk.txt").exists()


# ---------------------------------------------------------------------------
# NL parsing edge cases
# ---------------------------------------------------------------------------

class TestParsing:
    def test_empty_command(self, sandbox, skill):
        res = skill.handle("")
        assert res.success is False

    def test_unrecognized_command(self, sandbox, skill):
        res = skill.handle("dance around the file")
        assert res.success is False

    def test_quoted_filename(self, sandbox, skill):
        skill.handle('create a file called "my notes.txt"')
        assert (sandbox / "my notes.txt").exists()

    def test_reorder_location_helper(self):
        assert FileOperationsSkill._reorder_location("a.txt in Projects") == str(
            Path("Projects") / "a.txt"
        )
        assert FileOperationsSkill._reorder_location("a.txt") == "a.txt"

    def test_detect_file_type_synonyms(self):
        assert FileOperationsSkill._detect_file_type("find my pictures") == "images"
        assert FileOperationsSkill._detect_file_type("find my songs") == "audio"
        assert FileOperationsSkill._detect_file_type("hello there") is None


# ---------------------------------------------------------------------------
# Intent router integration
# ---------------------------------------------------------------------------

class TestRouterIntegration:
    @pytest.mark.parametrize(
        "utterance",
        [
            "create a folder called Projects",
            "delete notes.txt",
            "find my documents",
            "open report.pdf",
            "move a.txt to Documents",
            "rename a.txt to b.txt",
            "search for my photos",
            "make a new file",
            "where is my budget spreadsheet",
        ],
    )
    def test_file_commands_classified_as_file(self, sandbox, utterance):
        router = IntentRouter()  # no LLM; pure heuristics
        assert router.classify(utterance).intent == INTENT_FILE

    def test_non_file_commands_not_misrouted(self, sandbox):
        router = IntentRouter()
        assert router.classify("what time is it").intent != INTENT_FILE
        assert router.classify("what is 2 plus 2").intent != INTENT_FILE
        assert router.classify("what's the weather").intent != INTENT_FILE

    def test_router_routes_to_file_skill(self, sandbox, opened, trashed):
        s = FileOperationsSkill(
            opener=lambda p: opened.append(p) or 0,
            trash=lambda p: trashed.append(p),
        )
        router = IntentRouter(skills=[s])
        res = router.route("create a folder called Reports")
        assert res.success
        assert res.metadata["intent"] == INTENT_FILE
        assert (sandbox / "Reports").is_dir()

    def test_router_pending_confirmation_routes_back(self, sandbox, trashed):
        s = FileOperationsSkill(opener=lambda p: 0, trash=lambda p: trashed.append(p))
        router = IntentRouter(skills=[s])
        _touch(sandbox / "junk.txt")
        router.route("delete junk.txt")
        # A bare "yes" -- which would normally classify as a question -- must be
        # routed back to the file skill to resolve the pending delete.
        res = router.route("yes")
        assert res.metadata["classification_source"] == "pending_confirmation"
        assert trashed and trashed[0].endswith("junk.txt")

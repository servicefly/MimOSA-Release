"""File operations skill -- search, open, create, move, and delete files (M2.1).

This is MimOSA's first *system integration* skill. It lets the user manage files
by voice ("find my budget spreadsheet", "create a folder called Taxes",
"delete old-notes.txt") while a strict safety layer
(:mod:`mimosa.system.file_safety`) prevents the assistant from ever touching
system-critical paths or anything outside the user's home sandbox.

Design principles
-----------------
* **Local & private.** Every operation is performed on-device with
  :mod:`pathlib`/:mod:`shutil`; nothing is sent to the cloud (``uses_llm`` is
  ``False``). Search never reads file *contents* unless a content preview is
  explicitly requested.
* **Safety first.** All paths pass through
  :func:`mimosa.system.file_safety.validate_path`. Operations are confined to
  ``$HOME`` (+ a few scratch/removable roots) and a system blacklist is always
  enforced.
* **Confirm destructive actions.** ``delete`` and overwriting ``move`` are
  *two-step*: the skill describes what it is about to do and waits for a
  confirmation ("yes"/"confirm") on the next turn before acting. Deletes default
  to the Trash (recoverable) rather than permanent removal.
* **Speakable responses.** Results are short natural-language sentences suitable
  for TTS, with structured data in :attr:`SkillResult.metadata` for logging and
  tests.

The skill exposes both a natural-language entry point (:meth:`handle`, used by
the router) and precise programmatic methods (:meth:`search_files`,
:meth:`open_path`, :meth:`create_file`, :meth:`create_directory`,
:meth:`move_path`, :meth:`delete_path`) that tests and other code can call
directly.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional

from mimosa.skills.base_skill import BaseSkill, SkillResult
from mimosa.system.file_safety import (
    FileSafetyError,
    get_home_root,
    validate_path,
)

# --- File-type categories ----------------------------------------------------
# Maps a spoken category name to the file extensions it covers. Used to filter
# search results ("find my images", "search for documents").
FILE_TYPE_EXTENSIONS: Dict[str, tuple] = {
    "documents": (
        ".doc", ".docx", ".odt", ".rtf", ".txt", ".md", ".pdf", ".tex",
    ),
    "pdfs": (".pdf",),
    "images": (
        ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tiff", ".webp", ".svg", ".heic",
    ),
    "audio": (".mp3", ".wav", ".flac", ".ogg", ".m4a", ".aac", ".opus"),
    "video": (".mp4", ".mkv", ".avi", ".mov", ".webm", ".flv", ".wmv"),
    "spreadsheets": (".xls", ".xlsx", ".ods", ".csv", ".tsv"),
    "presentations": (".ppt", ".pptx", ".odp"),
    "archives": (".zip", ".tar", ".gz", ".bz2", ".xz", ".7z", ".rar"),
    "code": (
        ".py", ".js", ".ts", ".java", ".c", ".cpp", ".h", ".rs", ".go",
        ".rb", ".sh", ".html", ".css", ".json", ".yaml", ".yml", ".toml",
    ),
}

# Words that, when said on the turn after a destructive prompt, confirm it.
_CONFIRM_WORDS = re.compile(
    r"^\s*(yes|yep|yeah|yup|confirm|confirmed|do it|go ahead|proceed|sure|ok|okay|"
    r"affirmative|please do)\b",
    re.IGNORECASE,
)
_CANCEL_WORDS = re.compile(
    r"^\s*(no|nope|cancel|stop|don'?t|never ?mind|abort|forget it)\b",
    re.IGNORECASE,
)

# Default number of search hits to report.
DEFAULT_SEARCH_LIMIT = 10
# Cap on directories walked during a search, to keep voice latency bounded.
_MAX_WALK_ENTRIES = 50_000


@dataclass
class FileMatch:
    """A single search hit.

    Attributes:
        path: Absolute path to the matched file/folder.
        is_dir: ``True`` if the match is a directory.
        size: Size in bytes (0 for directories).
        modified: Last-modified time (epoch seconds).
        preview: Optional first-line content preview (text files only).
    """

    path: Path
    is_dir: bool
    size: int
    modified: float
    preview: Optional[str] = None

    def human_size(self) -> str:
        """Render the size in human-friendly units (e.g. ``1.2 MB``)."""
        if self.is_dir:
            return "folder"
        size = float(self.size)
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if size < 1024 or unit == "TB":
                return f"{int(size)} {unit}" if unit == "B" else f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} TB"

    def human_modified(self) -> str:
        """Render the modification date as ``YYYY-MM-DD``."""
        try:
            return datetime.fromtimestamp(self.modified).strftime("%Y-%m-%d")
        except (OverflowError, OSError, ValueError):  # pragma: no cover
            return "unknown"


@dataclass
class _PendingAction:
    """A destructive action awaiting user confirmation."""

    description: str
    execute: Callable[[], SkillResult]
    created: float = field(default_factory=time.time)


def _default_opener(path: str) -> int:
    """Launch ``path`` with the desktop default app via ``xdg-open``.

    Returns the subprocess return code. Separated out so tests can inject a
    fake opener instead of spawning real applications.
    """
    proc = subprocess.run(  # noqa: S603 - fixed argv, no shell
        ["xdg-open", path],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return proc.returncode


class FileOperationsSkill(BaseSkill):
    """Search and manage files/folders by voice, safely and locally."""

    name = "file_ops"
    intents = ["file_ops", "file"]
    uses_llm = False

    def __init__(
        self,
        llm_provider=None,
        *,
        opener: Optional[Callable[[str], int]] = None,
        trash: Optional[Callable[[str], None]] = None,
        search_limit: int = DEFAULT_SEARCH_LIMIT,
    ) -> None:
        """Construct the skill.

        Args:
            llm_provider: Unused (kept for a uniform skill constructor); this
                skill is fully local.
            opener: Callable used to open a file with its default app. Defaults
                to :func:`_default_opener` (``xdg-open``). Injectable for tests.
            trash: Callable used to send a path to the Trash. Defaults to
                ``send2trash.send2trash``. Injectable for tests.
            search_limit: Default maximum number of search results to report.
        """
        super().__init__(llm_provider=llm_provider)
        self._opener = opener or _default_opener
        self._trash = trash if trash is not None else self._resolve_trash()
        self.search_limit = search_limit
        self._pending: Optional[_PendingAction] = None

    @staticmethod
    def _resolve_trash() -> Optional[Callable[[str], None]]:
        """Return ``send2trash`` if installed, else ``None`` (lazy import)."""
        try:
            from send2trash import send2trash  # type: ignore

            return send2trash
        except Exception:  # noqa: BLE001 - optional dependency
            return None

    # ------------------------------------------------------------------
    # Natural-language entry point (used by the IntentRouter)
    # ------------------------------------------------------------------

    def handle(self, text: str, context: Optional[List] = None) -> SkillResult:
        """Parse a natural-language file command and dispatch to an operation."""
        utterance = (text or "").strip()
        lowered = utterance.lower()

        # 1) Resolve a pending destructive confirmation, if any.
        if self._pending is not None:
            return self._resolve_pending(lowered)

        if not lowered:
            return self._fail("I didn't catch a file command. Try 'find my notes' "
                              "or 'create a folder called Projects'.")

        # 2) Detect the operation. Order matters: destructive verbs first.
        if re.search(r"\b(delete|remove|trash|erase|get rid of)\b", lowered):
            return self._parse_delete(utterance, lowered)
        if re.search(r"\b(move|rename|relocate)\b", lowered):
            return self._parse_move(utterance, lowered)
        if re.search(r"\b(create|make|new)\b", lowered):
            return self._parse_create(utterance, lowered)
        if re.search(r"\b(open|launch|show me|display)\b", lowered):
            return self._parse_open(utterance, lowered)
        if re.search(r"\b(find|search|locate|look for|where is|where'?s|list)\b", lowered):
            return self._parse_search(utterance, lowered)

        return self._fail(
            "I'm not sure what file operation you mean. I can find, open, "
            "create, move, or delete files and folders."
        )

    # ------------------------------------------------------------------
    # Confirmation handling
    # ------------------------------------------------------------------

    def _resolve_pending(self, lowered: str) -> SkillResult:
        """Apply the user's yes/no to a queued destructive action."""
        pending = self._pending
        if _CONFIRM_WORDS.match(lowered):
            self._pending = None
            return pending.execute()
        if _CANCEL_WORDS.match(lowered):
            self._pending = None
            return SkillResult(
                text="Okay, I've cancelled that. Nothing was changed.",
                skill=self.name,
                metadata={"operation": "cancel"},
            )
        # Anything else: keep waiting, re-prompt once.
        return SkillResult(
            text=(
                f"I still need a yes or no. {pending.description} "
                "Say 'yes' to proceed or 'no' to cancel."
            ),
            success=False,
            skill=self.name,
            metadata={"operation": "awaiting_confirmation"},
        )

    def _queue(self, description: str, execute: Callable[[], SkillResult]) -> SkillResult:
        """Store a destructive action and prompt the user to confirm it."""
        self._pending = _PendingAction(description=description, execute=execute)
        return SkillResult(
            text=f"{description} Say 'yes' to confirm or 'no' to cancel.",
            success=True,
            skill=self.name,
            metadata={"operation": "confirm_required", "pending": description},
        )

    # ------------------------------------------------------------------
    # Programmatic operations (also directly unit-testable)
    # ------------------------------------------------------------------

    def search_files(
        self,
        pattern: str = "",
        *,
        file_type: Optional[str] = None,
        root: Optional[str] = None,
        limit: Optional[int] = None,
        with_preview: bool = False,
    ) -> List[FileMatch]:
        """Search the sandbox for files/folders matching ``pattern``/``file_type``.

        Args:
            pattern: Case-insensitive substring matched against the file *name*.
                Empty string matches everything (useful with ``file_type``).
            file_type: One of :data:`FILE_TYPE_EXTENSIONS` keys to filter by
                extension category (e.g. ``"images"``).
            root: Directory to search under. Defaults to the sandbox home root.
                Must itself be inside the sandbox.
            limit: Maximum number of results (defaults to :attr:`search_limit`).
            with_preview: If ``True``, attach a short first-line preview for
                text-like files.

        Returns:
            Up to ``limit`` :class:`FileMatch` objects, newest-modified first.

        Raises:
            FileSafetyError: If ``root`` is outside the sandbox.
        """
        limit = limit or self.search_limit
        decision = validate_path(root) if root else None
        if decision is not None and not decision.allowed:
            raise FileSafetyError(decision.message, reason=decision.reason)
        base = decision.path if decision is not None else get_home_root()

        pattern_l = (pattern or "").lower().strip()
        exts = FILE_TYPE_EXTENSIONS.get((file_type or "").lower()) if file_type else None

        matches: List[FileMatch] = []
        walked = 0
        for dirpath, dirnames, filenames in os.walk(base):
            # Skip hidden directories (dotfiles) to avoid config noise.
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            for name in list(dirnames) + filenames:
                walked += 1
                if walked > _MAX_WALK_ENTRIES:  # pragma: no cover - safety cap
                    break
                full = Path(dirpath) / name
                if not self._name_matches(name, pattern_l, exts, full.is_dir()):
                    continue
                try:
                    st = full.stat()
                except OSError:
                    continue
                is_dir = full.is_dir()
                preview = (
                    self._preview(full)
                    if (with_preview and not is_dir)
                    else None
                )
                matches.append(
                    FileMatch(
                        path=full,
                        is_dir=is_dir,
                        size=0 if is_dir else st.st_size,
                        modified=st.st_mtime,
                        preview=preview,
                    )
                )
            if walked > _MAX_WALK_ENTRIES:  # pragma: no cover
                break

        matches.sort(key=lambda m: m.modified, reverse=True)
        return matches[:limit]

    @staticmethod
    def _name_matches(name: str, pattern_l: str, exts, is_dir: bool) -> bool:
        """Decide whether a single entry name passes the search filters."""
        if exts is not None:
            if is_dir:
                return False
            if not name.lower().endswith(exts):
                return False
        if pattern_l and pattern_l not in name.lower():
            return False
        return True

    @staticmethod
    def _preview(path: Path, max_chars: int = 120) -> Optional[str]:
        """Return a short first-line preview for text files, else ``None``."""
        try:
            with path.open("r", encoding="utf-8", errors="ignore") as fh:
                line = fh.readline().strip()
            return (line[:max_chars] + "…") if len(line) > max_chars else line or None
        except (OSError, UnicodeError):
            return None

    def open_path(self, raw_path: str) -> SkillResult:
        """Open a file/folder with the desktop default application."""
        decision = validate_path(raw_path)
        if not decision.allowed:
            return self._fail(decision.message, operation="open")
        path = decision.path
        if not path.exists():
            return self._fail(
                f"I couldn't find {path.name}. Try searching for it first.",
                operation="open",
            )
        try:
            code = self._opener(str(path))
        except FileNotFoundError:
            return self._fail(
                "I couldn't open that because no desktop opener is available.",
                operation="open",
            )
        if code != 0:
            return self._fail(
                f"I tried to open {path.name} but the system reported an error.",
                operation="open",
            )
        return SkillResult(
            text=f"Opening {path.name}.",
            skill=self.name,
            metadata={"operation": "open", "path": str(path)},
        )

    def create_directory(self, raw_path: str) -> SkillResult:
        """Create an (empty) directory, including parents, inside the sandbox."""
        decision = validate_path(raw_path)
        if not decision.allowed:
            return self._fail(decision.message, operation="create")
        path = decision.path
        if path.exists():
            return self._fail(
                f"A folder named {path.name} already exists there.",
                operation="create",
            )
        try:
            path.mkdir(parents=True, exist_ok=False)
        except OSError as exc:
            return self._fail(f"I couldn't create that folder: {exc}.", operation="create")
        return SkillResult(
            text=f"Created the folder {path.name}.",
            skill=self.name,
            metadata={"operation": "create", "kind": "directory", "path": str(path)},
        )

    def create_file(self, raw_path: str, content: str = "") -> SkillResult:
        """Create a new file with optional ``content`` inside the sandbox."""
        decision = validate_path(raw_path)
        if not decision.allowed:
            return self._fail(decision.message, operation="create")
        path = decision.path
        if path.exists():
            return self._fail(
                f"A file named {path.name} already exists there.",
                operation="create",
            )
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        except OSError as exc:
            return self._fail(f"I couldn't create that file: {exc}.", operation="create")
        return SkillResult(
            text=f"Created the file {path.name}.",
            skill=self.name,
            metadata={
                "operation": "create",
                "kind": "file",
                "path": str(path),
                "bytes": len(content.encode("utf-8")),
            },
        )

    def move_path(self, raw_src: str, raw_dst: str, *, overwrite: bool = False) -> SkillResult:
        """Move or rename ``raw_src`` to ``raw_dst`` with conflict detection.

        Both endpoints must be inside the sandbox. If the destination exists and
        ``overwrite`` is ``False`` the move is refused (the NL layer turns this
        into a confirmation prompt instead).
        """
        src_dec = validate_path(raw_src)
        if not src_dec.allowed:
            return self._fail(src_dec.message, operation="move")
        dst_dec = validate_path(raw_dst)
        if not dst_dec.allowed:
            return self._fail(dst_dec.message, operation="move")

        src, dst = src_dec.path, dst_dec.path
        if not src.exists():
            return self._fail(f"I couldn't find {src.name} to move.", operation="move")

        # If the destination is an existing directory, move *into* it.
        if dst.is_dir():
            dst = dst / src.name

        if dst.exists() and not overwrite:
            return self._fail(
                f"Something named {dst.name} already exists at the destination.",
                operation="move",
                extra={"conflict": True, "src": str(src), "dst": str(dst)},
            )
        try:
            if dst.exists() and overwrite:
                if dst.is_dir():
                    shutil.rmtree(dst)
                else:
                    dst.unlink()
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst))
        except OSError as exc:
            return self._fail(f"I couldn't move that: {exc}.", operation="move")

        renamed = src.parent == dst.parent
        verb = "Renamed" if renamed else "Moved"
        return SkillResult(
            text=f"{verb} {src.name} to {dst.name}.",
            skill=self.name,
            metadata={"operation": "move", "src": str(src), "dst": str(dst)},
        )

    def delete_path(self, raw_path: str, *, permanent: bool = False) -> SkillResult:
        """Delete a file/folder -- to Trash by default, or permanently.

        Trash uses :mod:`send2trash` so the user can recover mistakes. Permanent
        deletion uses :func:`os.remove`/:func:`shutil.rmtree`.
        """
        decision = validate_path(raw_path)
        if not decision.allowed:
            return self._fail(decision.message, operation="delete")
        path = decision.path
        if not path.exists():
            return self._fail(f"I couldn't find {path.name} to delete.", operation="delete")

        if permanent or self._trash is None:
            try:
                if path.is_dir():
                    shutil.rmtree(path)
                else:
                    path.unlink()
            except OSError as exc:
                return self._fail(f"I couldn't delete that: {exc}.", operation="delete")
            note = "permanently deleted" if permanent else "deleted"
            return SkillResult(
                text=f"I've {note} {path.name}.",
                skill=self.name,
                metadata={"operation": "delete", "permanent": True, "path": str(path)},
            )

        try:
            self._trash(str(path))
        except Exception as exc:  # noqa: BLE001 - send2trash raises broad errors
            return self._fail(f"I couldn't move that to the Trash: {exc}.", operation="delete")
        return SkillResult(
            text=f"I've moved {path.name} to the Trash. You can recover it if needed.",
            skill=self.name,
            metadata={"operation": "delete", "permanent": False, "path": str(path)},
        )

    # ------------------------------------------------------------------
    # Natural-language parsers -> operations
    # ------------------------------------------------------------------

    def _parse_search(self, utterance: str, lowered: str) -> SkillResult:
        """Turn 'find my budget spreadsheet' into a :meth:`search_files` call."""
        file_type = self._detect_file_type(lowered)
        pattern = self._extract_search_term(utterance, lowered, file_type)
        try:
            results = self.search_files(
                pattern=pattern, file_type=file_type, with_preview=False
            )
        except FileSafetyError as exc:
            return self._fail(str(exc), operation="search")

        if not results:
            what = f" matching '{pattern}'" if pattern else ""
            cat = f" {file_type}" if file_type else ""
            return SkillResult(
                text=f"I couldn't find any{cat} files{what} in your home folder.",
                success=True,
                skill=self.name,
                metadata={"operation": "search", "count": 0, "pattern": pattern,
                          "file_type": file_type},
            )

        home = get_home_root()
        lines = []
        for m in results:
            try:
                rel = m.path.relative_to(home)
            except ValueError:
                rel = m.path
            lines.append(f"{rel} ({m.human_size()}, modified {m.human_modified()})")

        top = results[0]
        try:
            top_rel = top.path.relative_to(home)
        except ValueError:
            top_rel = top.path
        summary = (
            f"I found {len(results)} "
            f"{'result' if len(results) == 1 else 'results'}. "
            f"The most recent is {top_rel}."
        )
        return SkillResult(
            text=summary,
            skill=self.name,
            metadata={
                "operation": "search",
                "count": len(results),
                "pattern": pattern,
                "file_type": file_type,
                "results": [str(m.path) for m in results],
                "details": lines,
            },
        )

    def _parse_open(self, utterance: str, lowered: str) -> SkillResult:
        target = self._extract_target(utterance, lowered, ("open", "launch", "show me", "display"))
        if not target:
            return self._fail("Which file should I open?", operation="open")
        return self.open_path(target)

    def _parse_create(self, utterance: str, lowered: str) -> SkillResult:
        is_dir = bool(re.search(r"\b(folder|directory|dir)\b", lowered))
        name = self._extract_named_target(utterance)
        if not name:
            name = self._extract_target(utterance, lowered, ("create", "make", "new", "folder",
                                                              "directory", "file", "called", "named"))
        if not name:
            kind = "folder" if is_dir else "file"
            return self._fail(f"What should I name the {kind}?", operation="create")
        # Honour an explicit "in <folder>" location that the named-target
        # extractor may have stopped short of (e.g. "create report.txt in Docs").
        if "/" not in name:
            loc = re.search(
                r"\b(?:in|inside|under|within)\s+(?:the\s+|my\s+)?(.+?)(?:\s+(?:folder|directory))?[.?!]?$",
                utterance,
                re.IGNORECASE,
            )
            if loc:
                location = self._clean_token(loc.group(1))
                if location and location.lower() not in name.lower():
                    name = str(Path(location) / name)
        if is_dir:
            return self.create_directory(name)
        return self.create_file(name)

    def _parse_move(self, utterance: str, lowered: str) -> SkillResult:
        # Patterns: "move A to B", "rename A to B".
        match = re.search(
            r"\b(?:move|rename|relocate)\b\s+(.+?)\s+(?:to|into|as)\s+(.+)$",
            utterance,
            re.IGNORECASE,
        )
        if not match:
            return self._fail(
                "Tell me what to move and where, like 'move report.txt to Documents'.",
                operation="move",
            )
        src = self._reorder_location(self._clean_token(match.group(1)))
        dst = self._reorder_location(self._clean_token(match.group(2)))

        # Pre-check for a conflict so we can ask before overwriting.
        try:
            dst_dec = validate_path(dst)
            src_dec = validate_path(src)
        except FileSafetyError as exc:
            return self._fail(str(exc), operation="move")
        if dst_dec.allowed and src_dec.allowed:
            final_dst = dst_dec.path
            if final_dst.is_dir():
                final_dst = final_dst / src_dec.path.name
            if final_dst.exists():
                return self._queue(
                    f"{final_dst.name} already exists. I'll overwrite it by moving "
                    f"{src_dec.path.name} there.",
                    lambda: self.move_path(src, dst, overwrite=True),
                )
        return self.move_path(src, dst)

    def _parse_delete(self, utterance: str, lowered: str) -> SkillResult:
        permanent = bool(re.search(r"\b(permanent(ly)?|forever|for good)\b", lowered))
        target = self._extract_target(
            utterance, lowered,
            ("delete", "remove", "trash", "erase", "get rid of", "permanently", "forever"),
        )
        if not target:
            return self._fail("Which file or folder should I delete?", operation="delete")

        decision = validate_path(target)
        if not decision.allowed:
            return self._fail(decision.message, operation="delete")
        path = decision.path
        if not path.exists():
            return self._fail(f"I couldn't find {path.name} to delete.", operation="delete")

        kind = "folder" if path.is_dir() else "file"
        if permanent or decision.sensitive:
            how = "permanently delete" if permanent else "delete"
            warn = " This is a sensitive location." if decision.sensitive else ""
            return self._queue(
                f"I'm about to {how} the {kind} {path.name}.{warn}",
                lambda: self.delete_path(target, permanent=permanent),
            )
        return self._queue(
            f"I'm about to move the {kind} {path.name} to the Trash.",
            lambda: self.delete_path(target, permanent=False),
        )

    # ------------------------------------------------------------------
    # NL extraction helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _detect_file_type(lowered: str) -> Optional[str]:
        """Map words like 'images'/'photos'/'pdfs' to a file-type category."""
        synonyms = {
            "documents": ("document", "documents", "doc", "docs"),
            "pdfs": ("pdf", "pdfs"),
            "images": ("image", "images", "photo", "photos", "picture", "pictures"),
            "audio": ("audio", "song", "songs", "music", "track", "tracks"),
            "video": ("video", "videos", "movie", "movies", "clip", "clips"),
            "spreadsheets": ("spreadsheet", "spreadsheets", "excel", "csv"),
            "presentations": ("presentation", "presentations", "slideshow", "powerpoint"),
            "archives": ("archive", "archives", "zip", "zips"),
            "code": ("code", "script", "scripts", "source"),
        }
        for category, words in synonyms.items():
            if any(re.search(rf"\b{re.escape(w)}\b", lowered) for w in words):
                return category
        return None

    def _extract_search_term(self, utterance: str, lowered: str,
                             file_type: Optional[str]) -> str:
        """Extract the filename substring to search for from a query."""
        quoted = self._extract_named_target(utterance)
        if quoted:
            return quoted
        # Strip leading command verbs and trailing type words.
        term = re.sub(
            r"^\s*(can you |could you |please )?(find|search( for)?|locate|look for|"
            r"where('?s| is)|list|show me)\b",
            "",
            utterance,
            flags=re.IGNORECASE,
        ).strip()
        term = re.sub(r"^(my|the|a|an|all|any|some)\s+", "", term, flags=re.IGNORECASE).strip()
        term = re.sub(
            r"\b(files?|folders?|documents?|images?|photos?|pictures?|videos?|"
            r"songs?|music|spreadsheets?|pdfs?|in my home( folder)?|on my computer)\b",
            "",
            term,
            flags=re.IGNORECASE,
        ).strip()
        term = self._clean_token(term)
        # If we only had a type word ("find my images"), search by type alone.
        if file_type and len(term) <= 1:
            return ""
        return term

    def _extract_target(self, utterance: str, lowered: str, verbs) -> str:
        """Extract a file/path token after one of the given command ``verbs``."""
        quoted = self._extract_named_target(utterance)
        if quoted:
            return quoted
        verb_alt = "|".join(re.escape(v) for v in verbs)
        # ``^.*`` is greedy so we capture what follows the *last* command verb
        # (e.g. "permanently delete X" -> "X", not "delete X").
        match = re.search(rf"^.*\b(?:{verb_alt})\b\s+(.+)$", utterance, re.IGNORECASE)
        token = match.group(1) if match else utterance
        token = re.sub(r"^(the|my|a|an|this|that)\s+", "", token, flags=re.IGNORECASE).strip()
        token = re.sub(
            r"\b(file|folder|directory|please|for me|now)\b\.?$", "", token, flags=re.IGNORECASE
        ).strip()
        return self._reorder_location(self._clean_token(token))

    @staticmethod
    def _reorder_location(token: str) -> str:
        """Convert 'name in/inside/under folder' phrasing into 'folder/name'.

        Voice users naturally say "delete notes.txt in Projects"; on disk that
        means ``Projects/notes.txt``. Absolute/already-pathed tokens are left
        untouched.
        """
        if not token:
            return token
        m = re.search(r"^(.*?)\s+(?:in|inside|under|within)\s+(.+)$", token, re.IGNORECASE)
        if not m:
            return token
        name, location = m.group(1).strip(), m.group(2).strip()
        location = re.sub(r"^(the|my)\s+", "", location, flags=re.IGNORECASE).strip()
        location = re.sub(r"\s+(folder|directory)$", "", location, flags=re.IGNORECASE).strip()
        if not name or not location:
            return token
        return str(Path(location) / name)

    @staticmethod
    def _extract_named_target(utterance: str) -> str:
        """Pull a quoted name or a 'called X'/'named X' token from the utterance."""
        # Quoted: "report.txt" or 'My Folder'.
        q = re.search(r"[\"'“”‘’]([^\"'“”‘’]+)[\"'“”‘’]", utterance)
        if q:
            return q.group(1).strip()
        # called/named X (X up to end or a stop word).
        m = re.search(
            r"\b(?:called|named|titled)\s+(.+?)(?:\s+(?:in|under|inside|within|to|with)\b|[?!]|$)",
            utterance,
            re.IGNORECASE,
        )
        if m:
            return FileOperationsSkill._clean_token(m.group(1))
        return ""

    @staticmethod
    def _clean_token(token: str) -> str:
        """Trim trailing punctuation/filler from an extracted path token."""
        token = (token or "").strip()
        token = re.sub(r"[\s.?!,]+$", "", token)
        token = token.strip("\"'“”‘’ ")
        return token

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _fail(self, message: str, *, operation: str = "unknown",
              extra: Optional[dict] = None) -> SkillResult:
        """Build a failed :class:`SkillResult` with operation metadata."""
        meta = {"operation": operation}
        if extra:
            meta.update(extra)
        return SkillResult(text=message, success=False, skill=self.name, metadata=meta)

    def has_pending_confirmation(self) -> bool:
        """Return ``True`` while a destructive action awaits yes/no.

        Lets the router send the next utterance straight back here so a bare
        "yes"/"no" resolves the queued delete/overwrite.
        """
        return self._pending is not None

    def _error_message(self) -> str:
        return "Sorry, I ran into a problem with that file operation."

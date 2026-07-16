"""Desktop application registry for MimOSA (M2.2).

This module discovers the applications installed on a Linux desktop by parsing
the freedesktop ``.desktop`` entries that live in the standard application
directories (``/usr/share/applications`` and
``~/.local/share/applications``). It builds a small, searchable in-memory
catalog so the :class:`~mimosa.skills.application.ApplicationSkill` can answer
voice commands like "open Firefox", "launch my text editor", or "what
browsers do I have?".

Design principles
-----------------
* **Local & private.** Discovery reads only on-device ``.desktop`` files with
  :mod:`configparser`; nothing is sent to the network or an LLM.
* **Robust parsing.** Real systems contain malformed, duplicated, and hidden
  ``.desktop`` files. Every entry is parsed defensively -- a single bad file
  never breaks the catalog; it is simply skipped (and logged at debug level).
* **Lazy + cached.** The catalog is built on first use and cached. Call
  :meth:`AppRegistry.refresh` to rebuild it (e.g. after installing an app).
* **Fuzzy matching.** Voice transcripts are imperfect ("libre office" vs
  "LibreOffice"), so lookups use a blend of exact, substring, and
  :mod:`difflib` ratio matching to find the best application.

Field handling follows the Desktop Entry Specification: the ``Exec`` key's
field codes (``%u``, ``%f``, ``%U``, ``%F``, ``%i``, ``%c``, ``%k`` ...) are
stripped so the command can be executed directly, and ``NoDisplay``/``Hidden``/
``Type`` are honoured so we only surface real, launchable GUI apps.

Testing
-------
The application search directories can be overridden with the
``MIMOSA_APP_DIRS`` environment variable (a ``os.pathsep``-separated list),
mirroring the ``MIMOSA_FILE_ROOT`` override used by the file-safety layer. This
keeps the unit tests fully hermetic -- they point the registry at a temp dir
full of fixture ``.desktop`` files.
"""

from __future__ import annotations

import configparser
import logging
import os
import re
import shlex
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger("mimosa.system.app_registry")

# Standard freedesktop locations for application entries. The user-local
# directory takes precedence over the system one when the same app id appears
# in both (later entries overwrite earlier ones during the build).
DEFAULT_SYSTEM_APP_DIRS: Tuple[str, ...] = (
    "/usr/share/applications",
    "/usr/local/share/applications",
    "/var/lib/flatpak/exports/share/applications",
    "/var/lib/snapd/desktop/applications",
)

#: Field codes defined by the Desktop Entry Spec that must be removed from the
#: ``Exec`` line before the command can be run directly.
_FIELD_CODE_RE = re.compile(r"%[fFuUdDnNickvm]")

#: Minimum similarity (0..1) for a fuzzy match to be considered at all.
DEFAULT_MATCH_THRESHOLD = 0.6

# Common spoken app names that don't match .desktop Name fields directly.
# Maps a normalised spoken name to a list of app-id substrings (most preferred
# first). Used as a fallback when the standard fuzzy matcher scores below
# threshold.
_COMMON_ALIASES: dict[str, list[str]] = {
    "file manager": ["dolphin", "nautilus", "thunar", "nemo", "pcmanfm"],
    "files": ["dolphin", "nautilus", "thunar", "nemo", "pcmanfm"],
    "terminal": ["konsole", "gnome-terminal", "xterm", "tilix", "alacritty", "kitty"],
    "text editor": ["kate", "gedit", "geany", "mousepad", "pluma", "xed"],
    "editor": ["kate", "gedit", "geany", "mousepad", "pluma", "xed"],
    "browser": ["firefox", "chromium", "brave", "google-chrome"],
    "web browser": ["firefox", "chromium", "brave", "google-chrome"],
    "email": ["thunderbird", "evolution", "kmail", "geary"],
    "mail": ["thunderbird", "evolution", "kmail", "geary"],
    "calculator": ["kcalc", "gnome-calculator", "galculator"],
    "image viewer": ["gwenview", "eog", "shotwell", "ristretto"],
    "photo viewer": ["gwenview", "eog", "shotwell", "ristretto"],
    "music player": ["elisa", "rhythmbox", "clementine", "audacious", "amarok"],
    "video player": ["vlc", "mpv", "totem", "dragon"],
    "media player": ["vlc", "mpv", "totem", "dragon"],
    "system settings": ["systemsettings", "gnome-control-center"],
    "settings": ["systemsettings", "gnome-control-center"],
    "archive manager": ["ark", "file-roller", "engrampa"],
    "screenshot": ["spectacle", "gnome-screenshot", "flameshot"],
    "pdf viewer": ["okular", "evince", "mupdf"],
    "document viewer": ["okular", "evince"],
    "office": ["libreoffice"],
    "word processor": ["libreoffice", "abiword"],
    "spreadsheet": ["libreoffice-calc", "gnumeric"],
    "presentation": ["libreoffice-impress"],
    "disk usage": ["filelight", "baobab"],
    "task manager": ["ksysguard", "gnome-system-monitor", "htop"],
    "process manager": ["ksysguard", "gnome-system-monitor"],
    "software center": ["discover", "gnome-software"],
    "app store": ["discover", "gnome-software"],
    "package manager": ["discover", "gnome-software", "synaptic"],
}


@dataclass
class AppEntry:
    """A single launchable desktop application.

    Attributes:
        app_id: The ``.desktop`` file stem (e.g. ``org.kde.dolphin``), a stable
            identifier for the app.
        name: Human-friendly display name (the ``Name`` key).
        exec_command: The ``Exec`` line with field codes stripped, ready to be
            split with :func:`shlex.split` and launched.
        icon: Icon name or path (the ``Icon`` key), may be empty.
        categories: The freedesktop categories (e.g. ``["Utility", "TextEditor"]``).
        comment: Short description (the ``Comment`` key), may be empty.
        terminal: ``True`` if the app must be run inside a terminal emulator.
        keywords: Extra search keywords declared by the app.
        path: Source ``.desktop`` file path (useful for diagnostics).
    """

    app_id: str
    name: str
    exec_command: str
    icon: str = ""
    categories: List[str] = field(default_factory=list)
    comment: str = ""
    terminal: bool = False
    keywords: List[str] = field(default_factory=list)
    path: Optional[Path] = None

    def argv(self) -> List[str]:
        """Return the executable command split into an argv list.

        Falls back to a naive split if :func:`shlex.split` chokes on exotic
        quoting so callers always get *something* runnable.
        """
        try:
            return shlex.split(self.exec_command)
        except ValueError:
            return self.exec_command.split()


def _candidate_dirs() -> List[Path]:
    """Return the directories to scan for ``.desktop`` files.

    Honours the ``MIMOSA_APP_DIRS`` override (``os.pathsep``-separated) used by
    tests; otherwise scans the per-user directory plus the standard system
    locations (and any extra dirs from ``$XDG_DATA_DIRS``).
    """
    override = os.getenv("MIMOSA_APP_DIRS")
    if override:
        return [Path(p).expanduser() for p in override.split(os.pathsep) if p.strip()]

    dirs: List[Path] = []

    # Per-user entries first (highest precedence on conflict).
    xdg_data_home = os.getenv("XDG_DATA_HOME")
    if xdg_data_home:
        dirs.append(Path(xdg_data_home) / "applications")
    else:
        dirs.append(Path.home() / ".local" / "share" / "applications")

    # Anything advertised via XDG_DATA_DIRS.
    xdg_data_dirs = os.getenv("XDG_DATA_DIRS", "")
    for base in xdg_data_dirs.split(os.pathsep):
        base = base.strip()
        if base:
            dirs.append(Path(base) / "applications")

    # The conventional system locations.
    for d in DEFAULT_SYSTEM_APP_DIRS:
        dirs.append(Path(d))

    # De-duplicate while preserving order.
    seen: set = set()
    unique: List[Path] = []
    for d in dirs:
        key = str(d)
        if key not in seen:
            seen.add(key)
            unique.append(d)
    return unique


def strip_field_codes(exec_line: str) -> str:
    """Remove Desktop Entry ``Exec`` field codes and tidy whitespace.

    ``%f``, ``%u`` etc. are placeholders for files/URLs the launcher would
    substitute; for a direct launch we drop them. A literal percent is encoded
    as ``%%`` -- we restore it to a single ``%``.
    """
    if not exec_line:
        return ""
    # Protect literal percent signs, strip field codes, then restore them.
    protected = exec_line.replace("%%", "\x00")
    stripped = _FIELD_CODE_RE.sub("", protected)
    stripped = stripped.replace("\x00", "%")
    # Collapse the whitespace left behind by removed codes.
    return re.sub(r"\s+", " ", stripped).strip()


def parse_desktop_file(path: Path) -> Optional[AppEntry]:
    """Parse a single ``.desktop`` file into an :class:`AppEntry`.

    Returns ``None`` (rather than raising) for files that are malformed, hidden
    (``NoDisplay``/``Hidden``), non-application types, or otherwise not
    launchable. This keeps catalog building resilient to the messy reality of a
    real applications directory.
    """
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    # Preserve key case (default lowercases); spec keys are CamelCase.
    parser.optionxform = str  # type: ignore[assignment]

    try:
        # Some .desktop files contain duplicate keys or stray content; read the
        # text ourselves so a decode error doesn't bubble up.
        text = path.read_text(encoding="utf-8", errors="replace")
        parser.read_string(text)
    except (OSError, configparser.Error, UnicodeError) as exc:
        logger.debug("Skipping unreadable .desktop file %s: %s", path, exc)
        return None

    if not parser.has_section("Desktop Entry"):
        logger.debug("No [Desktop Entry] section in %s", path)
        return None

    section = parser["Desktop Entry"]

    def get(key: str, default: str = "") -> str:
        return (section.get(key, default) or "").strip()

    # Only surface real, visible, launchable applications.
    if get("Type", "Application") != "Application":
        return None
    if get("NoDisplay").lower() == "true":
        return None
    if get("Hidden").lower() == "true":
        return None

    name = get("Name")
    exec_line = get("Exec")
    if not name or not exec_line:
        logger.debug("Missing Name/Exec in %s", path)
        return None

    exec_command = strip_field_codes(exec_line)
    if not exec_command:
        return None

    categories = [c for c in get("Categories").split(";") if c]
    keywords = [k for k in get("Keywords").split(";") if k]

    return AppEntry(
        app_id=path.stem,
        name=name,
        exec_command=exec_command,
        icon=get("Icon"),
        categories=categories,
        comment=get("Comment"),
        terminal=get("Terminal").lower() == "true",
        keywords=keywords,
        path=path,
    )


class AppRegistry:
    """A lazily-built, searchable catalog of installed desktop applications.

    The catalog is built on first access and cached. Use :meth:`refresh` to
    force a rebuild. Lookups are tolerant of imperfect speech-to-text via fuzzy
    matching.
    """

    def __init__(
        self,
        *,
        search_dirs: Optional[Sequence[Path]] = None,
        match_threshold: float = DEFAULT_MATCH_THRESHOLD,
    ) -> None:
        """Construct the registry.

        Args:
            search_dirs: Explicit directories to scan. If omitted, the standard
                freedesktop locations (honouring ``MIMOSA_APP_DIRS``) are used.
            match_threshold: Minimum fuzzy-match similarity (0..1) to accept a
                result from :meth:`find`.
        """
        self._explicit_dirs = [Path(d) for d in search_dirs] if search_dirs else None
        self.match_threshold = match_threshold
        self._apps: Optional[Dict[str, AppEntry]] = None  # keyed by app_id

    # -- building / caching -------------------------------------------------

    @property
    def search_dirs(self) -> List[Path]:
        """The directories this registry scans."""
        if self._explicit_dirs is not None:
            return list(self._explicit_dirs)
        return _candidate_dirs()

    def _ensure_loaded(self) -> Dict[str, AppEntry]:
        """Build the catalog on first use, then return the cached map."""
        if self._apps is None:
            self._apps = self._build()
        return self._apps

    def refresh(self) -> "AppRegistry":
        """Rebuild the catalog from disk, discarding the cache. Returns self."""
        self._apps = self._build()
        return self

    def _build(self) -> Dict[str, AppEntry]:
        """Scan the search directories and parse every ``.desktop`` file."""
        apps: Dict[str, AppEntry] = {}
        # Reverse order so earlier (higher-precedence) dirs win on app_id
        # collisions -- we overwrite as we go, finishing with the per-user dir.
        for directory in reversed(self.search_dirs):
            try:
                if not directory.is_dir():
                    continue
                entries = sorted(directory.glob("*.desktop"))
            except OSError as exc:  # pragma: no cover - unusual FS error
                logger.debug("Could not list %s: %s", directory, exc)
                continue
            for desktop_file in entries:
                entry = parse_desktop_file(desktop_file)
                if entry is not None:
                    apps[entry.app_id] = entry
        logger.info("App registry built: %d applications.", len(apps))
        return apps

    # -- access -------------------------------------------------------------

    def all_apps(self) -> List[AppEntry]:
        """Return every catalogued application, sorted by display name."""
        return sorted(self._ensure_loaded().values(), key=lambda a: a.name.lower())

    def __len__(self) -> int:
        return len(self._ensure_loaded())

    def get(self, app_id: str) -> Optional[AppEntry]:
        """Return the app with the given ``app_id`` (``.desktop`` stem)."""
        return self._ensure_loaded().get(app_id)

    def by_category(self, category: str) -> List[AppEntry]:
        """Return all apps whose categories include ``category`` (case-insensitive)."""
        cat = (category or "").strip().lower()
        if not cat:
            return []
        results = [
            app
            for app in self._ensure_loaded().values()
            if any(c.lower() == cat for c in app.categories)
        ]
        return sorted(results, key=lambda a: a.name.lower())

    # -- fuzzy lookup -------------------------------------------------------

    def _score(self, query: str, app: AppEntry) -> float:
        """Compute a 0..1 relevance score of ``app`` for the spoken ``query``."""
        q = query.lower().strip()
        if not q:
            return 0.0
        name = app.name.lower()
        app_id = app.app_id.lower()

        # Exact name / id matches dominate.
        if q == name or q == app_id:
            return 1.0

        best = 0.0
        # Substring containment is a strong, cheap signal.
        if q in name or name in q:
            best = max(best, 0.9)
        if q in app_id:
            best = max(best, 0.85)

        # Token overlap (e.g. "text editor" vs "KWrite Text Editor").
        q_tokens = set(re.findall(r"\w+", q))
        name_tokens = set(re.findall(r"\w+", name))
        kw_tokens = {k.lower() for k in app.keywords}
        if q_tokens:
            overlap = len(q_tokens & (name_tokens | kw_tokens)) / len(q_tokens)
            best = max(best, 0.7 * overlap + 0.1 if overlap else best)

        # difflib ratio as a fuzzy fallback for typos/mishearings.
        ratio = SequenceMatcher(None, q, name).ratio()
        best = max(best, ratio)

        # A keyword exact hit is meaningful too.
        if q in kw_tokens:
            best = max(best, 0.85)
        return best

    def find(self, query: str) -> Optional[AppEntry]:
        """Return the single best-matching app for ``query`` (or ``None``).

        Honours :attr:`match_threshold`; if nothing scores above it, returns
        ``None`` so the skill can say it couldn't find the app.
        """
        ranked = self.rank(query, limit=1)
        if ranked and ranked[0][1] >= self.match_threshold:
            return ranked[0][0]
        # Fuzzy match failed -- fall back to common KDE/GNOME spoken aliases
        # (e.g. "file manager" -> Dolphin/Nautilus) before giving up.
        return self._find_by_alias(query)

    def _find_by_alias(self, query: str) -> Optional[AppEntry]:
        """Resolve common spoken names ("file manager") to an installed app.

        Looks ``query`` up in :data:`_COMMON_ALIASES`; for a match, walks the
        prioritised list of binary/app-id tokens and returns the first installed
        app whose ``app_id`` (or exec command) contains that token. Returns
        ``None`` when the query is not a known alias or nothing is installed.
        """
        normalised = (query or "").strip().lower()
        if not normalised:
            return None
        candidates = _COMMON_ALIASES.get(normalised)
        if not candidates:
            return None
        catalog = self._ensure_loaded()
        for token in candidates:
            # Direct app_id hit (fast path).
            entry = catalog.get(token)
            if entry is not None:
                return entry
            # Otherwise scan for an entry whose app_id or exec contains the token.
            token_l = token.lower()
            for app in catalog.values():
                if token_l in app.app_id.lower() or token_l in app.exec_command.lower():
                    return app
        return None

    def rank(self, query: str, *, limit: int = 5) -> List[Tuple[AppEntry, float]]:
        """Return up to ``limit`` ``(app, score)`` pairs, best first.

        Useful for "did you mean ...?" style disambiguation and for tests.
        """
        if not (query or "").strip():
            return []
        scored = [
            (app, self._score(query, app)) for app in self._ensure_loaded().values()
        ]
        scored = [pair for pair in scored if pair[1] > 0]
        scored.sort(key=lambda pair: pair[1], reverse=True)
        return scored[:limit]

    def search(self, query: str, *, limit: int = 5) -> List[AppEntry]:
        """Return up to ``limit`` apps matching ``query`` above the threshold."""
        return [
            app
            for app, score in self.rank(query, limit=limit)
            if score >= self.match_threshold
        ]

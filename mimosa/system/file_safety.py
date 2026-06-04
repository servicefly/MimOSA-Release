"""Filesystem safety guardrails for MimOSA's file operations (M2.1).

A voice assistant that can move and delete files is powerful -- and dangerous if
it acts on a misheard command. This module centralises every safety decision so
:class:`~mimosa.skills.file_ops.FileOperationsSkill` (and any future
file-touching skill) can ask one trusted authority *"am I allowed to touch this
path?"* before doing anything.

Guardrails enforced here
------------------------
* **Home-directory restriction.** By default all operations are confined to the
  user's home directory (``$HOME``) and a small set of explicitly allowed roots
  (e.g. ``/tmp``, ``/media/$USER``, ``/mnt``). The sandbox root can be
  overridden for tests via the ``MIMOSA_FILE_ROOT`` environment variable.
* **System blacklist.** System-critical trees (``/etc``, ``/bin``, ``/sys``,
  ``/proc``, ``/boot`` ...) are *always* rejected, even if some clever symlink
  appears to place them under an allowed root -- we resolve symlinks first.
* **Hidden / dotfile protection.** Destructive operations on configuration
  dotfiles (``~/.ssh``, ``~/.config`` ...) are flagged so the skill can refuse
  or require extra confirmation.
* **Symlink resolution.** Paths are fully resolved (``Path.resolve``) before any
  check, defeating ``..`` traversal and symlink escapes.

Nothing in this module performs I/O beyond ``stat``/``resolve``; it only decides
*whether* an action is permitted. The skill performs the actual work.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

# Absolute system trees that must never be read from or written to, regardless
# of how the path was constructed. These are prefix-matched against the fully
# resolved path.
BLACKLISTED_ROOTS: Tuple[str, ...] = (
    "/etc",
    "/bin",
    "/sbin",
    "/usr",
    "/lib",
    "/lib32",
    "/lib64",
    "/libx32",
    "/sys",
    "/proc",
    "/dev",
    "/boot",
    "/root",
    "/run",
    "/var",
    "/srv",
    "/opt",
)

# Dotfile directories that hold sensitive configuration/credentials. Destructive
# operations targeting these (or anything inside them) require special care.
SENSITIVE_HOME_DIRS: Tuple[str, ...] = (
    ".ssh",
    ".gnupg",
    ".config",
    ".local",
    ".mozilla",
    ".aws",
    ".kube",
    ".docker",
)


class FileSafetyError(PermissionError):
    """Raised when a requested path violates a safety guardrail.

    Subclasses :class:`PermissionError` so callers can catch either; the
    ``reason`` attribute carries a short, speakable explanation.
    """

    def __init__(self, message: str, reason: str = "blocked") -> None:
        super().__init__(message)
        self.reason = reason


@dataclass
class SafetyDecision:
    """Outcome of validating a path.

    Attributes:
        path: The fully resolved :class:`~pathlib.Path` (symlinks/``..`` removed).
        allowed: ``True`` if the path is within the sandbox and not blacklisted.
        reason: Short machine-friendly reason when ``allowed`` is ``False``
            (``"blacklisted"``, ``"outside_sandbox"``, ``"sensitive"``).
        message: Human/speakable explanation.
        sensitive: ``True`` if the path lives under a sensitive dotfile dir.
    """

    path: Path
    allowed: bool
    reason: str = ""
    message: str = ""
    sensitive: bool = False


def get_home_root() -> Path:
    """Return the sandbox root directory.

    Honours ``MIMOSA_FILE_ROOT`` (used by tests to redirect the sandbox to a
    temp dir) and falls back to ``$HOME`` / :meth:`Path.home`.
    """
    override = os.getenv("MIMOSA_FILE_ROOT")
    if override:
        return Path(override).expanduser().resolve()
    home = os.getenv("HOME")
    if home:
        return Path(home).expanduser().resolve()
    return Path.home().resolve()


def get_allowed_roots() -> List[Path]:
    """Return every directory tree the assistant may operate within.

    This is the home root plus a few conventional removable/scratch mounts. When
    ``MIMOSA_FILE_ROOT`` is set (tests), *only* that root is allowed so the
    sandbox is hermetic.
    """
    home = get_home_root()
    if os.getenv("MIMOSA_FILE_ROOT"):
        return [home]

    roots: List[Path] = [home]
    user = os.getenv("USER") or os.getenv("LOGNAME") or ""
    candidates = [
        Path("/tmp"),
        Path("/mnt"),
        Path("/media"),
    ]
    if user:
        candidates.append(Path(f"/media/{user}"))
        candidates.append(Path(f"/run/media/{user}"))
    for cand in candidates:
        try:
            if cand.exists():
                roots.append(cand.resolve())
        except OSError:
            continue
    return roots


def _is_relative_to(path: Path, other: Path) -> bool:
    """Backport of :meth:`Path.is_relative_to` (added in 3.9) -- robust form."""
    try:
        path.relative_to(other)
        return True
    except ValueError:
        return False


def is_blacklisted(path: Path) -> bool:
    """Return ``True`` if ``path`` is within any :data:`BLACKLISTED_ROOTS` tree.

    ``path`` should already be resolved. ``/var`` etc. are matched as path
    prefixes, so ``/var/log/x`` is blacklisted but ``/variable`` is not.
    """
    # If the sandbox root itself lives under a normally-blacklisted tree (e.g.
    # tests using /tmp/... as MIMOSA_FILE_ROOT), don't blacklist within it.
    sandbox = get_home_root()
    for root in BLACKLISTED_ROOTS:
        root_path = Path(root)
        if _is_relative_to(path, root_path) or path == root_path:
            # Allow if the sandbox root is itself inside this tree and the path
            # is inside the sandbox (covers /tmp test roots, /home under /var, …).
            if _is_relative_to(sandbox, root_path) and (
                _is_relative_to(path, sandbox) or path == sandbox
            ):
                return False
            return True
    return False


def _is_sensitive(path: Path, home: Path) -> bool:
    """Return ``True`` if ``path`` is a sensitive dotfile dir or lives inside one."""
    if not (_is_relative_to(path, home) or path == home):
        return False
    try:
        rel_parts = path.relative_to(home).parts
    except ValueError:
        return False
    if not rel_parts:
        return False
    return rel_parts[0] in SENSITIVE_HOME_DIRS


def resolve_path(raw: str, base: Optional[Path] = None) -> Path:
    """Expand ``~``/env vars and fully resolve ``raw`` to an absolute path.

    Relative paths are resolved against ``base`` (defaults to the sandbox home
    root) rather than the process CWD, which keeps behaviour predictable for a
    voice command like "delete notes.txt".

    ``Path.resolve(strict=False)`` is used so the path need not exist yet (e.g.
    when creating a new file).
    """
    if raw is None:
        raise FileSafetyError("No path provided.", reason="empty")
    text = os.path.expandvars(str(raw).strip())
    if not text:
        raise FileSafetyError("No path provided.", reason="empty")

    p = Path(text).expanduser()
    if not p.is_absolute():
        base = base or get_home_root()
        p = base / p
    try:
        return p.resolve(strict=False)
    except (OSError, RuntimeError) as exc:  # pragma: no cover - exotic FS errors
        raise FileSafetyError(f"Could not resolve path: {exc}", reason="invalid") from exc


def validate_path(raw: str, *, base: Optional[Path] = None) -> SafetyDecision:
    """Validate ``raw`` against every guardrail and return a :class:`SafetyDecision`.

    This never raises for a *disallowed* path -- it returns a decision with
    ``allowed=False`` and a speakable ``message`` so the skill can respond
    naturally. It only raises :class:`FileSafetyError` for malformed input that
    cannot be resolved at all.

    Args:
        raw: The user-supplied path string.
        base: Base directory for relative paths (defaults to the sandbox root).

    Returns:
        A :class:`SafetyDecision` describing whether the path may be used.
    """
    resolved = resolve_path(raw, base=base)
    home = get_home_root()

    if is_blacklisted(resolved):
        return SafetyDecision(
            path=resolved,
            allowed=False,
            reason="blacklisted",
            message=(
                "That location is a protected system directory, so I can't "
                "touch it for safety reasons."
            ),
        )

    allowed_roots = get_allowed_roots()
    if not any(_is_relative_to(resolved, r) or resolved == r for r in allowed_roots):
        return SafetyDecision(
            path=resolved,
            allowed=False,
            reason="outside_sandbox",
            message=(
                "That path is outside your home folder, so I can't access it. "
                "I can only work inside your personal directories."
            ),
        )

    sensitive = _is_sensitive(resolved, home)
    return SafetyDecision(
        path=resolved,
        allowed=True,
        reason="",
        message="",
        sensitive=sensitive,
    )


def require_safe(raw: str, *, base: Optional[Path] = None) -> Path:
    """Validate ``raw`` and return the resolved path, raising if disallowed.

    Convenience wrapper around :func:`validate_path` for call sites that prefer
    exceptions to decision objects.

    Raises:
        FileSafetyError: If the path is blacklisted or outside the sandbox.
    """
    decision = validate_path(raw, base=base)
    if not decision.allowed:
        raise FileSafetyError(decision.message, reason=decision.reason)
    return decision.path


def is_within_sandbox(path: Path) -> bool:
    """Return ``True`` if an already-resolved ``path`` is safe to operate on."""
    if is_blacklisted(path):
        return False
    return any(
        _is_relative_to(path, r) or path == r for r in get_allowed_roots()
    )


def filter_safe(paths: Iterable[Path]) -> List[Path]:
    """Return only the paths from ``paths`` that pass the sandbox guardrails."""
    return [p for p in paths if is_within_sandbox(p)]

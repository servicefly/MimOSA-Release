"""Centralised logging configuration for MimOSA (Milestone 8.2).

MimOSA writes a single, rotating application log to a well-known location so
that users always know where to look and the file never grows without bound.

Design goals
------------
* **One documented location.** All logs land in
  :func:`mimosa.memory.paths.log_file_path` (``~/.local/share/mimosa/logs/
  mimosa.log`` by default, overridable via the ``MIMOSA_DATA``/``XDG_DATA_HOME``
  environment variables).
* **Rotation.** A :class:`~logging.handlers.RotatingFileHandler` caps the log at
  a small size with a handful of backups, so disk usage stays bounded.
* **Privacy first.** The formatter only records levels, logger names and the
  message a developer chose to emit -- never raw transcripts. Callers remain
  responsible for not logging message *content* at INFO; this module simply
  avoids adding anything that would leak it.
* **Graceful degradation.** If the log directory or file cannot be written
  (read-only home, sandbox, permissions) we silently fall back to console-only
  logging instead of crashing the app.
* **Idempotent.** Calling :func:`configure_logging` more than once will not
  attach duplicate handlers, which matters for tests and re-entrant launches.
"""

from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path
from typing import Optional

from mimosa.memory import paths

__all__ = [
    "DEFAULT_FORMAT",
    "MAX_BYTES",
    "BACKUP_COUNT",
    "configure_logging",
    "describe_log_location",
]

#: Privacy-conscious log line format -- no transcript content is included.
DEFAULT_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"

#: Rotate once the active log reaches ~1 MiB.
MAX_BYTES = 1_048_576

#: Keep a handful of rotated backups (mimosa.log.1 ... mimosa.log.3).
BACKUP_COUNT = 3

#: Marker attribute used to recognise handlers this module installed, so we can
#: stay idempotent without clobbering handlers other code may have added.
_MIMOSA_HANDLER_FLAG = "_mimosa_managed"


def _make_console_handler(level: int) -> logging.Handler:
    handler = logging.StreamHandler()
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter(DEFAULT_FORMAT))
    setattr(handler, _MIMOSA_HANDLER_FLAG, True)
    return handler


def _make_file_handler(level: int, log_path: Path) -> Optional[logging.Handler]:
    """Build a rotating file handler, or ``None`` if the path is unwritable."""
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(
            str(log_path),
            maxBytes=MAX_BYTES,
            backupCount=BACKUP_COUNT,
            encoding="utf-8",
            delay=True,  # don't open the file until something is logged
        )
    except (OSError, PermissionError):
        return None
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter(DEFAULT_FORMAT))
    setattr(handler, _MIMOSA_HANDLER_FLAG, True)
    return handler


def _remove_managed_handlers(root: logging.Logger) -> None:
    for handler in list(root.handlers):
        if getattr(handler, _MIMOSA_HANDLER_FLAG, False):
            root.removeHandler(handler)
            try:
                handler.close()
            except Exception:  # pragma: no cover - defensive
                pass


def configure_logging(
    *,
    verbose: bool = False,
    to_file: bool = True,
    log_path: Optional[Path] = None,
) -> Path | None:
    """Configure root logging for MimOSA.

    Parameters
    ----------
    verbose:
        When ``True`` the level is ``DEBUG``; otherwise ``INFO``.
    to_file:
        When ``True`` (default) attach a rotating file handler in addition to
        the console handler. When ``False`` only the console handler is used.
    log_path:
        Override the log file location (mainly for tests). Defaults to
        :func:`mimosa.memory.paths.log_file_path`.

    Returns
    -------
    pathlib.Path | None
        The active log file path, or ``None`` if file logging was disabled or
        could not be initialised (console-only fallback).

    The function is idempotent: handlers installed by a previous call are
    removed before new ones are attached, so repeated calls never duplicate
    output.
    """
    level = logging.DEBUG if verbose else logging.INFO
    root = logging.getLogger()
    root.setLevel(level)

    # Drop any handlers we previously installed so we never stack duplicates.
    _remove_managed_handlers(root)

    root.addHandler(_make_console_handler(level))

    active_path: Path | None = None
    if to_file:
        target = Path(log_path) if log_path is not None else paths.log_file_path()
        file_handler = _make_file_handler(level, target)
        if file_handler is not None:
            root.addHandler(file_handler)
            active_path = target
        else:
            # Degrade gracefully: warn once on the console, keep running.
            logging.getLogger(__name__).warning(
                "Could not open log file at %s; logging to console only.", target
            )

    return active_path


def describe_log_location(log_path: Optional[Path] = None) -> str:
    """Return a human-readable description of where logs are written."""
    target = Path(log_path) if log_path is not None else paths.log_file_path()
    return f"Logs are written to {target} (rotated at ~1 MB, {BACKUP_COUNT} backups)."

"""Filesystem path resolution for MimOSA's persistent memory (M5).

All long-term memory lives on the user's device under a single data directory,
mirroring the privacy-first, local-only posture of :mod:`mimosa.utils.config`
(which resolves *configuration* paths). Nothing here ever touches the network.

Resolution order for the data directory (highest precedence first):

1. ``MIMOSA_DATA`` environment variable (used by tests and power users).
2. ``$XDG_DATA_HOME/mimosa`` (freedesktop base-directory spec).
3. ``~/.local/share/mimosa`` (the XDG default).

The data directory is distinct from the *config* directory
(``~/.config/mimosa``): config holds small, user-tunable JSON settings while
data holds the (potentially large) SQLite databases and vector stores produced
by the memory subsystem.
"""

from __future__ import annotations

import os
from pathlib import Path

#: Canonical file names for the memory databases / stores.
CONVERSATIONS_DB = "conversations.db"
PREFERENCES_DB = "preferences.db"
PRIVATE_DB = "private.db"
SEMANTIC_DIR = "semantic"


def default_data_dir() -> Path:
    """Return the base directory for MimOSA's on-device data.

    Honors ``MIMOSA_DATA`` then ``XDG_DATA_HOME`` then falls back to
    ``~/.local/share/mimosa``. The directory is *not* created here; callers
    that write create it lazily (so read-only callers never have side effects).
    """
    override = os.environ.get("MIMOSA_DATA")
    if override:
        return Path(override).expanduser()
    xdg = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg).expanduser() if xdg else Path.home() / ".local" / "share"
    return base / "mimosa"


def conversations_db_path() -> Path:
    """Absolute path to the conversation-history SQLite database (M5.1)."""
    return default_data_dir() / CONVERSATIONS_DB


def preferences_db_path() -> Path:
    """Absolute path to the learned-preferences SQLite database (M5.2)."""
    return default_data_dir() / PREFERENCES_DB


def private_db_path() -> Path:
    """Absolute path to the encrypted private-conversation database (M5.4)."""
    return default_data_dir() / PRIVATE_DB


def semantic_store_dir() -> Path:
    """Directory for the semantic (vector) memory store (M5.3)."""
    return default_data_dir() / SEMANTIC_DIR


def ensure_data_dir() -> Path:
    """Create (if needed) and return the base data directory."""
    d = default_data_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d

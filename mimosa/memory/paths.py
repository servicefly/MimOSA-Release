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
TASKS_DB = "tasks.db"
#: Sub-directory holding the Chroma vector store for the onboarding / profile
#: memory system (M6). Kept separate from the M5 ``semantic`` store so the two
#: subsystems never collide.
MEMORY_DIR = "memory"
CHROMA_DIR = "chroma"
#: File names for the structured user profile and the resumable onboarding
#: conversation state (both small JSON documents).
PROFILE_FILE = "profile.json"
ONBOARDING_STATE_FILE = "onboarding_state.json"
#: File names for the continuous-learning subsystem (M4). All small, local-only
#: JSON documents living alongside the profile in the ``memory`` directory.
PATTERNS_FILE = "patterns.json"
PROACTIVE_QUESTIONS_FILE = "proactive_questions.json"
RELATIONSHIP_FILE = "relationship.json"
#: Sub-directory (under the data dir) and file name for application logs (M8.2).
LOGS_DIR = "logs"
LOG_FILE = "mimosa.log"


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


def tasks_db_path() -> Path:
    """Absolute path to the background-task-queue SQLite database (M7.1)."""
    return default_data_dir() / TASKS_DB


def memory_dir() -> Path:
    """Base directory for the onboarding / profile memory subsystem (M6)."""
    return default_data_dir() / MEMORY_DIR


def memory_chroma_dir() -> Path:
    """Directory for the onboarding memory's Chroma vector store (M6).

    Resolves to ``<data>/memory/chroma`` (e.g.
    ``~/.local/share/mimosa/memory/chroma``).
    """
    return memory_dir() / CHROMA_DIR


def profile_path() -> Path:
    """Absolute path to the structured user-profile JSON document (M6)."""
    return memory_dir() / PROFILE_FILE


def onboarding_state_path() -> Path:
    """Absolute path to the resumable onboarding conversation state (M6)."""
    return memory_dir() / ONBOARDING_STATE_FILE


def patterns_path() -> Path:
    """Absolute path to the behavioural-pattern state document (M4)."""
    return memory_dir() / PATTERNS_FILE


def proactive_questions_path() -> Path:
    """Absolute path to the proactive-question tracking document (M4)."""
    return memory_dir() / PROACTIVE_QUESTIONS_FILE


def relationship_path() -> Path:
    """Absolute path to the relationship-depth tracking document (M4)."""
    return memory_dir() / RELATIONSHIP_FILE


def ensure_memory_dir() -> Path:
    """Create (if needed) and return the onboarding memory directory (M6)."""
    d = memory_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d


def log_dir() -> Path:
    """Directory for MimOSA's rotating application logs (M8.2)."""
    return default_data_dir() / LOGS_DIR


def log_file_path() -> Path:
    """Absolute path to the main rotating log file (M8.2)."""
    return log_dir() / LOG_FILE


def ensure_data_dir() -> Path:
    """Create (if needed) and return the base data directory."""
    d = default_data_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d


def ensure_log_dir() -> Path:
    """Create (if needed) and return the log directory."""
    d = log_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d

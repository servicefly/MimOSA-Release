"""Locator for optional avatar asset files (M3.1).

The live avatar is drawn procedurally (see :mod:`mimosa.ui.avatar_renderer`), so
asset files are strictly optional. This tiny helper finds the bundled
``data/avatars`` directory and exposes paths to any SVG/PNG assets, returning
``None`` gracefully when nothing is present. No GTK/Cairo imports -- pure
``pathlib`` so it is trivially unit-testable.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import List, Optional

#: ``data/avatars`` resolved relative to the repository root (…/mimosa/ui/ -> up 2).
_DEFAULT_DIR = Path(__file__).resolve().parents[2] / "data" / "avatars"


class AvatarAssets:
    """Resolve optional avatar assets from a directory.

    Args:
        directory: Override the asset directory. Falls back to
            ``MIMOSA_AVATAR_DIR`` (env) and then the bundled ``data/avatars``.
    """

    def __init__(self, directory: Optional[os.PathLike] = None) -> None:
        if directory is not None:
            self.directory = Path(directory)
        else:
            env = os.environ.get("MIMOSA_AVATAR_DIR")
            self.directory = Path(env) if env else _DEFAULT_DIR

    def exists(self) -> bool:
        """True if the asset directory exists."""
        return self.directory.is_dir()

    def default_svg_path(self) -> Optional[Path]:
        """Path to ``default.svg`` if present, else ``None``."""
        candidate = self.directory / "default.svg"
        return candidate if candidate.is_file() else None

    def list_assets(self, suffixes=(".svg", ".png")) -> List[Path]:
        """Return all asset files with the given suffixes (sorted), or ``[]``."""
        if not self.exists():
            return []
        out = [
            p
            for p in sorted(self.directory.iterdir())
            if p.is_file() and p.suffix.lower() in suffixes
        ]
        return out

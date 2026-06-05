"""UI configuration for MimOSA's GTK4 avatar (M3.1).

This module owns the *persistent preferences* for the desktop avatar: window
size and position, opacity, always-on-top, the color theme, and animation
preferences. It is deliberately free of any GTK / Cairo imports so it can be
loaded, validated, and unit-tested on a headless machine with zero UI
dependencies.

Design notes
------------
* **Local & private.** Preferences live in a small JSON file under the user's
  XDG config directory (``~/.config/mimosa/ui.json`` by default). Nothing is
  ever sent off the device -- this is consistent with MimOSA's privacy-first
  principles.
* **Robust I/O.** Loading never raises on a missing or corrupt file; it falls
  back to defaults and logs at debug level. Saving is atomic (write to a temp
  file, then ``os.replace``) so a crash mid-write can't corrupt the config.
* **Injectable path.** ``MIMOSA_UI_CONFIG`` (env) or an explicit ``path``
  argument override the location, which keeps tests hermetic.
* **Validation.** Unknown keys are ignored; out-of-range values are clamped to
  sane bounds rather than rejected, so a hand-edited file never bricks the UI.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# -- bounds & defaults -------------------------------------------------------

MIN_SIZE = 80
MAX_SIZE = 600
DEFAULT_SIZE = 200

MIN_OPACITY = 0.1
MAX_OPACITY = 1.0
DEFAULT_OPACITY = 0.95

MIN_ANIM_SPEED = 0.1
MAX_ANIM_SPEED = 5.0
DEFAULT_ANIM_SPEED = 1.0

#: Built-in color themes. Each maps a state to an ``(r, g, b)`` tuple in the
#: 0..1 range used directly by Cairo. Renderers read these via
#: :meth:`UIConfig.theme_colors`.
COLOR_THEMES: Dict[str, Dict[str, tuple]] = {
    "aurora": {  # default -- cool blues/teals/violets
        "idle": (0.36, 0.65, 0.94),
        "listening": (0.20, 0.85, 0.62),
        "processing": (0.78, 0.62, 0.98),
        "speaking": (0.98, 0.78, 0.36),
        "base": (0.10, 0.12, 0.18),
    },
    "ember": {  # warm reds/oranges
        "idle": (0.95, 0.55, 0.30),
        "listening": (0.98, 0.78, 0.25),
        "processing": (0.85, 0.35, 0.45),
        "speaking": (0.99, 0.90, 0.55),
        "base": (0.16, 0.10, 0.10),
    },
    "mono": {  # accessible grayscale
        "idle": (0.70, 0.70, 0.72),
        "listening": (0.90, 0.90, 0.92),
        "processing": (0.55, 0.55, 0.58),
        "speaking": (0.98, 0.98, 0.99),
        "base": (0.12, 0.12, 0.13),
    },
}

DEFAULT_THEME = "aurora"
ANIMATION_STYLES = ("pulse", "rings", "waveform", "minimal")
DEFAULT_ANIMATION_STYLE = "pulse"


def default_config_path() -> Path:
    """Return the default UI-config path, honoring ``MIMOSA_UI_CONFIG`` & XDG.

    Order of precedence:

    1. ``MIMOSA_UI_CONFIG`` env var (used by tests and power users).
    2. ``$XDG_CONFIG_HOME/mimosa/ui.json``.
    3. ``~/.config/mimosa/ui.json``.
    """
    override = os.environ.get("MIMOSA_UI_CONFIG")
    if override:
        return Path(override).expanduser()
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg).expanduser() if xdg else Path.home() / ".config"
    return base / "mimosa" / "ui.json"


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


@dataclass
class UIConfig:
    """Persistent UI preferences for the avatar window.

    All fields have safe defaults so a fresh install works with no config file.
    Use :meth:`load` / :meth:`save` for disk I/O and :meth:`validate` to clamp
    values into range.
    """

    # window geometry
    size: int = DEFAULT_SIZE
    pos_x: Optional[int] = None  # None => let the window manager/center decide
    pos_y: Optional[int] = None
    monitor: int = 0  # preferred monitor index (best-effort)

    # appearance
    opacity: float = DEFAULT_OPACITY
    theme: str = DEFAULT_THEME
    always_on_top: bool = True

    # animation
    animation_style: str = DEFAULT_ANIMATION_STYLE
    animation_speed: float = DEFAULT_ANIM_SPEED
    animations_enabled: bool = True
    target_fps: int = 30

    # behavior
    start_hidden: bool = False

    def validate(self) -> "UIConfig":
        """Clamp/normalize all fields in place and return self.

        Out-of-range numbers are clamped; unknown theme/style names fall back
        to their defaults. This guarantees a usable config even after a user
        hand-edits the JSON.
        """
        try:
            self.size = int(_clamp(int(self.size), MIN_SIZE, MAX_SIZE))
        except (TypeError, ValueError):
            self.size = DEFAULT_SIZE

        try:
            self.opacity = round(_clamp(float(self.opacity), MIN_OPACITY, MAX_OPACITY), 3)
        except (TypeError, ValueError):
            self.opacity = DEFAULT_OPACITY

        try:
            self.animation_speed = round(
                _clamp(float(self.animation_speed), MIN_ANIM_SPEED, MAX_ANIM_SPEED), 3
            )
        except (TypeError, ValueError):
            self.animation_speed = DEFAULT_ANIM_SPEED

        try:
            self.target_fps = int(_clamp(int(self.target_fps), 5, 60))
        except (TypeError, ValueError):
            self.target_fps = 30

        if self.theme not in COLOR_THEMES:
            self.theme = DEFAULT_THEME
        if self.animation_style not in ANIMATION_STYLES:
            self.animation_style = DEFAULT_ANIMATION_STYLE

        self.always_on_top = bool(self.always_on_top)
        self.animations_enabled = bool(self.animations_enabled)
        self.start_hidden = bool(self.start_hidden)

        for attr in ("pos_x", "pos_y"):
            val = getattr(self, attr)
            if val is not None:
                try:
                    setattr(self, attr, int(val))
                except (TypeError, ValueError):
                    setattr(self, attr, None)

        try:
            self.monitor = max(0, int(self.monitor))
        except (TypeError, ValueError):
            self.monitor = 0

        return self

    def theme_colors(self) -> Dict[str, tuple]:
        """Return the active theme's state->color map (falls back to default)."""
        return COLOR_THEMES.get(self.theme, COLOR_THEMES[DEFAULT_THEME])

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to a plain dict suitable for JSON."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "UIConfig":
        """Build a config from a dict, ignoring unknown keys, then validate."""
        known = {f.name for f in fields(cls)}
        filtered = {k: v for k, v in (data or {}).items() if k in known}
        return cls(**filtered).validate()

    # -- persistence -------------------------------------------------------

    @classmethod
    def load(cls, path: Optional[os.PathLike] = None) -> "UIConfig":
        """Load preferences from ``path`` (or the default), never raising.

        A missing or unreadable/corrupt file yields a default config.
        """
        target = Path(path) if path is not None else default_config_path()
        try:
            with open(target, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if not isinstance(data, dict):
                raise ValueError("config root is not an object")
            cfg = cls.from_dict(data)
            logger.debug("Loaded UI config from %s", target)
            return cfg
        except FileNotFoundError:
            logger.debug("No UI config at %s; using defaults", target)
            return cls()
        except Exception as exc:  # corrupt JSON, perms, etc. -- degrade to defaults
            logger.warning("Could not read UI config %s (%s); using defaults", target, exc)
            return cls()

    def save(self, path: Optional[os.PathLike] = None) -> bool:
        """Atomically write preferences to ``path`` (or the default).

        Returns ``True`` on success, ``False`` if the write failed (e.g.
        read-only filesystem). Never raises.
        """
        target = Path(path) if path is not None else default_config_path()
        self.validate()
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            payload = json.dumps(self.to_dict(), indent=2, sort_keys=True)
            # Atomic write: temp file in the same dir, then replace.
            fd, tmp = tempfile.mkstemp(prefix=".ui.", suffix=".json", dir=str(target.parent))
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    fh.write(payload)
                os.replace(tmp, target)
            finally:
                if os.path.exists(tmp):
                    try:
                        os.unlink(tmp)
                    except OSError:  # pragma: no cover
                        pass
            logger.debug("Saved UI config to %s", target)
            return True
        except Exception as exc:
            logger.warning("Could not save UI config %s (%s)", target, exc)
            return False

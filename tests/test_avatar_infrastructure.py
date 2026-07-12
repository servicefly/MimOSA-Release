"""Infrastructure tests for the v2.0.0 avatar system (Milestone 8.1).

These cover the foundation laid in M8.1:

* avatar-tier capability detection,
* the ``AvatarSettings`` config schema (load/save/validate round-trip),
* the abstract :class:`BaseAvatarRenderer` interface,
* the 2D sprite renderer skeleton,
* the GTK avatar window (skipped on headless machines),
* the circle <-> avatar fallback toggle.

Everything except the window test runs headless (no GTK required), mirroring the
project's existing split between logic tests and GTK-gated UI tests.
"""

from __future__ import annotations

import pytest

from mimosa.system import capability_detector as cd
from mimosa.system.capability_detector import (
    CapabilityReport,
    SUPPORTED_AVATAR_TIERS,
    TIER_2D,
    TIER_CIRCLE,
    detect_avatar_tier,
)
from mimosa.utils.config import AppConfig, AppConfigManager, AvatarSettings
from mimosa.avatar.base_renderer import BaseAvatarRenderer
from mimosa.avatar.renderer_2d import Sprite2DRenderer
from mimosa.ui.environment import is_gui_available
from mimosa.ui.state_bridge import UIState


# ---------------------------------------------------------------------------
# 1. Avatar capability detection
# ---------------------------------------------------------------------------


def test_avatar_capability_detection():
    """Verify avatar tier detection returns a valid, *supported* tier."""
    # No report => runs a live scan; result must be renderable today.
    tier = detect_avatar_tier()
    assert tier in SUPPORTED_AVATAR_TIERS
    assert tier in (TIER_2D, TIER_CIRCLE)

    # A capable machine gets the 2D avatar.
    capable = CapabilityReport(ram_gb=8.0, cpu_cores=8)
    assert detect_avatar_tier(capable) == TIER_2D

    # A tiny machine falls back to the circle.
    tiny = CapabilityReport(ram_gb=1.0, cpu_cores=1)
    assert detect_avatar_tier(tiny) == TIER_CIRCLE

    # Unknown probe values are treated optimistically (2D is lightweight).
    unknown = CapabilityReport(ram_gb=None, cpu_cores=None)
    assert detect_avatar_tier(unknown) == TIER_2D

    # Never returns a reserved/unimplemented tier.
    assert cd.TIER_3D not in SUPPORTED_AVATAR_TIERS
    assert cd.TIER_LIVE2D not in SUPPORTED_AVATAR_TIERS


def test_avatar_capability_detection_never_raises():
    """Detection must degrade gracefully to the circle, never raise."""

    class _Boom:
        ram_gb = property(lambda self: (_ for _ in ()).throw(RuntimeError("boom")))

    # A report whose attribute access explodes must not propagate.
    assert detect_avatar_tier(_Boom()) == TIER_CIRCLE


# ---------------------------------------------------------------------------
# 2. Avatar config schema
# ---------------------------------------------------------------------------


def test_avatar_config_schema(tmp_path, monkeypatch):
    """Verify AvatarSettings loads/saves/validates correctly."""
    # Defaults: opt-out (circle) so existing users are unaffected.
    defaults = AvatarSettings()
    assert defaults.enabled is False
    assert defaults.tier == "circle_only"
    assert defaults.custom_sprite_path is None
    assert defaults.voice_id is None
    assert defaults.use_circle() is True

    # Validation clamps unknown tiers and trims blank strings to None.
    dirty = AvatarSettings(
        enabled=True, tier="not-a-tier", custom_sprite_path="   ", voice_id="  cora  "
    )
    dirty.validate()
    assert dirty.tier == "circle_only"          # unknown -> default
    assert dirty.custom_sprite_path is None      # blank -> None
    assert dirty.voice_id == "cora"              # trimmed

    # AppConfig embeds the section and round-trips through to_dict/from_dict.
    cfg = AppConfig()
    assert isinstance(cfg.avatar, AvatarSettings)
    cfg.avatar.enabled = True
    cfg.avatar.tier = "2d"
    cfg.avatar.custom_sprite_path = "/tmp/hero.png"
    cfg.avatar.voice_id = "cora"
    restored = AppConfig.from_dict(cfg.to_dict())
    assert restored.avatar.enabled is True
    assert restored.avatar.tier == "2d"
    assert restored.avatar.custom_sprite_path == "/tmp/hero.png"
    assert restored.avatar.voice_id == "cora"
    assert restored.avatar.use_circle() is False

    # Full disk persistence via the manager (settings.json round-trip).
    path = tmp_path / "settings.json"
    monkeypatch.setenv("MIMOSA_CONFIG", str(path))
    mgr = AppConfigManager(path=path)
    loaded = mgr.load()
    loaded.avatar.enabled = True
    loaded.avatar.tier = "2d"
    assert mgr.save() is True
    assert path.exists()

    mgr2 = AppConfigManager(path=path)
    reloaded = mgr2.load()
    assert reloaded.avatar.enabled is True
    assert reloaded.avatar.tier == "2d"


# ---------------------------------------------------------------------------
# 3. Base renderer interface
# ---------------------------------------------------------------------------


def test_base_renderer_interface():
    """Verify all abstract methods are defined and enforced."""
    # The abstract methods that concrete renderers must implement.
    assert BaseAvatarRenderer.__abstractmethods__ == frozenset(
        {"draw", "load", "dispose"}
    )

    # The base class cannot be instantiated directly.
    with pytest.raises(TypeError):
        BaseAvatarRenderer()

    # The shared, backend-free interface methods exist.
    for method in (
        "set_state",
        "update",
        "set_audio_level",
        "set_viseme",
        "set_emotion",
        "describe",
    ):
        assert callable(getattr(BaseAvatarRenderer, method))

    # A minimal concrete subclass implementing the abstract methods works and
    # inherits the shared state bookkeeping.
    class _Dummy(BaseAvatarRenderer):
        tier = "dummy"

        def draw(self, ctx, width, height):
            return None

        def load(self):
            self._loaded = True
            return True

        def dispose(self):
            self._loaded = False

    d = _Dummy()
    assert d.state is UIState.IDLE
    d.set_state(UIState.LISTENING)
    assert d.state is UIState.LISTENING
    assert d.previous_state is UIState.IDLE
    d.set_audio_level(2.0)          # clamps to 1.0
    assert d.audio_level == 1.0
    d.update(0.1)                   # advances clock without raising
    assert d.load() is True and d.is_loaded is True


# ---------------------------------------------------------------------------
# 4. 2D renderer initialization
# ---------------------------------------------------------------------------


def test_2d_renderer_initialization():
    """Verify the 2D renderer can be instantiated and driven headlessly."""
    r = Sprite2DRenderer()
    assert isinstance(r, BaseAvatarRenderer)
    assert r.tier == TIER_2D
    assert r.is_loaded is False

    # load() is a safe placeholder in M8.1 and marks the renderer ready.
    assert r.load() is True
    assert r.is_loaded is True

    # State transitions select a matching (placeholder) animation.
    r.set_state(UIState.SPEAKING)
    assert r.current_animation == "speaking"
    r.set_state(UIState.PROCESSING)
    assert r.current_animation == "thinking"
    r.update(0.5)  # advances clock/frame without a backend

    # from_config constructor mirrors AvatarRenderer.
    r2 = Sprite2DRenderer.from_config()
    assert isinstance(r2, Sprite2DRenderer)

    # draw() must never raise, even with a non-cairo context (it swallows and
    # logs). We pass a recording stub to confirm it attempts to paint.
    class _RecCtx:
        def __init__(self):
            self.ops = []

        def __getattr__(self, name):
            def _rec(*a, **k):
                self.ops.append(name)

            return _rec

    ctx = _RecCtx()
    r.draw(ctx, 200, 200)
    assert "arc" in ctx.ops and "fill" in ctx.ops

    r.dispose()
    assert r.is_loaded is False


# ---------------------------------------------------------------------------
# 5. Avatar window creation (GTK-gated)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not is_gui_available(), reason="requires GTK 4 and a display server"
)
def test_avatar_window_creation():
    """Verify the character-avatar window opens without crashing."""
    import gi

    gi.require_version("Gtk", "4.0")
    from gi.repository import GLib, Gtk

    from mimosa.avatar.avatar_window import AvatarCharacterWindow, HAS_GTK
    from mimosa.ui.ui_config import UIConfig

    assert HAS_GTK is True
    result = {"error": None, "ok": False}

    def on_activate(app):
        try:
            win = AvatarCharacterWindow(application=app, config=UIConfig(size=160))
            win.present()
            assert win._anim_source is not None
            win.set_state(UIState.LISTENING)
            win.set_audio_level(0.5)
            result["ok"] = True
        except Exception as exc:  # capture for main-thread assertion
            result["error"] = exc
        finally:
            GLib.timeout_add(300, lambda: (app.quit(), False)[1])

    app = Gtk.Application(application_id="ai.mimosa.AvatarTest")
    app.connect("activate", on_activate)
    app.run(None)

    assert result["error"] is None
    assert result["ok"] is True


def test_avatar_window_import_safe_headless():
    """The window module imports even when GTK is absent (None sentinel)."""
    import mimosa.avatar.avatar_window as mod

    # HAS_GTK reflects the environment; when False the class is None.
    if not mod.HAS_GTK:
        assert mod.AvatarCharacterWindow is None
    else:  # pragma: no cover - only on GTK hosts
        assert mod.AvatarCharacterWindow is not None


# ---------------------------------------------------------------------------
# 6. Fallback to circle
# ---------------------------------------------------------------------------


def test_fallback_to_circle():
    """Verify we can toggle between the avatar and the classic circle."""
    cfg = AppConfig()

    # Default: avatar disabled => use the circle.
    assert cfg.avatar.use_circle() is True

    # Enable the 2D avatar => stop using the circle.
    cfg.avatar.enabled = True
    cfg.avatar.tier = "2d"
    cfg.avatar.validate()
    assert cfg.avatar.use_circle() is False

    # Explicit circle_only tier forces the circle even when "enabled".
    cfg.avatar.tier = "circle_only"
    cfg.avatar.validate()
    assert cfg.avatar.use_circle() is True

    # Disabling always falls back to the circle regardless of tier.
    cfg.avatar.enabled = False
    cfg.avatar.tier = "2d"
    cfg.avatar.validate()
    assert cfg.avatar.use_circle() is True

    # The legacy circle renderer remains importable/usable (no regression).
    from mimosa.ui.avatar_renderer import AvatarRenderer

    circle = AvatarRenderer.from_config(cfg.ui)
    assert circle is not None

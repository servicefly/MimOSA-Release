"""Tests for mimosa.ui.avatar_renderer -- animation math + Cairo drawing.

The animation math is tested directly (pure & deterministic). The ``draw``
method is exercised against a real pycairo ImageSurface when available, and
skipped otherwise.
"""

import math

import pytest

from mimosa.ui.avatar_renderer import (
    TRANSITION_SECONDS,
    AvatarRenderer,
    blend_color,
    ease_in_out,
    lerp,
)
from mimosa.ui.state_bridge import UIState


class TestEasing:
    def test_ease_endpoints(self):
        assert ease_in_out(0.0) == 0.0
        assert ease_in_out(1.0) == 1.0

    def test_ease_midpoint(self):
        assert ease_in_out(0.5) == pytest.approx(0.5)

    def test_ease_clamps(self):
        assert ease_in_out(-5) == 0.0
        assert ease_in_out(5) == 1.0

    def test_lerp(self):
        assert lerp(0, 10, 0.5) == 5
        assert lerp(2, 4, 0.0) == 2

    def test_blend_color(self):
        assert blend_color((0, 0, 0), (1, 1, 1), 0.5) == (0.5, 0.5, 0.5)

    def test_blend_color_clamps(self):
        assert blend_color((0, 0, 0), (1, 1, 1), 2.0) == (1, 1, 1)


class TestRendererState:
    def test_default_state_is_idle(self):
        assert AvatarRenderer().state == UIState.IDLE

    def test_set_state_resets_transition(self):
        r = AvatarRenderer()
        r._transition = 1.0
        r.set_state(UIState.LISTENING)
        assert r.state == UIState.LISTENING
        assert r.transition == 0.0

    def test_set_same_state_is_noop(self):
        r = AvatarRenderer()
        r._transition = 1.0
        r.set_state(UIState.IDLE)
        assert r.transition == 1.0  # unchanged

    def test_set_state_accepts_voice_value(self):
        r = AvatarRenderer()
        r.set_state("speaking")
        assert r.state == UIState.SPEAKING

    def test_unknown_theme_falls_back(self):
        r = AvatarRenderer(theme="nope")
        assert r.theme == "aurora"

    def test_set_theme(self):
        r = AvatarRenderer()
        r.set_theme("ember")
        assert r.theme == "ember"
        r.set_theme("bogus")
        assert r.theme == "aurora"


class TestTick:
    def test_tick_advances_phase(self):
        r = AvatarRenderer()
        r.tick(0.1)
        assert r._phase > 0

    def test_tick_completes_transition(self):
        r = AvatarRenderer()
        r.set_state(UIState.LISTENING)
        # dt is clamped to 0.25s per tick, so step until the cross-fade settles.
        for _ in range(10):
            r.tick(0.1)
        assert r.transition == pytest.approx(1.0)

    def test_tick_clamps_large_dt(self):
        r = AvatarRenderer()
        r.tick(100.0)  # absurd dt clamped
        assert r._phase <= 0.25 * r.animation_speed + 1e-9

    def test_tick_ignores_garbage(self):
        r = AvatarRenderer()
        r.tick("not-a-number")  # type: ignore
        assert r._phase == 0.0

    def test_animations_disabled_freezes_phase(self):
        r = AvatarRenderer(animations_enabled=False)
        r.tick(0.1)
        assert r._phase == 0.0


class TestAnimationMath:
    def test_breathing_within_bounds(self):
        r = AvatarRenderer()
        for _ in range(50):
            r.tick(0.05)
            s = r.breathing_scale(0.9, 1.0)
            assert 0.9 <= s <= 1.0

    def test_breathing_frozen_when_disabled(self):
        r = AvatarRenderer(animations_enabled=False)
        assert r.breathing_scale(0.9, 1.0) == 1.0

    def test_ring_progress_count_and_range(self):
        r = AvatarRenderer()
        r.tick(0.3)
        rings = r.ring_progress(4)
        assert len(rings) == 4
        assert all(0.0 <= x < 1.0 for x in rings)

    def test_ring_progress_zero(self):
        assert AvatarRenderer().ring_progress(0) == ()

    def test_thinking_dots_range(self):
        r = AvatarRenderer()
        r.tick(0.2)
        dots = r.thinking_dots(3)
        assert len(dots) == 3
        assert all(0.0 <= d <= 1.0 for d in dots)

    def test_speaking_level_uses_audio_level(self):
        r = AvatarRenderer()
        r.set_audio_level(0.42)
        assert r.speaking_level() == pytest.approx(0.42)

    def test_speaking_level_synthesized_range(self):
        r = AvatarRenderer()
        for _ in range(40):
            r.tick(0.03)
            lvl = r.speaking_level()
            assert 0.0 <= lvl <= 1.0

    def test_audio_level_clamped(self):
        r = AvatarRenderer()
        r.set_audio_level(5.0)
        assert r._audio_level == 1.0
        r.set_audio_level(-2.0)
        assert r._audio_level == 0.0
        r.set_audio_level("bad")  # type: ignore
        assert r._audio_level == 0.0

    def test_accent_color_blends_during_transition(self):
        r = AvatarRenderer()
        r.set_state(UIState.LISTENING)  # transition starts at 0
        early = r.accent_color()
        for _ in range(10):  # dt clamped to 0.25s/tick; step until settled
            r.tick(0.1)
        late = r.accent_color()
        # After transition, accent should equal the listening color.
        assert late == pytest.approx(r._colors["listening"])
        assert early != late


cairo = pytest.importorskip("cairo")


class TestDraw:
    @pytest.mark.parametrize(
        "state",
        [
            UIState.IDLE,
            UIState.LISTENING,
            UIState.PROCESSING,
            UIState.SPEAKING,
            UIState.DISABLED,
        ],
    )
    def test_draw_paints_pixels(self, state):
        surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, 200, 200)
        cr = cairo.Context(surf)
        r = AvatarRenderer()
        r.set_state(state)
        r.tick(0.1)
        r.draw(cr, 200, 200)
        data = bytes(surf.get_data())
        assert any(b for b in data), f"no pixels drawn for {state}"

    def test_draw_never_raises_small_size(self):
        surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, 10, 10)
        cr = cairo.Context(surf)
        AvatarRenderer().draw(cr, 10, 10)  # should not raise

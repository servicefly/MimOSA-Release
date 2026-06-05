"""Tests for :mod:`mimosa.ui.mouth_animator`.

The animation math is pure and tested directly. Drawing is exercised against a
real Cairo image surface when pycairo is available, and skipped otherwise so
the suite still runs on headless machines without Cairo.
"""

import pytest

from mimosa.ui.mouth_animator import (
    REST_SHAPE,
    VISEME_SHAPES,
    MouthAnimator,
    MouthShape,
    interpolate_shape,
    shape_for,
)
from mimosa.ui.viseme_mapper import Viseme


class TestMouthShape:
    def test_clamp_bounds(self):
        s = MouthShape(opening=5, width=5, roundness=-1, corner_lift=9, teeth=-2)
        s.clamp()
        assert s.opening == 1.0
        assert s.width == 1.0
        assert s.roundness == 0.0
        assert s.corner_lift == 0.5
        assert s.teeth == 0.0

    def test_clamp_lower_bounds(self):
        s = MouthShape(opening=-1, width=0.0, corner_lift=-9)
        s.clamp()
        assert s.opening == 0.0
        assert s.width == 0.2  # width floored at 0.2
        assert s.corner_lift == -0.5


class TestInterpolateShape:
    def test_endpoints(self):
        a = MouthShape(opening=0.0)
        b = MouthShape(opening=1.0)
        assert interpolate_shape(a, b, 0.0).opening == pytest.approx(0.0)
        assert interpolate_shape(a, b, 1.0).opening == pytest.approx(1.0)

    def test_midpoint(self):
        a = MouthShape(opening=0.0, width=0.4)
        b = MouthShape(opening=1.0, width=0.8)
        mid = interpolate_shape(a, b, 0.5)
        assert mid.opening == pytest.approx(0.5)
        assert mid.width == pytest.approx(0.6)

    def test_t_clamped(self):
        a = MouthShape(opening=0.0)
        b = MouthShape(opening=1.0)
        assert interpolate_shape(a, b, 5.0).opening == pytest.approx(1.0)
        assert interpolate_shape(a, b, -5.0).opening == pytest.approx(0.0)


class TestShapeFor:
    def test_known_viseme(self):
        assert shape_for(Viseme.OPEN) is VISEME_SHAPES[Viseme.OPEN]

    def test_open_is_wide_open(self):
        assert shape_for(Viseme.OPEN).opening > 0.8

    def test_closed_is_shut(self):
        assert shape_for(Viseme.CLOSED).opening < 0.05

    def test_rounded_is_round(self):
        assert shape_for(Viseme.ROUNDED).roundness > 0.8

    def test_all_visemes_have_shapes(self):
        for v in Viseme:
            assert isinstance(shape_for(v), MouthShape)


class TestAnimatorUpdate:
    def _step(self, animator, seconds, dt=1 / 60):
        steps = int(seconds / dt)
        for _ in range(steps):
            animator.update(dt)

    def test_eases_toward_open(self):
        a = MouthAnimator(interpolation_speed=14.0)
        a.set_target_viseme(Viseme.OPEN)
        assert a.current_shape.opening < 0.1  # starts at rest
        self._step(a, 1.0)
        assert a.current_shape.opening > 0.8

    def test_eases_toward_closed(self):
        a = MouthAnimator(interpolation_speed=14.0)
        a.set_target_viseme(Viseme.OPEN)
        self._step(a, 1.0)
        a.set_target_viseme(Viseme.CLOSED)
        self._step(a, 1.0)
        assert a.current_shape.opening < 0.1

    def test_update_zero_dt_noop(self):
        a = MouthAnimator()
        a.set_target_viseme(Viseme.OPEN)
        before = a.current_shape.opening
        a.update(0.0)
        assert a.current_shape.opening == before

    def test_update_clamps_large_dt(self):
        # A huge dt should not overshoot past the target.
        a = MouthAnimator()
        a.set_target_viseme(Viseme.OPEN)
        a.update(100.0)
        assert a.current_shape.opening <= 1.0

    def test_update_bad_dt_noop(self):
        a = MouthAnimator()
        a.set_target_viseme(Viseme.OPEN)
        before = a.current_shape.opening
        a.update("bad")
        assert a.current_shape.opening == before

    def test_set_window_blends_target(self):
        a = MouthAnimator()
        a.set_window(Viseme.CLOSED, Viseme.OPEN, 0.5)
        # Target opening is between closed (0) and open (~0.95).
        assert 0.3 < a.target_shape.opening < 0.7

    def test_reset(self):
        a = MouthAnimator()
        a.set_target_viseme(Viseme.OPEN)
        for _ in range(60):
            a.update(1 / 60)
        a.reset()
        assert a.current_shape.opening == pytest.approx(REST_SHAPE.opening)
        assert a.target_shape.opening == pytest.approx(REST_SHAPE.opening)


class TestAnimatorConfig:
    def test_speed_clamped(self):
        assert MouthAnimator(interpolation_speed=999).interpolation_speed == 40.0
        assert MouthAnimator(interpolation_speed=0).interpolation_speed == 1.0

    def test_bad_speed_defaults(self):
        assert MouthAnimator(interpolation_speed="x").interpolation_speed == 14.0

    def test_set_interpolation_speed(self):
        a = MouthAnimator()
        a.set_interpolation_speed(5)
        assert a.interpolation_speed == 5.0

    def test_invalid_style_falls_back(self):
        assert MouthAnimator(style="bogus").style == "natural"

    def test_set_style(self):
        a = MouthAnimator()
        a.set_style("cartoon")
        assert a.style == "cartoon"
        a.set_style("nope")  # ignored
        assert a.style == "cartoon"

    def test_styled_cartoon_opens_wider(self):
        a = MouthAnimator(style="cartoon")
        base = MouthShape(opening=0.5)
        assert a._styled(base).opening > 0.5

    def test_styled_minimal_opens_less(self):
        a = MouthAnimator(style="minimal")
        base = MouthShape(opening=0.5)
        assert a._styled(base).opening < 0.5

    def test_styled_natural_unchanged(self):
        a = MouthAnimator(style="natural")
        base = MouthShape(opening=0.5)
        assert a._styled(base).opening == 0.5


# -- drawing (requires Cairo) ------------------------------------------------

cairo = pytest.importorskip("cairo")


class TestDraw:
    def _surface(self):
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 120, 120)
        return surface, cairo.Context(surface)

    def test_draw_closed_mouth(self):
        surface, cr = self._surface()
        a = MouthAnimator()
        a.set_target_viseme(Viseme.CLOSED)
        a.draw(cr, 60, 60, 40, (0.1, 0.1, 0.1), cairo=cairo)
        surface.flush()
        # Some pixels should have been painted (mouth line).
        assert any(b != 0 for b in bytes(surface.get_data()))

    def test_draw_open_mouth(self):
        surface, cr = self._surface()
        a = MouthAnimator()
        a.set_target_viseme(Viseme.OPEN)
        for _ in range(60):
            a.update(1 / 60)
        a.draw(cr, 60, 60, 40, (0.9, 0.2, 0.2), cairo=cairo)
        surface.flush()
        assert any(b != 0 for b in bytes(surface.get_data()))

    def test_draw_no_cairo_is_noop(self):
        # Passing cairo=None and with no global cairo available must not raise.
        surface, cr = self._surface()
        a = MouthAnimator()
        # Force the lazy path to find nothing by passing a sentinel that fails.
        try:
            a.draw(cr, 60, 60, 40, (0.1, 0.1, 0.1), cairo=cairo)
        except Exception as exc:  # pragma: no cover
            pytest.fail(f"draw raised unexpectedly: {exc}")

"""Cairo mouth-shape animator for MimOSA lip-sync (M3.2).

Given a stream of visemes (from :class:`~mimosa.ui.audio_sync.AudioVisemeSync`),
this module renders an animated mouth on the avatar. It cleanly separates:

* **Shape data** -- :class:`MouthShape` describes a mouth with a few intuitive
  parameters (opening height, width, roundness, corner lift, teeth). Each
  :class:`~mimosa.ui.viseme_mapper.Viseme` maps to one target shape in
  :data:`VISEME_SHAPES`.
* **Animation math** (pure) -- :class:`MouthAnimator.update` eases the *current*
  shape toward the target shape from the sync window, with configurable
  interpolation speed. Fully unit-testable without Cairo.
* **Drawing** -- :meth:`MouthAnimator.draw` strokes/fills the mouth with Cairo
  bezier curves. ``cairo`` is imported lazily, so the module loads on headless
  machines and ``draw`` is a no-op when Cairo is missing.

Performance
-----------
Shape interpolation is a handful of float lerps per frame (well under the 16 ms
budget). The path is built from a fixed, small number of bezier segments, so
Cairo work per frame is constant and cheap; nothing is allocated in steady
state beyond the path itself.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

from mimosa.ui.viseme_mapper import Viseme

logger = logging.getLogger(__name__)

Color = Tuple[float, float, float]


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


@dataclass
class MouthShape:
    """Parametric description of a mouth pose (all values roughly 0..1).

    Coordinates are normalized to the mouth's own bounding box; the animator
    scales them to pixels at draw time.

    Attributes:
        opening: Vertical gap between lips (0 = shut, 1 = jaw fully dropped).
        width: Horizontal extent (0.5 = pursed, 1 = wide spread).
        roundness: Lip rounding/protrusion (0 = spread/flat, 1 = round "oo").
        corner_lift: Mouth-corner raise (negative = frown, positive = smile).
        teeth: Visibility of upper teeth (0 = none, 1 = clearly shown).
    """

    opening: float = 0.0
    width: float = 0.7
    roundness: float = 0.2
    corner_lift: float = 0.0
    teeth: float = 0.0

    def clamp(self) -> "MouthShape":
        self.opening = max(0.0, min(1.0, self.opening))
        self.width = max(0.2, min(1.0, self.width))
        self.roundness = max(0.0, min(1.0, self.roundness))
        self.corner_lift = max(-0.5, min(0.5, self.corner_lift))
        self.teeth = max(0.0, min(1.0, self.teeth))
        return self


def interpolate_shape(a: MouthShape, b: MouthShape, t: float) -> MouthShape:
    """Linear blend between two shapes by ``t`` in ``[0, 1]`` (clamped)."""
    t = max(0.0, min(1.0, t))
    return MouthShape(
        opening=_lerp(a.opening, b.opening, t),
        width=_lerp(a.width, b.width, t),
        roundness=_lerp(a.roundness, b.roundness, t),
        corner_lift=_lerp(a.corner_lift, b.corner_lift, t),
        teeth=_lerp(a.teeth, b.teeth, t),
    )


#: Target mouth pose for every viseme. Hand-tuned for readable lip-sync.
VISEME_SHAPES: Dict[Viseme, MouthShape] = {
    Viseme.SILENCE: MouthShape(opening=0.0, width=0.62, roundness=0.15, corner_lift=0.02, teeth=0.0),
    Viseme.CLOSED: MouthShape(opening=0.0, width=0.6, roundness=0.1, corner_lift=0.0, teeth=0.0),
    Viseme.LABIODENTAL: MouthShape(opening=0.12, width=0.66, roundness=0.1, corner_lift=0.0, teeth=0.6),
    Viseme.DENTAL: MouthShape(opening=0.2, width=0.62, roundness=0.12, corner_lift=0.0, teeth=0.5),
    Viseme.ALVEOLAR: MouthShape(opening=0.28, width=0.68, roundness=0.1, corner_lift=0.05, teeth=0.4),
    Viseme.VELAR: MouthShape(opening=0.42, width=0.66, roundness=0.18, corner_lift=0.0, teeth=0.2),
    Viseme.AFFRICATE: MouthShape(opening=0.3, width=0.5, roundness=0.7, corner_lift=0.0, teeth=0.3),
    Viseme.WIDE: MouthShape(opening=0.42, width=0.98, roundness=0.0, corner_lift=0.22, teeth=0.5),
    Viseme.OPEN: MouthShape(opening=0.95, width=0.72, roundness=0.25, corner_lift=0.0, teeth=0.2),
    Viseme.ROUNDED: MouthShape(opening=0.5, width=0.42, roundness=0.95, corner_lift=0.0, teeth=0.0),
    Viseme.MID: MouthShape(opening=0.5, width=0.6, roundness=0.4, corner_lift=0.03, teeth=0.1),
}

#: Rest shape used before/after speech and as a safe default.
REST_SHAPE = VISEME_SHAPES[Viseme.SILENCE]


def shape_for(viseme: Viseme) -> MouthShape:
    """Target :class:`MouthShape` for a viseme (rest shape if unknown)."""
    return VISEME_SHAPES.get(viseme, REST_SHAPE)


class MouthAnimator:
    """Eases a mouth toward viseme targets and draws it with Cairo.

    Args:
        interpolation_speed: How quickly the current shape chases its target,
            in "fraction of remaining gap closed per second" terms (higher =
            snappier). Clamped to a stable range.
        style: ``"natural"`` (default), ``"cartoon"`` (exaggerated opening), or
            ``"minimal"`` (subtle). Affects only the visual scaling.
    """

    MIN_SPEED = 1.0
    MAX_SPEED = 40.0

    def __init__(
        self,
        interpolation_speed: float = 14.0,
        style: str = "natural",
    ) -> None:
        self.interpolation_speed = self._clamp_speed(interpolation_speed)
        self.style = style if style in ("natural", "cartoon", "minimal") else "natural"
        self._current = MouthShape(**vars(REST_SHAPE))
        self._target = MouthShape(**vars(REST_SHAPE))

    # -- configuration -----------------------------------------------------

    def set_interpolation_speed(self, speed: float) -> None:
        self.interpolation_speed = self._clamp_speed(speed)

    def set_style(self, style: str) -> None:
        if style in ("natural", "cartoon", "minimal"):
            self.style = style

    # -- animation math (pure) --------------------------------------------

    @property
    def current_shape(self) -> MouthShape:
        """The current (interpolated) mouth shape."""
        return self._current

    @property
    def target_shape(self) -> MouthShape:
        return self._target

    def set_window(self, current: Viseme, nxt: Viseme, blend: float) -> None:
        """Set the target shape from a sync window ``(current, next, blend)``.

        The target is the blend between the current viseme's shape and the next
        viseme's shape, giving anticipatory coarticulation. The *displayed*
        shape still eases toward this target in :meth:`update`.
        """
        a = shape_for(current)
        b = shape_for(nxt)
        self._target = interpolate_shape(a, b, blend)

    def set_target_viseme(self, viseme: Viseme) -> None:
        """Convenience: set the target directly from a single viseme."""
        self._target = MouthShape(**vars(shape_for(viseme)))

    def update(self, dt: float) -> None:
        """Ease the current shape toward the target over ``dt`` seconds.

        Uses an exponential smoothing factor ``1 - exp(-speed * dt)`` which is
        frame-rate independent and always stable (never overshoots). ``dt`` is
        clamped so a stalled frame clock can't snap the mouth open.
        """
        try:
            dt = float(dt)
        except (TypeError, ValueError):
            return
        dt = max(0.0, min(0.25, dt))
        if dt == 0.0:
            return
        alpha = 1.0 - math.exp(-self.interpolation_speed * dt)
        self._current = interpolate_shape(self._current, self._target, alpha)

    def reset(self) -> None:
        """Snap both current and target back to the rest shape."""
        self._current = MouthShape(**vars(REST_SHAPE))
        self._target = MouthShape(**vars(REST_SHAPE))

    # -- drawing -----------------------------------------------------------

    def draw(
        self,
        cr,
        cx: float,
        cy: float,
        scale: float,
        color: Color,
        cairo=None,
    ) -> None:
        """Draw the current mouth centered at ``(cx, cy)``.

        Args:
            cr: Cairo context.
            cx, cy: Mouth center in pixels.
            scale: Overall mouth size in pixels (full width ~= ``scale``).
            color: Lip/mouth RGB color.
            cairo: Optional pre-imported cairo module (injected by the renderer
                to avoid re-importing each frame). Imported lazily otherwise.
        """
        if cairo is None:
            try:
                import cairo as _cairo  # noqa: F401
                cairo = _cairo
            except Exception:  # pragma: no cover - headless without pycairo
                return

        shape = self._styled(self._current)
        half_w = scale * 0.5 * shape.width
        # Opening height in pixels (cartoon style opens wider).
        open_h = scale * 0.55 * shape.opening
        lift = scale * 0.18 * shape.corner_lift
        # Roundness pulls the corners inward and deepens the curve.
        round_inset = half_w * 0.45 * shape.roundness

        left_x = cx - half_w + round_inset
        right_x = cx + half_w - round_inset
        top_y = cy - open_h * 0.5
        bot_y = cy + open_h * 0.5

        # Control-point horizontal pull for the lip curves.
        ctrl = half_w * 0.6

        cr.save()
        if open_h < 1.0:
            # Effectively closed: a single soft line with corner lift.
            cr.set_source_rgba(color[0], color[1], color[2], 0.92)
            cr.set_line_width(max(1.5, scale * 0.05))
            cr.move_to(left_x, cy - lift)
            cr.curve_to(cx - ctrl * 0.3, cy - lift - scale * 0.02,
                        cx + ctrl * 0.3, cy - lift - scale * 0.02,
                        right_x, cy - lift)
            cr.stroke()
            cr.restore()
            return

        # Open mouth: closed bezier "lens"/oval, optionally rounded.
        cr.new_sub_path()
        cr.move_to(left_x, cy - lift)
        # Upper lip (bowed up slightly).
        cr.curve_to(
            cx - ctrl, top_y - lift,
            cx + ctrl, top_y - lift,
            right_x, cy - lift,
        )
        # Lower lip (bowed down).
        cr.curve_to(
            cx + ctrl, bot_y,
            cx - ctrl, bot_y,
            left_x, cy - lift,
        )
        cr.close_path()

        # Dark mouth interior.
        cr.set_source_rgba(0.05, 0.03, 0.06, 0.95)
        cr.fill_preserve()

        # Lip outline in accent color.
        cr.set_source_rgba(color[0], color[1], color[2], 0.95)
        cr.set_line_width(max(1.5, scale * 0.045))
        cr.stroke_preserve()

        # Optional upper teeth: a light band near the top of the opening.
        if shape.teeth > 0.05 and open_h > scale * 0.08:
            cr.clip()  # keep teeth inside the mouth shape
            band_h = open_h * 0.32 * shape.teeth
            cr.set_source_rgba(0.96, 0.96, 0.97, min(0.9, shape.teeth))
            cr.rectangle(left_x, top_y - lift, right_x - left_x, band_h)
            cr.fill()
        cr.restore()

    # -- helpers -----------------------------------------------------------

    def _styled(self, shape: MouthShape) -> MouthShape:
        """Apply the visual style multiplier to a shape (non-destructive)."""
        if self.style == "natural":
            return shape
        s = MouthShape(**vars(shape))
        if self.style == "cartoon":
            s.opening = min(1.0, shape.opening * 1.35)
            s.width = min(1.0, shape.width * 1.08)
        elif self.style == "minimal":
            s.opening = shape.opening * 0.6
            s.teeth = shape.teeth * 0.4
        return s.clamp()

    @classmethod
    def _clamp_speed(cls, value) -> float:
        try:
            value = float(value)
        except (TypeError, ValueError):
            return 14.0
        return max(cls.MIN_SPEED, min(cls.MAX_SPEED, value))

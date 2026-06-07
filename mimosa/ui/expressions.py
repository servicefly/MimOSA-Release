"""Sprite / expression-layer model atop the procedural avatar renderer (M4.3).

MimOSA's default avatar is drawn procedurally by
:class:`~mimosa.ui.avatar_renderer.AvatarRenderer`.  Phase 4 introduces an
*optional* expression layer on top of that renderer so the avatar can convey
simple emotions (happy, thinking, surprised, ...) and so future themes can ship
sprite-sheets without touching the drawing code.

This module is **pure logic**: it models expressions, sprite-sheet *metadata*
(frame geometry only -- no pixels are ever loaded here) and the composition of
ordered, alpha-blended layers.  Nothing in it imports GTK, Cairo, Pillow or any
image back-end, so it is fully unit-testable on a headless machine.  A thin
drawing/integration layer (the renderer or a GTK shell) consumes the
:class:`LayerFrame` instances produced here and does the actual blitting.

Design split (mirrors the rest of the UI package):

* :class:`Expression` -- the catalogue of supported expressions, with helpers
  to derive a sensible default from a :class:`~mimosa.ui.state_bridge.UIState`
  or from a sentiment hint.
* :class:`SpriteSheet` -- metadata describing a grid of equally-sized frames
  and named frame lookups; computes pixel rectangles without any image data.
* :class:`ExpressionLayer` / :class:`LayerFrame` -- a declarative layer and its
  resolved (composited) form.
* :class:`ExpressionController` -- stateful coordinator that maps the current
  UI state / explicit override to an expression, drives a deterministic
  blink animation (via an injectable clock) and emits the ordered layer stack.
"""

from __future__ import annotations

import enum
import logging
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

from mimosa.ui.state_bridge import UIState

logger = logging.getLogger(__name__)

Offset = Tuple[float, float]
Rect = Tuple[int, int, int, int]


class ExpressionError(ValueError):
    """Raised when expression / sprite metadata is invalid."""


# -- expressions -------------------------------------------------------------


class Expression(enum.Enum):
    """A small catalogue of avatar expressions.

    The set is intentionally compact and emotion-neutral enough to map cleanly
    onto the existing :class:`UIState` animations while leaving room for richer
    sprite-based themes.
    """

    NEUTRAL = "neutral"
    HAPPY = "happy"
    THINKING = "thinking"
    LISTENING = "listening"
    SPEAKING = "speaking"
    SURPRISED = "surprised"
    CONFUSED = "confused"
    SLEEPY = "sleepy"

    @classmethod
    def from_value(cls, value) -> "Expression":
        """Coerce ``value`` (an :class:`Expression`, name or ``.value``).

        Unknown values fall back to :attr:`NEUTRAL` so callers never crash on
        bad input.
        """
        if isinstance(value, cls):
            return value
        text = getattr(value, "value", value)
        if isinstance(text, str):
            text = text.strip().lower()
            for member in cls:
                if member.value == text:
                    return member
        return cls.NEUTRAL

    @classmethod
    def from_state(cls, state: UIState) -> "Expression":
        """Default expression for a given :class:`UIState`."""
        mapping = {
            UIState.IDLE: cls.NEUTRAL,
            UIState.LISTENING: cls.LISTENING,
            UIState.PROCESSING: cls.THINKING,
            UIState.SPEAKING: cls.SPEAKING,
            UIState.PAUSED: cls.SLEEPY,
            UIState.DISABLED: cls.SLEEPY,
        }
        return mapping.get(state, cls.NEUTRAL)

    @classmethod
    def from_sentiment(cls, sentiment) -> "Expression":
        """Map a coarse sentiment hint to an expression.

        Accepts ``"positive"`` / ``"negative"`` / ``"neutral"`` / ``"question"``
        (case-insensitive) or any unknown value (-> :attr:`NEUTRAL`).
        """
        text = getattr(sentiment, "value", sentiment)
        text = str(text or "").strip().lower()
        return {
            "positive": cls.HAPPY,
            "happy": cls.HAPPY,
            "negative": cls.CONFUSED,
            "sad": cls.CONFUSED,
            "question": cls.THINKING,
            "surprise": cls.SURPRISED,
            "surprised": cls.SURPRISED,
        }.get(text, cls.NEUTRAL)


#: All expression string values, in declaration order.
EXPRESSION_NAMES: Tuple[str, ...] = tuple(e.value for e in Expression)


# -- sprite-sheet metadata ---------------------------------------------------


@dataclass
class SpriteSheet:
    """Metadata for a grid sprite-sheet (no image data is held here).

    Frames are laid out left-to-right, top-to-bottom.  ``names`` provides an
    optional human-readable lookup from a label to a frame index.
    """

    frame_width: int
    frame_height: int
    columns: int
    rows: int = 1
    names: Dict[str, int] = field(default_factory=dict)
    source: str = ""  # optional asset path/key, never opened by this module

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        for label, value in (
            ("frame_width", self.frame_width),
            ("frame_height", self.frame_height),
            ("columns", self.columns),
            ("rows", self.rows),
        ):
            if not isinstance(value, int) or value <= 0:
                raise ExpressionError(f"{label} must be a positive integer, got {value!r}")
        for name, idx in self.names.items():
            if not isinstance(idx, int) or not (0 <= idx < self.frame_count):
                raise ExpressionError(
                    f"frame index for {name!r} out of range: {idx!r} "
                    f"(0..{self.frame_count - 1})"
                )

    @property
    def frame_count(self) -> int:
        return self.columns * self.rows

    @property
    def sheet_width(self) -> int:
        return self.columns * self.frame_width

    @property
    def sheet_height(self) -> int:
        return self.rows * self.frame_height

    def index_of(self, name: str) -> int:
        """Resolve a frame *name* to its index."""
        try:
            return self.names[name]
        except KeyError:
            raise ExpressionError(f"unknown sprite frame name: {name!r}") from None

    def rect_for_index(self, index: int) -> Rect:
        """Return ``(x, y, w, h)`` pixel rect for ``index`` on the sheet."""
        if not (0 <= index < self.frame_count):
            raise ExpressionError(
                f"frame index {index} out of range (0..{self.frame_count - 1})"
            )
        col = index % self.columns
        row = index // self.columns
        return (
            col * self.frame_width,
            row * self.frame_height,
            self.frame_width,
            self.frame_height,
        )

    def rect_for(self, name: str) -> Rect:
        """Return the pixel rect for a named frame."""
        return self.rect_for_index(self.index_of(name))


# -- layers ------------------------------------------------------------------


@dataclass
class ExpressionLayer:
    """A declarative drawing layer for an expression.

    ``sprite`` names a frame within an associated :class:`SpriteSheet`; when
    ``None`` the layer is purely procedural (handled by the base renderer).
    """

    name: str
    sprite: Optional[str] = None
    z_index: int = 0
    opacity: float = 1.0
    offset: Offset = (0.0, 0.0)
    visible: bool = True

    def __post_init__(self) -> None:
        self.name = (self.name or "").strip() or "layer"
        self.opacity = _clamp(float(self.opacity), 0.0, 1.0)
        ox, oy = self.offset
        self.offset = (float(ox), float(oy))


@dataclass(frozen=True)
class LayerFrame:
    """A resolved, ready-to-draw layer (output of composition)."""

    name: str
    z_index: int
    opacity: float
    offset: Offset
    sprite: Optional[str] = None
    rect: Optional[Rect] = None  # sheet rect when a sprite/sheet is present


def compose_layers(
    layers: List[ExpressionLayer],
    sheet: Optional[SpriteSheet] = None,
) -> List[LayerFrame]:
    """Resolve and order ``layers`` for drawing.

    Hidden (``visible=False``) and fully transparent layers are dropped.  The
    remaining layers are returned sorted by ``z_index`` ascending (stable for
    equal z to preserve declaration order).  When a ``sheet`` is supplied and a
    layer names a sprite, its pixel rect is resolved.
    """
    resolved: List[Tuple[int, int, LayerFrame]] = []
    for order, layer in enumerate(layers):
        if not layer.visible or layer.opacity <= 0.0:
            continue
        rect = None
        if layer.sprite is not None and sheet is not None:
            rect = sheet.rect_for(layer.sprite)
        resolved.append(
            (
                layer.z_index,
                order,
                LayerFrame(
                    name=layer.name,
                    z_index=layer.z_index,
                    opacity=layer.opacity,
                    offset=layer.offset,
                    sprite=layer.sprite,
                    rect=rect,
                ),
            )
        )
    resolved.sort(key=lambda t: (t[0], t[1]))
    return [frame for _, _, frame in resolved]


#: Default layer stack per expression.  Each base layer is procedural (no
#: sprite) so the model works even without a sprite-sheet; sprite-based themes
#: can override these via :meth:`ExpressionController.set_layers`.
def default_layers_for(expression: Expression) -> List[ExpressionLayer]:
    """Built-in procedural layer stack for ``expression``."""
    base = ExpressionLayer(name="base", z_index=0)
    accent = ExpressionLayer(
        name=f"accent:{expression.value}",
        z_index=10,
        opacity=0.9 if expression is not Expression.NEUTRAL else 0.0,
        visible=expression is not Expression.NEUTRAL,
    )
    return [base, accent]


# -- controller --------------------------------------------------------------


_DEFAULT_BLINK_INTERVAL = 4.0   # seconds between blinks
_DEFAULT_BLINK_DURATION = 0.15  # seconds an eye-blink lasts


class ExpressionController:
    """Coordinates the avatar's current expression and layer stack.

    The controller is deterministic and side-effect free: time is supplied via
    an injectable ``clock`` callable so blink animation is fully testable.
    """

    def __init__(
        self,
        sheet: Optional[SpriteSheet] = None,
        *,
        clock: Optional[Callable[[], float]] = None,
        blink_interval: float = _DEFAULT_BLINK_INTERVAL,
        blink_duration: float = _DEFAULT_BLINK_DURATION,
        blink_enabled: bool = True,
    ) -> None:
        self._sheet = sheet
        self._clock = clock
        self._blink_interval = max(0.1, float(blink_interval))
        self._blink_duration = max(0.0, float(blink_duration))
        self._blink_enabled = bool(blink_enabled)

        self._state = UIState.IDLE
        self._expression = Expression.NEUTRAL
        self._override: Optional[Expression] = None
        self._layers: Optional[List[ExpressionLayer]] = None
        self._last_blink = self._now()
        self._blinking = False

    # -- time helper --
    def _now(self) -> float:
        if self._clock is not None:
            return float(self._clock())
        import time

        return time.monotonic()

    # -- state / expression --
    @property
    def state(self) -> UIState:
        return self._state

    @property
    def current_expression(self) -> Expression:
        """The effective expression (explicit override wins over state)."""
        return self._override or self._expression

    @property
    def sheet(self) -> Optional[SpriteSheet]:
        return self._sheet

    def set_state(self, state) -> Expression:
        """Update the UI state; recomputes the state-derived expression.

        An explicit override (see :meth:`set_expression`) continues to take
        precedence over the state mapping until cleared.
        """
        if not isinstance(state, UIState):
            state = UIState.from_voice_state(state)
        self._state = state
        self._expression = Expression.from_state(state)
        return self.current_expression

    def set_expression(self, expression) -> Expression:
        """Pin an explicit expression override (``None`` clears it)."""
        if expression is None:
            self._override = None
        else:
            self._override = Expression.from_value(expression)
        return self.current_expression

    def set_emotion(self, sentiment) -> Expression:
        """Override the expression from a coarse sentiment hint."""
        return self.set_expression(Expression.from_sentiment(sentiment))

    def clear_override(self) -> Expression:
        """Drop any explicit override and fall back to the state mapping."""
        self._override = None
        return self.current_expression

    # -- layers --
    def set_layers(self, layers: Optional[List[ExpressionLayer]]) -> None:
        """Override the layer stack (``None`` restores per-expression defaults)."""
        self._layers = list(layers) if layers is not None else None

    def set_sheet(self, sheet: Optional[SpriteSheet]) -> None:
        self._sheet = sheet

    def _base_layers(self) -> List[ExpressionLayer]:
        if self._layers is not None:
            return [
                ExpressionLayer(
                    name=l.name,
                    sprite=l.sprite,
                    z_index=l.z_index,
                    opacity=l.opacity,
                    offset=l.offset,
                    visible=l.visible,
                )
                for l in self._layers
            ]
        return default_layers_for(self.current_expression)

    # -- blink animation --
    @property
    def is_blinking(self) -> bool:
        return self._blinking

    def update(self, now: Optional[float] = None) -> bool:
        """Advance the blink animation; returns the current blinking flag.

        A blink starts every ``blink_interval`` seconds and lasts
        ``blink_duration`` seconds.  When ``blink_enabled`` is ``False`` the
        avatar never blinks.
        """
        if not self._blink_enabled or self._blink_duration <= 0.0:
            self._blinking = False
            return False
        t = self._now() if now is None else float(now)
        elapsed = t - self._last_blink
        if self._blinking:
            if elapsed >= self._blink_duration:
                self._blinking = False
                self._last_blink = t
        elif elapsed >= self._blink_interval:
            self._blinking = True
            self._last_blink = t
        return self._blinking

    def layers(self, now: Optional[float] = None) -> List[LayerFrame]:
        """Return the ordered, composited layer stack for drawing.

        Includes a transient ``blink`` overlay layer while a blink is active.
        """
        self.update(now)
        base = self._base_layers()
        if self._blinking:
            base.append(ExpressionLayer(name="blink", z_index=100, opacity=1.0))
        return compose_layers(base, self._sheet)

    def to_dict(self) -> Dict[str, object]:
        """Serializable snapshot (handy for debugging / state inspection)."""
        return {
            "state": self._state.value,
            "expression": self.current_expression.value,
            "override": self._override.value if self._override else None,
            "blinking": self._blinking,
            "has_sheet": self._sheet is not None,
        }


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))

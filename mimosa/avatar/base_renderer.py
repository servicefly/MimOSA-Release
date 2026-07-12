"""Abstract base class for MimOSA avatar renderers (Milestone 8.1).

The v2.0.0 avatar system ships in tiers -- an animated 2D sprite baseline in
this release, with richer 2.5D (Live2D) and 3D character renderers planned for
v2.1.0+. Every tier speaks the *same* small interface so the rest of the app
(the avatar window, the state bridge, the audio/viseme sync) can drive any
renderer without knowing which tier it is talking to.

:class:`BaseAvatarRenderer` defines that contract. It deliberately imports **no**
GTK/Cairo at module load, mirroring :mod:`mimosa.ui.avatar_renderer`, so this
file loads and unit-tests cleanly on a headless machine. Concrete renderers
(e.g. :class:`mimosa.avatar.renderer_2d.Sprite2DRenderer`) import their drawing
backend lazily inside :meth:`draw`.

Interface overview
------------------
State & animation (pure, deterministic -- unit-testable with no backend):

* :meth:`set_state`   -- transition to a new :class:`~mimosa.ui.state_bridge.UIState`.
* :meth:`update`      -- advance the animation clock by ``dt`` seconds.
* :meth:`set_audio_level` / :meth:`set_viseme` -- feed lip-sync data.
* :meth:`set_emotion` -- request an expression/emotion (reserved for M8.3).

Drawing & lifecycle (backend-specific -- implemented by subclasses):

* :meth:`draw`     -- paint one frame into a drawing context.
* :meth:`load`     -- load sprites / models / assets (may be a no-op).
* :meth:`dispose`  -- release any held resources.
"""

from __future__ import annotations

import abc
import logging
from typing import Optional

from mimosa.ui.state_bridge import UIState

logger = logging.getLogger(__name__)


class BaseAvatarRenderer(abc.ABC):
    """Abstract interface shared by every avatar rendering tier.

    Concrete subclasses must implement the abstract methods below. The base
    class provides light, backend-free bookkeeping (current/previous state, a
    monotonically increasing animation clock, the latest audio level) that most
    renderers need, so subclasses can focus on their tier-specific drawing.

    Args:
        tier: The renderer's tier identifier (e.g. ``"2d"``). Subclasses set a
            sensible default; callers rarely pass this explicitly.
    """

    #: Tier identifier for this renderer class. Overridden by subclasses.
    tier: str = "base"

    def __init__(self, tier: Optional[str] = None) -> None:
        if tier is not None:
            self.tier = tier
        # Shared, backend-free animation bookkeeping.
        self._state: UIState = UIState.IDLE
        self._previous_state: UIState = UIState.IDLE
        self._elapsed: float = 0.0            # seconds since construction
        self._transition: float = 1.0         # 0..1 progress of state cross-fade
        self._audio_level: float = 0.0        # 0..1 speaking loudness
        self._emotion: str = "neutral"        # reserved for M8.3 expressions
        self._loaded: bool = False

    # -- state & animation (concrete, pure) --------------------------------

    @property
    def state(self) -> UIState:
        """The renderer's current :class:`UIState`."""
        return self._state

    @property
    def previous_state(self) -> UIState:
        """The state the renderer is transitioning *from* (for cross-fades)."""
        return self._previous_state

    def set_state(self, state: UIState) -> None:
        """Transition to ``state``, resetting the cross-fade progress.

        Accepts a :class:`UIState` or anything :meth:`UIState.from_voice_state`
        understands (an enum or a raw string), so callers can forward voice
        states directly. Setting the state it is already in is a no-op.
        """
        if not isinstance(state, UIState):
            state = UIState.from_voice_state(state)
        if state is self._state:
            return
        self._previous_state = self._state
        self._state = state
        self._transition = 0.0

    def update(self, dt: float) -> None:
        """Advance the animation clock by ``dt`` seconds.

        Keeps the elapsed clock and the state cross-fade progress moving. Safe
        to call every frame; negative/zero ``dt`` is ignored.
        """
        try:
            dt = float(dt)
        except (TypeError, ValueError):
            return
        if dt <= 0.0:
            return
        self._elapsed += dt
        if self._transition < 1.0:
            # TRANSITION_SECONDS-style ease handled by subclasses if desired;
            # here we simply progress linearly toward 1.0.
            self._transition = min(1.0, self._transition + dt / 0.35)

    def set_audio_level(self, level: float) -> None:
        """Feed the current speaking loudness (``0..1``) for lip movement."""
        try:
            level = float(level)
        except (TypeError, ValueError):
            return
        self._audio_level = max(0.0, min(1.0, level))

    def set_viseme(self, viseme) -> None:  # noqa: ANN001 - Viseme imported lazily
        """Feed the current mouth shape (viseme) for precise lip-sync.

        The 2D baseline uses :meth:`set_audio_level`; richer tiers override this
        to snap the mouth to a specific viseme. Default: store for subclasses.
        """
        self._viseme = viseme

    def set_emotion(self, emotion: str) -> None:
        """Request an expression/emotion (e.g. ``"happy"``). Reserved for M8.3."""
        self._emotion = str(emotion or "neutral")

    @property
    def audio_level(self) -> float:
        return self._audio_level

    @property
    def emotion(self) -> str:
        return self._emotion

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    # -- drawing & lifecycle (abstract, backend-specific) ------------------

    @abc.abstractmethod
    def draw(self, ctx, width: int, height: int) -> None:
        """Paint one frame of the avatar into ``ctx`` at ``width`` x ``height``.

        ``ctx`` is a backend drawing context (a Cairo context for 2D). Concrete
        renderers import their backend lazily inside this method.
        """
        raise NotImplementedError

    @abc.abstractmethod
    def load(self) -> bool:
        """Load any assets (sprites, models). Returns ``True`` on success.

        May be a near no-op for tiers with nothing to load. Should never raise;
        return ``False`` on failure so the caller can fall back to the circle.
        """
        raise NotImplementedError

    @abc.abstractmethod
    def dispose(self) -> None:
        """Release any resources held by the renderer (textures, handles)."""
        raise NotImplementedError

    # -- introspection -----------------------------------------------------

    def describe(self) -> str:
        """Human-readable one-liner for logs / diagnostics."""
        return (
            f"{type(self).__name__}(tier={self.tier}, state={self._state.value}, "
            f"loaded={self._loaded})"
        )

"""Avatar frame-rate monitoring and automatic throttling (item #10).

The v2.0.0 avatar renders at a target frame rate (typically 30 FPS). On weaker
hardware the animation loop may not keep up, which looks worse than the classic
listening circle. :class:`FrameRateGovernor` watches the *actual* achieved frame
rate over a rolling window and, when it stays below a floor for long enough,
recommends **downgrading** — either lowering the target FPS or, in the worst
case, falling back to the ``circle_only`` tier.

This module is pure and dependency-free so it is fully unit-testable on a
headless CI machine; the renderer simply feeds it frame timestamps and reacts to
its recommendations.
"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass
from enum import Enum
from typing import Deque, Optional

logger = logging.getLogger(__name__)


class ThrottleAction(Enum):
    """What the governor recommends the renderer do next."""

    NONE = "none"            # healthy — keep the current settings
    LOWER_FPS = "lower_fps"  # reduce the target frame rate
    FALLBACK_CIRCLE = "fallback_circle"  # give up the sprite, use the circle


@dataclass(frozen=True)
class ThrottleDecision:
    """The governor's recommendation plus supporting numbers."""

    action: ThrottleAction
    measured_fps: float
    target_fps: int


class FrameRateGovernor:
    """Track achieved FPS and recommend throttling when it falls behind.

    Args:
        target_fps: The frame rate the renderer is aiming for.
        min_fps: The floor below which sustained performance is unacceptable.
        window: Number of recent frames to average over.
        grace_frames: How many frames to observe before making any decision
            (avoids reacting to a slow first frame / startup jitter).
        min_target_fps: The lowest target the governor will step down to before
            recommending the circle fallback instead.
        step: How many FPS to drop per :attr:`ThrottleAction.LOWER_FPS`.
    """

    def __init__(
        self,
        target_fps: int = 30,
        *,
        min_fps: float = 15.0,
        window: int = 30,
        grace_frames: int = 30,
        min_target_fps: int = 20,
        step: int = 5,
    ) -> None:
        if target_fps <= 0:
            raise ValueError("target_fps must be positive")
        self.target_fps = int(target_fps)
        self.min_fps = float(min_fps)
        self.window = max(2, int(window))
        self.grace_frames = max(0, int(grace_frames))
        self.min_target_fps = int(min_target_fps)
        self.step = max(1, int(step))

        self._frame_times: Deque[float] = deque(maxlen=self.window)
        self._last_ts: Optional[float] = None
        self._total_frames = 0

    def record_frame(self, timestamp: float) -> None:
        """Record the wall-clock ``timestamp`` (seconds) of a rendered frame."""
        if self._last_ts is not None:
            delta = timestamp - self._last_ts
            if delta > 0:
                self._frame_times.append(delta)
        self._last_ts = timestamp
        self._total_frames += 1

    def measured_fps(self) -> float:
        """Average FPS over the rolling window (0.0 until enough samples)."""
        if not self._frame_times:
            return 0.0
        avg_delta = sum(self._frame_times) / len(self._frame_times)
        if avg_delta <= 0:
            return 0.0
        return 1.0 / avg_delta

    def evaluate(self) -> ThrottleDecision:
        """Decide whether to throttle based on recent performance.

        Returns a :class:`ThrottleDecision`. During the grace period, or with
        too few samples, this always returns :attr:`ThrottleAction.NONE`.
        """
        fps = self.measured_fps()
        if self._total_frames < self.grace_frames or len(self._frame_times) < 2:
            return ThrottleDecision(ThrottleAction.NONE, fps, self.target_fps)

        if fps >= self.min_fps:
            return ThrottleDecision(ThrottleAction.NONE, fps, self.target_fps)

        # Under the floor: step the target down, or fall back to the circle if
        # we're already as low as we'll go.
        if self.target_fps > self.min_target_fps:
            return ThrottleDecision(
                ThrottleAction.LOWER_FPS, fps, self.target_fps)
        return ThrottleDecision(
            ThrottleAction.FALLBACK_CIRCLE, fps, self.target_fps)

    def apply(self, decision: ThrottleDecision) -> int:
        """Apply a ``LOWER_FPS`` decision, returning the new target FPS.

        Lowering the target also resets the rolling window so the next
        evaluation reflects performance at the *new* target rather than the old
        one. ``NONE`` / ``FALLBACK_CIRCLE`` decisions leave the target
        unchanged.
        """
        if decision.action is ThrottleAction.LOWER_FPS:
            new_target = max(self.min_target_fps, self.target_fps - self.step)
            if new_target != self.target_fps:
                logger.info(
                    "Avatar auto-throttle: lowering target FPS %d -> %d "
                    "(measured ~%.1f FPS)",
                    self.target_fps, new_target, decision.measured_fps,
                )
            self.target_fps = new_target
            self.reset_window()
        elif decision.action is ThrottleAction.FALLBACK_CIRCLE:
            logger.info(
                "Avatar auto-throttle: sustained ~%.1f FPS below floor %.1f at "
                "minimum target %d; recommending classic-circle fallback.",
                decision.measured_fps, self.min_fps, self.min_target_fps,
            )
        return self.target_fps

    def reset_window(self) -> None:
        """Clear the rolling window and grace counter (e.g. after a change)."""
        self._frame_times.clear()
        self._last_ts = None
        self._total_frames = 0

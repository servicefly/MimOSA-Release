"""Tests for the avatar frame-rate governor / auto-throttle (item #10)."""

from __future__ import annotations

import pytest

from mimosa.avatar.performance import (
    FrameRateGovernor,
    ThrottleAction,
)


def _feed(gov: FrameRateGovernor, fps: float, frames: int, start: float = 0.0):
    """Feed ``frames`` evenly spaced to simulate a steady ``fps``."""
    dt = 1.0 / fps
    t = start
    for _ in range(frames):
        gov.record_frame(t)
        t += dt
    return t


def test_healthy_fps_recommends_no_action():
    gov = FrameRateGovernor(target_fps=30, min_fps=15, grace_frames=5, window=20)
    _feed(gov, 30.0, 40)
    decision = gov.evaluate()
    assert decision.action is ThrottleAction.NONE
    assert decision.measured_fps == pytest.approx(30.0, rel=0.05)


def test_grace_period_suppresses_early_decisions():
    gov = FrameRateGovernor(target_fps=30, min_fps=15, grace_frames=30)
    _feed(gov, 5.0, 3)  # terrible fps but still within grace / too few samples
    assert gov.evaluate().action is ThrottleAction.NONE


def test_low_fps_recommends_lower_fps():
    gov = FrameRateGovernor(target_fps=30, min_fps=15, grace_frames=5, window=20)
    _feed(gov, 8.0, 40)
    decision = gov.evaluate()
    assert decision.action is ThrottleAction.LOWER_FPS
    assert decision.measured_fps < 15


def test_apply_lower_fps_steps_target_and_resets_window():
    gov = FrameRateGovernor(target_fps=30, min_fps=15, grace_frames=5,
                            window=20, step=5, min_target_fps=20)
    _feed(gov, 8.0, 40)
    decision = gov.evaluate()
    new_target = gov.apply(decision)
    assert new_target == 25
    # Window reset -> not enough samples -> NONE until refilled.
    assert gov.evaluate().action is ThrottleAction.NONE


def test_fallback_circle_at_min_target():
    gov = FrameRateGovernor(target_fps=20, min_fps=15, grace_frames=5,
                            window=20, min_target_fps=20)
    _feed(gov, 8.0, 40)
    decision = gov.evaluate()
    assert decision.action is ThrottleAction.FALLBACK_CIRCLE
    # Applying a fallback decision leaves the target unchanged.
    assert gov.apply(decision) == 20


def test_invalid_target_fps_raises():
    with pytest.raises(ValueError):
        FrameRateGovernor(target_fps=0)


def test_animator_optional_governor_records_frames():
    from mimosa.avatar.animator import Animator

    animator = Animator()
    gov = FrameRateGovernor(target_fps=30, grace_frames=0, window=10)
    animator.governor = gov
    for _ in range(5):
        animator.update()
    # Frames were sampled (deltas depend on wall-clock but count should grow).
    assert gov._total_frames == 5

"""Audio <-> viseme playback synchronization for MimOSA lip-sync (M3.2).

A :class:`VisemeTimeline` says *which mouth shape* to show *when*. This module
tracks **where playback currently is** so the renderer can ask "what should the
mouth look like right now?" each frame and stay in sync with the audio coming
out of the speakers.

Why a dedicated clock?
----------------------
Audio playback and the GTK frame clock are independent. We anchor the timeline
to a monotonic clock when speech starts and compute the elapsed position every
frame. A small, configurable **latency offset** compensates for output
buffering (the sound a user hears lags slightly behind "play start"), and an
adaptive :meth:`AudioVisemeSync.resync` lets a real audio backend nudge the
position if it can report true playback progress.

The module is pure Python (no GTK/Cairo) and the clock is injectable, so timing
behaviour is fully unit-testable without real audio or a display.
"""

from __future__ import annotations

import logging
import time
from typing import Callable, Optional, Tuple

from mimosa.voice.phoneme_extractor import VisemeTimeline
from mimosa.ui.viseme_mapper import Viseme

logger = logging.getLogger(__name__)


class AudioVisemeSync:
    """Tracks playback position over a :class:`VisemeTimeline`.

    Args:
        latency_offset: Seconds to *add* to the elapsed time before sampling the
            timeline, compensating for audio output buffering (so the visible
            mouth shape lines up with the sound the user hears). Clamped to a
            sane range.
        clock: Injectable monotonic clock returning seconds. Defaults to
            :func:`time.monotonic`. Tests pass a controllable fake.
    """

    #: Hard bounds on the latency offset (seconds).
    MIN_LATENCY = -0.5
    MAX_LATENCY = 1.0

    def __init__(
        self,
        latency_offset: float = 0.05,
        clock: Optional[Callable[[], float]] = None,
    ) -> None:
        self._clock = clock or time.monotonic
        self.latency_offset = self._clamp_latency(latency_offset)
        self._timeline: VisemeTimeline = VisemeTimeline.empty()
        self._active = False
        self._paused = False
        self._start_ts = 0.0          # clock time when playback began
        self._paused_elapsed = 0.0    # elapsed accumulated before a pause
        self._pause_ts = 0.0

    # -- lifecycle ---------------------------------------------------------

    def start(self, timeline: VisemeTimeline) -> None:
        """Begin playback of ``timeline`` anchored to *now*."""
        self._timeline = timeline or VisemeTimeline.empty()
        self._active = True
        self._paused = False
        self._paused_elapsed = 0.0
        self._start_ts = self._clock()

    def stop(self) -> None:
        """Stop playback and clear the timeline (mouth returns to rest)."""
        self._active = False
        self._paused = False
        self._timeline = VisemeTimeline.empty()
        self._paused_elapsed = 0.0

    def pause(self) -> None:
        """Freeze the playback position (idempotent)."""
        if not self._active or self._paused:
            return
        # Capture elapsed BEFORE flipping the flag (raw_elapsed reads the flag).
        self._paused_elapsed = self._raw_elapsed()
        self._pause_ts = self._clock()
        self._paused = True

    def resume(self) -> None:
        """Resume from where :meth:`pause` froze (idempotent)."""
        if not self._active or not self._paused:
            return
        self._paused = False
        # Re-anchor start so that raw elapsed continues from paused_elapsed.
        self._start_ts = self._clock() - self._paused_elapsed

    def resync(self, true_position: float) -> None:
        """Adaptively correct the position to a backend-reported ``true_position``.

        Lets an audio backend that knows its real playback offset (seconds since
        speech began) nudge the internal clock. Small corrections only; large
        jumps are accepted but logged.
        """
        if not self._active:
            return
        try:
            true_position = float(true_position)
        except (TypeError, ValueError):
            return
        if self._paused:
            self._paused_elapsed = max(0.0, true_position)
            return
        self._start_ts = self._clock() - max(0.0, true_position)

    def set_latency_offset(self, latency_offset: float) -> None:
        """Update the latency compensation (clamped)."""
        self.latency_offset = self._clamp_latency(latency_offset)

    # -- queries -----------------------------------------------------------

    @property
    def active(self) -> bool:
        return self._active

    @property
    def paused(self) -> bool:
        return self._paused

    @property
    def timeline(self) -> VisemeTimeline:
        return self._timeline

    def _raw_elapsed(self) -> float:
        if not self._active:
            return 0.0
        if self._paused:
            return self._paused_elapsed
        return max(0.0, self._clock() - self._start_ts)

    def position(self) -> float:
        """Current timeline position in seconds (raw elapsed + latency offset)."""
        if not self._active:
            return 0.0
        return max(0.0, self._raw_elapsed() + self.latency_offset)

    def is_finished(self) -> bool:
        """True once the position has run past the timeline's duration."""
        if not self._active:
            return True
        dur = self._timeline.duration
        if dur <= 0:
            return True
        return self.position() >= dur

    def current_viseme(self) -> Viseme:
        """The viseme that should be shown right now (silence when inactive)."""
        if not self._active:
            return Viseme.SILENCE
        return self._timeline.viseme_at(self.position())

    def current_window(self) -> Tuple[Viseme, Viseme, float]:
        """``(current, next, blend)`` for smooth interpolation at *now*.

        Returns all-silence when inactive so the mouth eases shut.
        """
        if not self._active:
            return (Viseme.SILENCE, Viseme.SILENCE, 0.0)
        return self._timeline.window_at(self.position())

    # -- helpers -----------------------------------------------------------

    @classmethod
    def _clamp_latency(cls, value) -> float:
        try:
            value = float(value)
        except (TypeError, ValueError):
            return 0.05
        return max(cls.MIN_LATENCY, min(cls.MAX_LATENCY, value))

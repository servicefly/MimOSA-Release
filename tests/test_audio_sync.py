"""Tests for :mod:`mimosa.ui.audio_sync`.

Playback timing is made deterministic by injecting a fake monotonic clock so
no real sleeping or audio device is involved.
"""

import pytest

from mimosa.ui.audio_sync import AudioVisemeSync
from mimosa.ui.viseme_mapper import Viseme
from mimosa.voice.phoneme_extractor import VisemeFrame, VisemeTimeline


class FakeClock:
    """A manually-advanced monotonic clock."""

    def __init__(self, t=0.0):
        self.t = float(t)

    def __call__(self):
        return self.t

    def advance(self, dt):
        self.t += dt


def _timeline():
    return VisemeTimeline(
        frames=[
            VisemeFrame(Viseme.OPEN, 0.0, 0.5),
            VisemeFrame(Viseme.CLOSED, 0.5, 1.0),
        ],
        duration=1.0,
        source="phonemes",
    )


class TestStartStop:
    def test_starts_inactive(self):
        s = AudioVisemeSync(clock=FakeClock())
        assert not s.active
        assert s.position() == 0.0
        assert s.is_finished()
        assert s.current_viseme() is Viseme.SILENCE

    def test_start_activates(self):
        clk = FakeClock()
        s = AudioVisemeSync(clock=clk)
        s.start(_timeline())
        assert s.active
        assert not s.paused
        assert s.position() == pytest.approx(s.latency_offset)

    def test_stop_resets(self):
        s = AudioVisemeSync(clock=FakeClock())
        s.start(_timeline())
        s.stop()
        assert not s.active
        assert s.timeline.is_empty
        assert s.position() == 0.0

    def test_start_none_is_empty(self):
        s = AudioVisemeSync(clock=FakeClock())
        s.start(None)
        assert s.active
        assert s.timeline.is_empty


class TestPosition:
    def test_position_tracks_clock_with_latency(self):
        clk = FakeClock()
        s = AudioVisemeSync(latency_offset=0.0, clock=clk)
        s.start(_timeline())
        clk.advance(0.3)
        assert s.position() == pytest.approx(0.3)
        clk.advance(0.4)
        assert s.position() == pytest.approx(0.7)

    def test_latency_offset_applied(self):
        clk = FakeClock()
        s = AudioVisemeSync(latency_offset=0.1, clock=clk)
        s.start(_timeline())
        clk.advance(0.2)
        assert s.position() == pytest.approx(0.3)

    def test_current_viseme_follows_position(self):
        clk = FakeClock()
        s = AudioVisemeSync(latency_offset=0.0, clock=clk)
        s.start(_timeline())
        clk.advance(0.2)
        assert s.current_viseme() is Viseme.OPEN
        clk.advance(0.5)  # now 0.7
        assert s.current_viseme() is Viseme.CLOSED

    def test_current_window(self):
        clk = FakeClock()
        s = AudioVisemeSync(latency_offset=0.0, clock=clk)
        s.start(_timeline())
        clk.advance(0.49)
        cur, nxt, blend = s.current_window()
        assert cur is Viseme.OPEN
        assert nxt is Viseme.CLOSED
        assert blend > 0.0

    def test_inactive_window_is_silence(self):
        s = AudioVisemeSync(clock=FakeClock())
        assert s.current_window() == (Viseme.SILENCE, Viseme.SILENCE, 0.0)


class TestFinished:
    def test_is_finished_after_duration(self):
        clk = FakeClock()
        s = AudioVisemeSync(latency_offset=0.0, clock=clk)
        s.start(_timeline())
        clk.advance(0.5)
        assert not s.is_finished()
        clk.advance(0.6)  # 1.1 > 1.0
        assert s.is_finished()

    def test_empty_timeline_finished(self):
        s = AudioVisemeSync(clock=FakeClock())
        s.start(VisemeTimeline.empty())
        assert s.is_finished()


class TestPauseResume:
    def test_pause_freezes_position(self):
        clk = FakeClock()
        s = AudioVisemeSync(latency_offset=0.0, clock=clk)
        s.start(_timeline())
        clk.advance(0.3)
        s.pause()
        assert s.paused
        frozen = s.position()
        clk.advance(1.0)  # time passes while paused
        assert s.position() == pytest.approx(frozen)

    def test_resume_continues(self):
        clk = FakeClock()
        s = AudioVisemeSync(latency_offset=0.0, clock=clk)
        s.start(_timeline())
        clk.advance(0.3)
        s.pause()
        clk.advance(5.0)  # paused, ignored
        s.resume()
        assert not s.paused
        clk.advance(0.2)
        assert s.position() == pytest.approx(0.5)

    def test_pause_when_inactive_noop(self):
        s = AudioVisemeSync(clock=FakeClock())
        s.pause()  # should not raise
        assert not s.paused


class TestResync:
    def test_resync_sets_position(self):
        clk = FakeClock()
        s = AudioVisemeSync(latency_offset=0.0, clock=clk)
        s.start(_timeline())
        clk.advance(0.2)
        s.resync(0.7)
        assert s.position() == pytest.approx(0.7)

    def test_resync_while_paused(self):
        clk = FakeClock()
        s = AudioVisemeSync(latency_offset=0.0, clock=clk)
        s.start(_timeline())
        s.pause()
        s.resync(0.4)
        assert s.position() == pytest.approx(0.4)

    def test_resync_inactive_noop(self):
        s = AudioVisemeSync(clock=FakeClock())
        s.resync(0.5)  # no crash
        assert s.position() == 0.0


class TestLatencyClamp:
    def test_clamped_to_bounds(self):
        s = AudioVisemeSync(latency_offset=99.0)
        assert s.latency_offset == AudioVisemeSync.MAX_LATENCY
        s2 = AudioVisemeSync(latency_offset=-99.0)
        assert s2.latency_offset == AudioVisemeSync.MIN_LATENCY

    def test_bad_value_defaults(self):
        s = AudioVisemeSync(latency_offset="not a number")
        assert s.latency_offset == pytest.approx(0.05)

    def test_set_latency_offset(self):
        s = AudioVisemeSync(latency_offset=0.0)
        s.set_latency_offset(0.2)
        assert s.latency_offset == pytest.approx(0.2)
        s.set_latency_offset(99.0)
        assert s.latency_offset == AudioVisemeSync.MAX_LATENCY

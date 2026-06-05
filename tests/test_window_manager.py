"""Tests for mimosa.ui.window_manager -- monitor logic & position persistence.

Pure geometry/persistence is tested directly; no GTK is required. A fake
monitors model exercises the Gdk-query parsing path.
"""

import pytest

from mimosa.ui.ui_config import UIConfig
from mimosa.ui.window_manager import (
    MonitorInfo,
    WindowManager,
    clamp_to_monitor,
    resolve_position,
    select_monitor,
)


@pytest.fixture
def monitors():
    return [
        MonitorInfo(0, 0, 1920, 1080),
        MonitorInfo(1920, 0, 1280, 1024),
    ]


class TestMonitorInfo:
    def test_edges(self):
        m = MonitorInfo(100, 50, 800, 600)
        assert m.right == 900
        assert m.bottom == 650

    def test_center(self):
        m = MonitorInfo(0, 0, 1000, 1000)
        assert m.center(200) == (400, 400)


class TestSelectMonitor:
    def test_valid_index(self, monitors):
        assert select_monitor(monitors, 1) is monitors[1]

    def test_out_of_range_falls_back_to_first(self, monitors):
        assert select_monitor(monitors, 9) is monitors[0]

    def test_empty_returns_none(self):
        assert select_monitor([], 0) is None


class TestClamp:
    def test_inside_unchanged(self, monitors):
        assert clamp_to_monitor(100, 100, 200, monitors[0]) == (100, 100)

    def test_clamps_right_bottom(self, monitors):
        x, y = clamp_to_monitor(5000, 5000, 200, monitors[0])
        assert x == 1920 - 200
        assert y == 1080 - 200

    def test_clamps_negative(self, monitors):
        assert clamp_to_monitor(-50, -50, 200, monitors[0]) == (0, 0)

    def test_oversized_window_pins_topleft(self):
        m = MonitorInfo(0, 0, 100, 100)
        assert clamp_to_monitor(50, 50, 500, m) == (0, 0)

    def test_margin(self):
        m = MonitorInfo(0, 0, 1000, 1000)
        assert clamp_to_monitor(-10, -10, 200, m, margin=20) == (20, 20)


class TestResolvePosition:
    def test_no_monitors_returns_none(self):
        assert resolve_position(UIConfig(), []) is None

    def test_saved_position_clamped(self, monitors):
        c = UIConfig(size=200, pos_x=5000, pos_y=5000, monitor=1)
        x, y = resolve_position(c, monitors)
        # clamped onto monitor #1 (origin 1920,0 size 1280x1024)
        assert x == 1920 + 1280 - 200
        assert y == 1024 - 200

    def test_no_saved_position_centers(self, monitors):
        c = UIConfig(size=200, monitor=0)
        assert resolve_position(c, monitors) == monitors[0].center(200)

    def test_bad_monitor_index_uses_first(self, monitors):
        c = UIConfig(size=200, monitor=99)
        assert resolve_position(c, monitors) == monitors[0].center(200)


class TestPersistence:
    def test_save_position_writes_config(self, tmp_path):
        path = tmp_path / "ui.json"
        c = UIConfig(size=200)
        wm = WindowManager(c, config_path=path)
        assert wm.save_position(300, 400, monitor_index=1) is True
        reloaded = UIConfig.load(path)
        assert reloaded.pos_x == 300
        assert reloaded.pos_y == 400
        assert reloaded.monitor == 1

    def test_startup_position_uses_injected_monitors(self, monitors):
        c = UIConfig(size=200, monitor=0)
        wm = WindowManager(c)
        assert wm.startup_position(monitors) == monitors[0].center(200)


class FakeRect:
    def __init__(self, x, y, w, h):
        self.x, self.y, self.width, self.height = x, y, w, h


class FakeMonitor:
    def __init__(self, rect):
        self._rect = rect

    def get_geometry(self):
        return self._rect


class FakeMonitorsModel:
    def __init__(self, mons):
        self._mons = mons

    def get_n_items(self):
        return len(self._mons)

    def get_item(self, i):
        return self._mons[i]


class FakeDisplay:
    def __init__(self, model):
        self._model = model

    def get_monitors(self):
        return self._model


class TestQueryMonitors:
    def test_parses_injected_display(self):
        model = FakeMonitorsModel(
            [
                FakeMonitor(FakeRect(0, 0, 1920, 1080)),
                FakeMonitor(FakeRect(1920, 0, 1280, 1024)),
            ]
        )
        wm = WindowManager(UIConfig())
        mons = wm.query_monitors(display=FakeDisplay(model))
        assert len(mons) == 2
        assert mons[0] == MonitorInfo(0, 0, 1920, 1080)
        assert mons[1] == MonitorInfo(1920, 0, 1280, 1024)

    def test_query_handles_failure_gracefully(self):
        class BadDisplay:
            def get_monitors(self):
                raise RuntimeError("no display")

        wm = WindowManager(UIConfig())
        assert wm.query_monitors(display=BadDisplay()) == []

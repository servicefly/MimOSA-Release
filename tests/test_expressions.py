"""Tests for the sprite / expression-layer model (M4.3).

Fully offline / hermetic: the module under test is pure logic with no GTK,
Cairo or image back-ends, so these tests load no pixels and touch no display.
"""

from __future__ import annotations

import pytest

from mimosa.ui.expressions import (
    EXPRESSION_NAMES,
    Expression,
    ExpressionController,
    ExpressionError,
    ExpressionLayer,
    LayerFrame,
    SpriteSheet,
    compose_layers,
    default_layers_for,
)
from mimosa.ui.state_bridge import UIState


# -- Expression enum ---------------------------------------------------------


class TestExpression:
    def test_names_cover_all_members(self):
        assert set(EXPRESSION_NAMES) == {e.value for e in Expression}
        assert "neutral" in EXPRESSION_NAMES

    @pytest.mark.parametrize(
        "value,expected",
        [
            (Expression.HAPPY, Expression.HAPPY),
            ("happy", Expression.HAPPY),
            ("  THINKING ", Expression.THINKING),
            ("bogus", Expression.NEUTRAL),
            (None, Expression.NEUTRAL),
            (123, Expression.NEUTRAL),
        ],
    )
    def test_from_value(self, value, expected):
        assert Expression.from_value(value) is expected

    @pytest.mark.parametrize(
        "state,expected",
        [
            (UIState.IDLE, Expression.NEUTRAL),
            (UIState.LISTENING, Expression.LISTENING),
            (UIState.PROCESSING, Expression.THINKING),
            (UIState.SPEAKING, Expression.SPEAKING),
            (UIState.DISABLED, Expression.SLEEPY),
        ],
    )
    def test_from_state(self, state, expected):
        assert Expression.from_state(state) is expected

    @pytest.mark.parametrize(
        "sentiment,expected",
        [
            ("positive", Expression.HAPPY),
            ("happy", Expression.HAPPY),
            ("negative", Expression.CONFUSED),
            ("question", Expression.THINKING),
            ("surprised", Expression.SURPRISED),
            ("", Expression.NEUTRAL),
            ("unknown", Expression.NEUTRAL),
            (None, Expression.NEUTRAL),
        ],
    )
    def test_from_sentiment(self, sentiment, expected):
        assert Expression.from_sentiment(sentiment) is expected


# -- SpriteSheet -------------------------------------------------------------


class TestSpriteSheet:
    def test_basic_geometry(self):
        sheet = SpriteSheet(frame_width=32, frame_height=48, columns=4, rows=2)
        assert sheet.frame_count == 8
        assert sheet.sheet_width == 128
        assert sheet.sheet_height == 96

    def test_rect_for_index_layout(self):
        sheet = SpriteSheet(frame_width=10, frame_height=20, columns=3, rows=2)
        assert sheet.rect_for_index(0) == (0, 0, 10, 20)
        assert sheet.rect_for_index(2) == (20, 0, 10, 20)
        # wraps to next row
        assert sheet.rect_for_index(3) == (0, 20, 10, 20)
        assert sheet.rect_for_index(5) == (20, 20, 10, 20)

    def test_named_frames(self):
        sheet = SpriteSheet(
            frame_width=16, frame_height=16, columns=2, rows=1,
            names={"open": 0, "closed": 1},
        )
        assert sheet.index_of("closed") == 1
        assert sheet.rect_for("closed") == (16, 0, 16, 16)

    def test_unknown_name_raises(self):
        sheet = SpriteSheet(frame_width=16, frame_height=16, columns=1)
        with pytest.raises(ExpressionError):
            sheet.index_of("missing")

    def test_index_out_of_range_raises(self):
        sheet = SpriteSheet(frame_width=8, frame_height=8, columns=2, rows=1)
        with pytest.raises(ExpressionError):
            sheet.rect_for_index(2)
        with pytest.raises(ExpressionError):
            sheet.rect_for_index(-1)

    @pytest.mark.parametrize(
        "kwargs",
        [
            dict(frame_width=0, frame_height=8, columns=1),
            dict(frame_width=8, frame_height=-1, columns=1),
            dict(frame_width=8, frame_height=8, columns=0),
            dict(frame_width=8, frame_height=8, columns=1, rows=0),
        ],
    )
    def test_invalid_geometry_raises(self, kwargs):
        with pytest.raises(ExpressionError):
            SpriteSheet(**kwargs)

    def test_invalid_name_index_raises(self):
        with pytest.raises(ExpressionError):
            SpriteSheet(frame_width=8, frame_height=8, columns=1, names={"x": 5})


# -- layers & composition ----------------------------------------------------


class TestLayers:
    def test_layer_clamps_opacity_and_normalizes(self):
        layer = ExpressionLayer(name="  ", opacity=5.0, offset=(1, 2))
        assert layer.name == "layer"
        assert layer.opacity == 1.0
        assert layer.offset == (1.0, 2.0)
        neg = ExpressionLayer(name="x", opacity=-3)
        assert neg.opacity == 0.0

    def test_compose_orders_by_z_index(self):
        layers = [
            ExpressionLayer(name="top", z_index=10),
            ExpressionLayer(name="bottom", z_index=0),
            ExpressionLayer(name="mid", z_index=5),
        ]
        out = compose_layers(layers)
        assert [f.name for f in out] == ["bottom", "mid", "top"]
        assert all(isinstance(f, LayerFrame) for f in out)

    def test_compose_stable_for_equal_z(self):
        layers = [
            ExpressionLayer(name="a", z_index=1),
            ExpressionLayer(name="b", z_index=1),
            ExpressionLayer(name="c", z_index=1),
        ]
        assert [f.name for f in compose_layers(layers)] == ["a", "b", "c"]

    def test_compose_drops_hidden_and_transparent(self):
        layers = [
            ExpressionLayer(name="visible", z_index=0),
            ExpressionLayer(name="hidden", z_index=1, visible=False),
            ExpressionLayer(name="ghost", z_index=2, opacity=0.0),
        ]
        out = compose_layers(layers)
        assert [f.name for f in out] == ["visible"]

    def test_compose_resolves_sprite_rect(self):
        sheet = SpriteSheet(
            frame_width=16, frame_height=16, columns=2, names={"eye": 1}
        )
        layers = [ExpressionLayer(name="eyes", sprite="eye", z_index=1)]
        out = compose_layers(layers, sheet)
        assert out[0].rect == (16, 0, 16, 16)
        assert out[0].sprite == "eye"

    def test_compose_sprite_without_sheet_leaves_rect_none(self):
        layers = [ExpressionLayer(name="eyes", sprite="eye")]
        out = compose_layers(layers, None)
        assert out[0].rect is None

    def test_default_layers_neutral_has_no_accent(self):
        out = compose_layers(default_layers_for(Expression.NEUTRAL))
        assert [f.name for f in out] == ["base"]

    def test_default_layers_non_neutral_has_accent(self):
        out = compose_layers(default_layers_for(Expression.HAPPY))
        names = [f.name for f in out]
        assert "base" in names
        assert any(n.startswith("accent:") for n in names)


# -- ExpressionController ----------------------------------------------------


class FakeClock:
    def __init__(self, t=0.0):
        self.t = float(t)

    def __call__(self):
        return self.t

    def advance(self, dt):
        self.t += dt


class TestExpressionController:
    def test_defaults(self):
        ctrl = ExpressionController(clock=FakeClock())
        assert ctrl.state is UIState.IDLE
        assert ctrl.current_expression is Expression.NEUTRAL

    def test_set_state_maps_expression(self):
        ctrl = ExpressionController(clock=FakeClock())
        assert ctrl.set_state(UIState.PROCESSING) is Expression.THINKING
        assert ctrl.current_expression is Expression.THINKING

    def test_set_state_accepts_voice_state_string(self):
        ctrl = ExpressionController(clock=FakeClock())
        ctrl.set_state("listening")
        assert ctrl.current_expression is Expression.LISTENING

    def test_override_wins_over_state(self):
        ctrl = ExpressionController(clock=FakeClock())
        ctrl.set_state(UIState.LISTENING)
        ctrl.set_expression(Expression.HAPPY)
        assert ctrl.current_expression is Expression.HAPPY
        # state change does not dislodge the override
        ctrl.set_state(UIState.SPEAKING)
        assert ctrl.current_expression is Expression.HAPPY

    def test_clear_override_restores_state_mapping(self):
        ctrl = ExpressionController(clock=FakeClock())
        ctrl.set_state(UIState.SPEAKING)
        ctrl.set_expression(Expression.HAPPY)
        ctrl.clear_override()
        assert ctrl.current_expression is Expression.SPEAKING

    def test_set_expression_none_clears(self):
        ctrl = ExpressionController(clock=FakeClock())
        ctrl.set_expression(Expression.HAPPY)
        ctrl.set_expression(None)
        assert ctrl.current_expression is Expression.NEUTRAL

    def test_set_emotion(self):
        ctrl = ExpressionController(clock=FakeClock())
        ctrl.set_emotion("positive")
        assert ctrl.current_expression is Expression.HAPPY

    def test_blink_cycle_deterministic(self):
        clock = FakeClock(0.0)
        ctrl = ExpressionController(
            clock=clock, blink_interval=4.0, blink_duration=0.15
        )
        assert ctrl.update() is False
        clock.advance(3.9)
        assert ctrl.update() is False
        clock.advance(0.2)  # now > 4.0 since last blink
        assert ctrl.update() is True
        assert ctrl.is_blinking is True
        clock.advance(0.2)  # blink lasted > 0.15
        assert ctrl.update() is False

    def test_blink_disabled(self):
        clock = FakeClock(0.0)
        ctrl = ExpressionController(clock=clock, blink_enabled=False)
        clock.advance(100)
        assert ctrl.update() is False
        assert ctrl.is_blinking is False

    def test_layers_include_blink_overlay_while_blinking(self):
        clock = FakeClock(0.0)
        ctrl = ExpressionController(clock=clock, blink_interval=1.0, blink_duration=0.5)
        clock.advance(1.1)
        frames = ctrl.layers()
        assert any(f.name == "blink" for f in frames)
        # blink overlay is on top (highest z)
        assert frames[-1].name == "blink"

    def test_layers_no_blink_when_not_blinking(self):
        ctrl = ExpressionController(clock=FakeClock(), blink_interval=10.0)
        frames = ctrl.layers()
        assert not any(f.name == "blink" for f in frames)

    def test_set_layers_override_and_restore(self):
        ctrl = ExpressionController(clock=FakeClock(), blink_interval=1000)
        custom = [ExpressionLayer(name="custom", z_index=0)]
        ctrl.set_layers(custom)
        assert [f.name for f in ctrl.layers()] == ["custom"]
        ctrl.set_layers(None)
        assert [f.name for f in ctrl.layers()] == ["base"]  # neutral default

    def test_set_layers_does_not_mutate_caller_list(self):
        ctrl = ExpressionController(clock=FakeClock(), blink_interval=1000)
        custom = [ExpressionLayer(name="custom", z_index=0)]
        ctrl.set_layers(custom)
        ctrl.layers()
        assert len(custom) == 1  # blink/internal additions never leak out

    def test_sheet_resolution_through_controller(self):
        sheet = SpriteSheet(
            frame_width=8, frame_height=8, columns=2, names={"face": 1}
        )
        ctrl = ExpressionController(sheet=sheet, clock=FakeClock(), blink_interval=1000)
        ctrl.set_layers([ExpressionLayer(name="face", sprite="face", z_index=0)])
        frames = ctrl.layers()
        assert frames[0].rect == (8, 0, 8, 8)

    def test_to_dict_snapshot(self):
        ctrl = ExpressionController(clock=FakeClock())
        ctrl.set_state(UIState.PROCESSING)
        snap = ctrl.to_dict()
        assert snap["state"] == "processing"
        assert snap["expression"] == "thinking"
        assert snap["override"] is None
        assert snap["has_sheet"] is False

    def test_default_clock_is_monotonic(self):
        # No clock injected -> uses time.monotonic(); should not raise.
        ctrl = ExpressionController(blink_interval=1000)
        assert ctrl.update() in (True, False)

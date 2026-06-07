"""Cairo avatar renderer with per-state animations (M3.1).

Draws MimOSA's circular avatar and animates it according to the current
:class:`~mimosa.ui.state_bridge.UIState`:

* **IDLE** -- a gentle breathing/pulsing glow.
* **LISTENING** -- expanding concentric rings (or a live waveform).
* **PROCESSING** -- orbiting "thinking" dots.
* **SPEAKING** -- a reactive mouth/level bar driven by an audio level (or a
  synthesized oscillation when no real level is available).

The class separates **animation math** (pure, deterministic, unit-testable
without any drawing backend) from **drawing** (the ``draw`` method, which needs
a Cairo context). State changes ease smoothly: colors and a transition factor
interpolate from the previous state to the new one over a short duration.

``cairo`` is imported lazily inside :meth:`draw` so this module imports fine on
headless machines that lack pycairo -- all the math methods still work for
tests and logic.
"""

from __future__ import annotations

import logging
import math
from typing import Dict, Optional, Tuple

from mimosa.ui.audio_sync import AudioVisemeSync
from mimosa.ui.mouth_animator import MouthAnimator
from mimosa.ui.state_bridge import UIState
from mimosa.ui.ui_config import COLOR_THEMES, DEFAULT_THEME
from mimosa.ui.viseme_mapper import Viseme

logger = logging.getLogger(__name__)

Color = Tuple[float, float, float]

_SILENCE_VISEME = Viseme.SILENCE

# Duration (seconds) of the cross-fade between two states.
TRANSITION_SECONDS = 0.35


# -- easing ------------------------------------------------------------------


def ease_in_out(t: float) -> float:
    """Smooth cubic ease-in-out for ``t`` in ``[0, 1]`` (clamped)."""
    t = max(0.0, min(1.0, t))
    if t < 0.5:
        return 4.0 * t * t * t
    f = (2.0 * t) - 2.0
    return 0.5 * f * f * f + 1.0


def lerp(a: float, b: float, t: float) -> float:
    """Linear interpolation between ``a`` and ``b`` by factor ``t``."""
    return a + (b - a) * t


def blend_color(a: Color, b: Color, t: float) -> Color:
    """Interpolate between two RGB colors by ``t`` in ``[0, 1]`` (clamped)."""
    t = max(0.0, min(1.0, t))
    return (lerp(a[0], b[0], t), lerp(a[1], b[1], t), lerp(a[2], b[2], t))


class AvatarRenderer:
    """Stateful, animatable renderer for the circular avatar.

    Args:
        theme: Name of a theme in :data:`COLOR_THEMES` (falls back to default).
        animation_style: One of ``pulse``/``rings``/``waveform``/``minimal``.
        animation_speed: Multiplier on animation rates (1.0 = normal).
        animations_enabled: When ``False``, animations are frozen (static glyph)
            -- useful for reduced-motion accessibility or low-power devices.
    """

    def __init__(
        self,
        theme: str = DEFAULT_THEME,
        animation_style: str = "pulse",
        animation_speed: float = 1.0,
        animations_enabled: bool = True,
        lipsync_enabled: bool = True,
        mouth_style: str = "natural",
        viseme_speed: float = 14.0,
        lipsync_latency: float = 0.05,
        lipsync_debug: bool = False,
        mouth_animator: Optional[MouthAnimator] = None,
        audio_sync: Optional[AudioVisemeSync] = None,
    ) -> None:
        self.theme = theme if theme in COLOR_THEMES else DEFAULT_THEME
        self.animation_style = animation_style
        self.animation_speed = max(0.1, float(animation_speed))
        self.animations_enabled = bool(animations_enabled)

        self._colors: Dict[str, Color] = COLOR_THEMES[self.theme]
        self._state = UIState.IDLE
        self._prev_state = UIState.IDLE
        self._phase = 0.0          # ever-advancing animation phase (seconds * speed)
        self._transition = 1.0     # 0..1 progress of prev->current cross-fade
        self._audio_level = 0.0    # 0..1 external level for SPEAKING reactivity

        # -- lip-sync (M3.2) ------------------------------------------------
        self.lipsync_enabled = bool(lipsync_enabled)
        self.lipsync_debug = bool(lipsync_debug)
        self._mouth = mouth_animator or MouthAnimator(
            interpolation_speed=viseme_speed, style=mouth_style
        )
        self._sync = audio_sync or AudioVisemeSync(latency_offset=lipsync_latency)
        # True from the moment a timeline is set until SPEAKING ends, so the
        # mouth (not the fallback bar) is drawn for the whole utterance.
        self._lipsync_engaged = False

    # -- public state API --------------------------------------------------

    @property
    def state(self) -> UIState:
        """The current target :class:`UIState`."""
        return self._state

    @property
    def transition(self) -> float:
        """Cross-fade progress in ``[0, 1]`` (1.0 == settled on current state)."""
        return self._transition

    def set_theme(self, theme: str) -> None:
        """Switch the active color theme (falls back to default if unknown)."""
        self.theme = theme if theme in COLOR_THEMES else DEFAULT_THEME
        self._colors = COLOR_THEMES[self.theme]

    def set_state(self, state) -> None:
        """Set the target state, starting a smooth cross-fade from the current one.

        Accepts a :class:`UIState` or anything :meth:`UIState.from_voice_state`
        understands. Re-setting the same state is a no-op (keeps the animation
        running uninterrupted).
        """
        if not isinstance(state, UIState):
            state = UIState.from_voice_state(state)
        if state is self._state:
            return
        self._prev_state = self._state
        self._state = state
        self._transition = 0.0
        # Leaving SPEAKING: end any lip-sync session so the mouth eases shut and
        # the fallback bar is not shown on the next utterance by mistake.
        if state is not UIState.SPEAKING:
            self._sync.stop()
            self._lipsync_engaged = False

    def set_audio_level(self, level: float) -> None:
        """Feed a 0..1 audio level used to make the SPEAKING animation reactive."""
        try:
            self._audio_level = max(0.0, min(1.0, float(level)))
        except (TypeError, ValueError):
            self._audio_level = 0.0

    # -- lip-sync API (M3.2) ----------------------------------------------

    def set_viseme_timeline(self, timeline) -> None:
        """Begin lip-syncing to ``timeline`` (a
        :class:`~mimosa.voice.phoneme_extractor.VisemeTimeline`).

        Safe to call regardless of state; it anchors the audio-sync clock to
        *now*. When ``lipsync_enabled`` is ``False`` or the timeline is empty,
        this is a no-op and the basic speaking animation is used instead.
        """
        if not self.lipsync_enabled or timeline is None or timeline.is_empty:
            return
        self._sync.start(timeline)
        self._lipsync_engaged = True

    def clear_viseme_timeline(self) -> None:
        """Stop lip-sync immediately (mouth eases back to rest)."""
        self._sync.stop()
        self._lipsync_engaged = False

    def set_lipsync_enabled(self, enabled: bool) -> None:
        """Toggle the lip-sync feature at runtime."""
        self.lipsync_enabled = bool(enabled)
        if not self.lipsync_enabled:
            self.clear_viseme_timeline()

    @property
    def lipsync_active(self) -> bool:
        """True while a viseme timeline is engaged for the current utterance."""
        return self._lipsync_engaged

    @property
    def audio_sync(self) -> AudioVisemeSync:
        """The underlying :class:`AudioVisemeSync` (for resync/pause/resume)."""
        return self._sync

    @property
    def mouth(self) -> MouthAnimator:
        """The underlying :class:`MouthAnimator` (current mouth shape, etc.)."""
        return self._mouth

    @classmethod
    def from_config(cls, config) -> "AvatarRenderer":
        """Build a renderer from a :class:`~mimosa.ui.ui_config.UIConfig`."""
        return cls(
            theme=config.theme,
            animation_style=config.animation_style,
            animation_speed=config.animation_speed,
            animations_enabled=config.animations_enabled,
            lipsync_enabled=getattr(config, "lipsync_enabled", True),
            mouth_style=getattr(config, "mouth_style", "natural"),
            viseme_speed=getattr(config, "viseme_speed", 14.0),
            lipsync_latency=getattr(config, "lipsync_latency", 0.05),
            lipsync_debug=getattr(config, "lipsync_debug", False),
        )

    # -- animation math (pure) --------------------------------------------

    def tick(self, dt: float) -> None:
        """Advance the animation by ``dt`` seconds.

        Updates the running phase and the prev->current transition factor. Pure
        and deterministic -- no drawing happens here, so it is fully unit
        testable. ``dt`` is clamped to a sane range to stay stable if the frame
        clock hiccups.
        """
        try:
            dt = float(dt)
        except (TypeError, ValueError):
            return
        dt = max(0.0, min(0.25, dt))
        if self.animations_enabled:
            self._phase += dt * self.animation_speed
        if self._transition < 1.0:
            self._transition = min(1.0, self._transition + dt / TRANSITION_SECONDS)
        self._tick_lipsync(dt)

    def _tick_lipsync(self, dt: float) -> None:
        """Advance the mouth animator from the audio-sync window each frame.

        While a timeline is engaged we sample ``(current, next, blend)`` from the
        sync clock and steer the mouth toward it; once playback runs past the
        timeline we let the mouth ease back to rest (target = silence). Cheap and
        exception-safe so a lip-sync glitch never breaks the frame loop.
        """
        if not self.lipsync_enabled:
            return
        try:
            if self._lipsync_engaged and self._sync.active and not self._sync.is_finished():
                cur, nxt, blend = self._sync.current_window()
                self._mouth.set_window(cur, nxt, blend)
            elif self._lipsync_engaged:
                # Past the end of the timeline: ease the mouth closed.
                self._mouth.set_target_viseme(_SILENCE_VISEME)
            self._mouth.update(dt)
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("lip-sync tick failed: %s", exc)

    def base_color(self) -> Color:
        """The themed background disc color."""
        return self._colors.get("base", (0.1, 0.12, 0.18))

    def accent_color(self) -> Color:
        """The current accent color, cross-fading from the previous state's color."""
        cur = self._colors.get(self._state.value, self._colors["idle"])
        prev = self._colors.get(self._prev_state.value, cur)
        return blend_color(prev, cur, ease_in_out(self._transition))

    def breathing_scale(self, lo: float = 0.92, hi: float = 1.0) -> float:
        """IDLE breathing scale oscillating between ``lo`` and ``hi``."""
        if not self.animations_enabled:
            return hi
        # ~0.25 Hz gentle breath.
        s = (math.sin(self._phase * 2.0 * math.pi * 0.25) + 1.0) / 2.0
        return lerp(lo, hi, s)

    def ring_progress(self, count: int = 3) -> Tuple[float, ...]:
        """LISTENING ring expansion fractions (0..1) for ``count`` staggered rings."""
        if count <= 0:
            return ()
        out = []
        for i in range(count):
            frac = (self._phase * 0.6 + i / count) % 1.0
            out.append(frac)
        return tuple(out)

    def thinking_dots(self, count: int = 3) -> Tuple[float, ...]:
        """PROCESSING per-dot brightness (0..1) for an orbiting/pulsing effect."""
        if count <= 0:
            return ()
        out = []
        for i in range(count):
            ph = self._phase * 2.0 * math.pi * 0.9 - i * (math.pi / 3.0)
            out.append((math.sin(ph) + 1.0) / 2.0)
        return tuple(out)

    def speaking_level(self) -> float:
        """SPEAKING mouth/level value in 0..1.

        Uses the externally supplied audio level when present; otherwise
        synthesizes a lively oscillation so the avatar still "talks" even when
        no real audio level is wired up.
        """
        if self._audio_level > 0.0:
            return self._audio_level
        if not self.animations_enabled:
            return 0.5
        osc = (math.sin(self._phase * 2.0 * math.pi * 3.0) + 1.0) / 2.0
        osc2 = (math.sin(self._phase * 2.0 * math.pi * 5.3) + 1.0) / 2.0
        return 0.2 + 0.8 * (0.6 * osc + 0.4 * osc2)

    # -- drawing -----------------------------------------------------------

    def draw(self, cr, width: int, height: int) -> None:
        """Render the avatar into Cairo context ``cr`` sized ``width`` x ``height``.

        Imports ``cairo`` lazily; if it is unavailable this is a no-op (the
        window simply shows nothing rather than crashing).
        """
        try:
            import cairo  # noqa: F401
        except Exception:  # pragma: no cover - headless without pycairo
            return

        cx, cy = width / 2.0, height / 2.0
        radius = min(width, height) / 2.0 * 0.86
        accent = self.accent_color()
        base = self.base_color()

        # Clear to fully transparent (window is an RGBA, frameless surface).
        cr.save()
        cr.set_operator(cairo.OPERATOR_CLEAR)
        cr.paint()
        cr.restore()

        scale = self.breathing_scale() if self._state is UIState.IDLE else 1.0
        disc_r = radius * scale

        # Base disc with a soft radial gradient.
        grad = cairo.RadialGradient(cx, cy, disc_r * 0.1, cx, cy, disc_r)
        grad.add_color_stop_rgba(0.0, base[0] * 1.4, base[1] * 1.4, base[2] * 1.4, 0.96)
        grad.add_color_stop_rgba(1.0, base[0], base[1], base[2], 0.92)
        cr.set_source(grad)
        cr.arc(cx, cy, disc_r, 0, 2 * math.pi)
        cr.fill()

        # Accent rim.
        cr.set_source_rgba(accent[0], accent[1], accent[2], 0.9)
        cr.set_line_width(max(2.0, radius * 0.04))
        cr.arc(cx, cy, disc_r, 0, 2 * math.pi)
        cr.stroke()

        # State-specific overlay.
        state = self._state
        if state is UIState.LISTENING:
            self._draw_rings(cr, cx, cy, radius, accent, cairo)
        elif state is UIState.PROCESSING:
            self._draw_thinking(cr, cx, cy, radius, accent, cairo)
        elif state is UIState.SPEAKING:
            self._draw_speaking(cr, cx, cy, radius, accent, cairo)
        elif state in (UIState.DISABLED, UIState.PAUSED):
            self._draw_core(cr, cx, cy, radius * 0.5, (0.5, 0.5, 0.52), 0.5, cairo)
        else:  # IDLE
            self._draw_core(cr, cx, cy, radius * 0.55, accent, 0.85, cairo)

    # -- drawing helpers ---------------------------------------------------

    def _draw_core(self, cr, cx, cy, r, color, alpha, cairo) -> None:
        grad = cairo.RadialGradient(cx, cy, 0, cx, cy, r)
        grad.add_color_stop_rgba(0.0, color[0], color[1], color[2], alpha)
        grad.add_color_stop_rgba(1.0, color[0], color[1], color[2], 0.0)
        cr.set_source(grad)
        cr.arc(cx, cy, r, 0, 2 * math.pi)
        cr.fill()

    def _draw_rings(self, cr, cx, cy, radius, accent, cairo) -> None:
        for frac in self.ring_progress():
            r = radius * (0.25 + 0.75 * frac)
            alpha = max(0.0, 0.7 * (1.0 - frac))
            cr.set_source_rgba(accent[0], accent[1], accent[2], alpha)
            cr.set_line_width(max(1.5, radius * 0.03))
            cr.arc(cx, cy, r, 0, 2 * math.pi)
            cr.stroke()
        self._draw_core(cr, cx, cy, radius * 0.3, accent, 0.9, cairo)

    def _draw_thinking(self, cr, cx, cy, radius, accent, cairo) -> None:
        dots = self.thinking_dots(3)
        orbit = radius * 0.45
        spacing = radius * 0.32
        start_x = cx - spacing
        for i, b in enumerate(dots):
            x = start_x + i * spacing
            dot_r = radius * 0.10 * (0.6 + 0.4 * b)
            cr.set_source_rgba(accent[0], accent[1], accent[2], 0.3 + 0.7 * b)
            cr.arc(x, cy, dot_r, 0, 2 * math.pi)
            cr.fill()

    def _draw_speaking(self, cr, cx, cy, radius, accent, cairo) -> None:
        # Lip-sync path: draw the animated mouth layered over the avatar base.
        if self.lipsync_enabled and self._lipsync_engaged:
            self._mouth.draw(cr, cx, cy, radius * 1.05, accent, cairo)
            if self.lipsync_debug:
                self._draw_lipsync_debug(cr, cx, cy, radius, cairo)
            return
        # Fallback: the original reactive "mouth bar".
        self._draw_speaking_bar(cr, cx, cy, radius, accent)

    def _draw_speaking_bar(self, cr, cx, cy, radius, accent) -> None:
        level = self.speaking_level()
        # A symmetric "mouth" bar that grows with the level.
        bar_w = radius * 1.0
        bar_h = max(radius * 0.08, radius * 0.6 * level)
        x = cx - bar_w / 2.0
        y = cy - bar_h / 2.0
        cr.set_source_rgba(accent[0], accent[1], accent[2], 0.9)
        radius_corner = bar_h / 2.0
        # rounded rectangle
        cr.new_sub_path()
        cr.arc(x + radius_corner, y + radius_corner, radius_corner, math.pi, 1.5 * math.pi)
        cr.arc(x + bar_w - radius_corner, y + radius_corner, radius_corner, 1.5 * math.pi, 0)
        cr.arc(x + bar_w - radius_corner, y + bar_h - radius_corner, radius_corner, 0, 0.5 * math.pi)
        cr.arc(x + radius_corner, y + bar_h - radius_corner, radius_corner, 0.5 * math.pi, math.pi)
        cr.close_path()
        cr.fill()

    def _draw_lipsync_debug(self, cr, cx, cy, radius, cairo) -> None:
        """Overlay the current viseme name + playback position for tuning."""
        try:
            cur, nxt, blend = self._sync.current_window()
            pos = self._sync.position()
            dur = self._sync.timeline.duration
            label = f"{cur.value}->{nxt.value} {blend:.2f} | {pos:.2f}/{dur:.2f}s"
            cr.save()
            cr.set_source_rgba(1.0, 1.0, 1.0, 0.85)
            cr.set_font_size(max(8.0, radius * 0.10))
            cr.move_to(cx - radius * 0.9, cy + radius * 0.85)
            cr.show_text(label)
            cr.restore()
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("lip-sync debug overlay failed: %s", exc)

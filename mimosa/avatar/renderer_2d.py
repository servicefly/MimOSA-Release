"""2D sprite avatar renderer -- skeleton (Milestone 8.1).

This is the *universal baseline* renderer for the v2.0.0 avatar system: an
animated 2D character sprite that runs on essentially any desktop (GPU, CPU, or
low-end) because it is just a Cairo draw loop.

**Scope of Milestone 8.1:** infrastructure only. This renderer can be
constructed, wired into the avatar window, and asked to draw a *placeholder*
head-and-shoulders silhouette. The real sprite loading, per-state animation
frames, blinking, lip-sync mouth shapes, and gesture layers land in later
milestones (M8.2 generation, M8.3 animation). The animation-state plumbing is
stubbed here with clearly-marked placeholder methods so those milestones have a
stable structure to fill in.

Like :mod:`mimosa.ui.avatar_renderer`, ``cairo`` is imported lazily inside
:meth:`draw` so this module imports fine on headless machines.
"""

from __future__ import annotations

import logging
import math
from typing import Optional

from mimosa.avatar.base_renderer import BaseAvatarRenderer
from mimosa.avatar.animator import Animator, AnimationState
from mimosa.avatar.mouth_shapes import get_mouth_params, MouthShape
from mimosa.ui.state_bridge import UIState
from mimosa.ui.ui_config import COLOR_THEMES, DEFAULT_THEME, UIConfig

logger = logging.getLogger(__name__)

#: Animation states the sprite renderer will support. In M8.1 these are just
#: identifiers used to select a (future) sprite frame set; the mapping from
#: :class:`UIState` to an animation name lives in :meth:`animation_for_state`.
ANIM_IDLE = "idle"
ANIM_LISTENING = "listening"
ANIM_THINKING = "thinking"
ANIM_SPEAKING = "speaking"
ANIM_SLEEPING = "sleeping"

VALID_ANIMATIONS = (
    ANIM_IDLE,
    ANIM_LISTENING,
    ANIM_THINKING,
    ANIM_SPEAKING,
    ANIM_SLEEPING,
)


class Sprite2DRenderer(BaseAvatarRenderer):
    """Animated 2D sprite renderer (skeleton).

    Args:
        config: UI preferences (theme, size). A default :class:`UIConfig` is
            used when omitted -- injectable for tests.
        sprite_path: Optional path to a custom sprite / sprite-sheet. When
            ``None`` the renderer draws a bundled placeholder silhouette.
        theme: Color-theme name (defaults to the config's / ``"aurora"``).
    """

    tier = "2d"

    def __init__(
        self,
        config: Optional[UIConfig] = None,
        sprite_path: Optional[str] = None,
        theme: Optional[str] = None,
    ) -> None:
        super().__init__(tier="2d")
        self.config = config or UIConfig()
        self.sprite_path = sprite_path
        theme_name = theme or getattr(self.config, "theme", DEFAULT_THEME)
        self._colors = COLOR_THEMES.get(theme_name, COLOR_THEMES[DEFAULT_THEME])
        self._current_animation = ANIM_IDLE
        # Placeholder frame bookkeeping for future sprite-sheet playback.
        self._frame_index = 0
        self._frames_per_animation = 1  # replaced when real sheets load (M8.2)
        
        # M8.3: Animation system
        self.animator = Animator()

    # -- lifecycle ---------------------------------------------------------

    def load(self) -> bool:
        """Load sprites. Skeleton: succeeds with the built-in placeholder.

        Real sprite-sheet loading arrives in M8.2. For now we mark the renderer
        loaded so the window treats it as ready; a genuinely missing custom
        sprite is non-fatal (we fall back to the placeholder silhouette).
        """
        try:
            if self.sprite_path:
                logger.debug("Custom sprite requested (%s); deferred to M8.2", self.sprite_path)
            self._loaded = True
            return True
        except Exception:  # pragma: no cover - defensive
            logger.debug("Sprite2DRenderer.load failed", exc_info=True)
            self._loaded = False
            return False

    def dispose(self) -> None:
        """Release sprite resources. Skeleton: nothing held yet."""
        self._loaded = False

    # -- animation-state plumbing (placeholders for M8.3) ------------------

    @staticmethod
    def animation_for_state(state: UIState) -> str:
        """Map a :class:`UIState` to a sprite animation name.

        Placeholder mapping used until real per-state frame sets exist.
        """
        mapping = {
            UIState.IDLE: ANIM_IDLE,
            UIState.LISTENING: ANIM_LISTENING,
            UIState.PROCESSING: ANIM_THINKING,
            UIState.SPEAKING: ANIM_SPEAKING,
            UIState.PAUSED: ANIM_SLEEPING,
            UIState.DISABLED: ANIM_SLEEPING,
        }
        return mapping.get(state, ANIM_IDLE)

    def set_state(self, state: UIState) -> None:
        """Transition state and select the matching animation."""
        super().set_state(state)
        self._current_animation = self.animation_for_state(self._state)
        self._frame_index = 0
        
        # M8.3: Update animator state
        anim_state_map = {
            UIState.IDLE: AnimationState.IDLE,
            UIState.LISTENING: AnimationState.LISTENING,
            UIState.PROCESSING: AnimationState.THINKING,
            UIState.SPEAKING: AnimationState.SPEAKING,
            UIState.PAUSED: AnimationState.IDLE,
            UIState.DISABLED: AnimationState.IDLE,
        }
        anim_state = anim_state_map.get(self._state, AnimationState.IDLE)
        self.animator.set_state(anim_state)

    def update(self, dt: float) -> None:
        """Advance animation clock and (placeholder) frame index."""
        super().update(dt)
        # Placeholder frame advance -- real sprite sheets replace this in M8.3.
        if self._frames_per_animation > 1:
            steps = int(self._elapsed * 12)  # ~12 fps sprite playback
            self._frame_index = steps % self._frames_per_animation

    @property
    def current_animation(self) -> str:
        return self._current_animation

    @property
    def frame_index(self) -> int:
        return self._frame_index

    # -- drawing -----------------------------------------------------------

    def draw(self, ctx, width: int, height: int) -> None:
        """Paint the placeholder head-and-shoulders silhouette.

        This draws a simple, theme-colored character placeholder so the avatar
        window is visibly *not* the circle during infrastructure bring-up. Real
        sprite blitting replaces this in M8.2/M8.3. Imports ``cairo`` lazily and
        never raises (a failed draw just leaves the frame blank).
        """
        try:
            self._draw_placeholder(ctx, width, height)
        except Exception:  # pragma: no cover - drawing must never crash the UI
            logger.debug("Sprite2DRenderer.draw failed", exc_info=True)

    def _draw_placeholder(self, ctx, width: int, height: int) -> None:
        """Draw animated character with emotion, mouth, and gestures (M8.3)."""
        # M8.3: Get current animation frame
        anim_frame = self.animator.update()
        visuals = anim_frame.emotion_visuals
        
        cx = width / 2.0
        # Pick a state color and apply emotion tint
        state_key = {
            UIState.IDLE: "idle",
            UIState.LISTENING: "listening",
            UIState.PROCESSING: "processing",
            UIState.SPEAKING: "speaking",
        }.get(self._state, "idle")
        r, g, b = self._colors.get(state_key, self._colors["idle"])
        base = self._colors.get("base", (0.1, 0.12, 0.18))
        
        # Apply emotion tint
        r = r * visuals.color_tint[0]
        g = g * visuals.color_tint[1]
        b = b * visuals.color_tint[2]
        
        # Apply brightness
        brightness = visuals.brightness
        r *= brightness
        g *= brightness
        b *= brightness

        # Background disc (matches the window's rounded footprint).
        radius = min(width, height) / 2.0
        ctx.save()
        ctx.arc(cx, height / 2.0, radius, 0, 2 * math.pi)
        ctx.set_source_rgba(base[0], base[1], base[2], 0.9)
        ctx.fill()
        ctx.restore()

        # Apply scale and rotation from emotion
        ctx.save()
        ctx.translate(cx, height / 2.0)
        ctx.scale(visuals.scale, visuals.scale)
        ctx.rotate(visuals.rotation * math.pi / 180.0)
        
        # Apply pulse (breathing)
        pulse_scale = 1.0 + visuals.pulse_amplitude * math.sin(
            anim_frame.time * visuals.pulse_rate * 2 * math.pi
        )
        ctx.scale(pulse_scale, pulse_scale)
        
        ctx.translate(-cx, -height / 2.0)

        # Shoulders: a wide rounded arc across the bottom.
        shoulder_w = width * 0.7
        shoulder_top = height * 0.62
        ctx.set_source_rgba(r, g, b, 0.85)
        ctx.arc(cx, height * 1.15, shoulder_w / 2.0, math.pi, 2 * math.pi)
        ctx.rectangle(cx - shoulder_w / 2.0, shoulder_top, shoulder_w, height - shoulder_top)
        ctx.fill()

        # Head: a circle centered in the upper third.
        head_r = min(width, height) * 0.22
        head_cy = height * 0.40
        ctx.arc(cx, head_cy, head_r, 0, 2 * math.pi)
        ctx.set_source_rgba(r, g, b, 0.95)
        ctx.fill()
        
        # Eyes (with blinking)
        if not anim_frame.blink_closed:
            eye_y = head_cy - head_r * 0.15
            eye_r = head_r * 0.12
            # Left eye
            ctx.arc(cx - head_r * 0.35, eye_y, eye_r, 0, 2 * math.pi)
            ctx.set_source_rgba(1.0, 1.0, 1.0, 0.9)
            ctx.fill()
            # Right eye
            ctx.arc(cx + head_r * 0.35, eye_y, eye_r, 0, 2 * math.pi)
            ctx.fill()
            # Pupils
            ctx.arc(cx - head_r * 0.35, eye_y, eye_r * 0.5, 0, 2 * math.pi)
            ctx.set_source_rgba(0.1, 0.1, 0.2, 0.9)
            ctx.fill()
            ctx.arc(cx + head_r * 0.35, eye_y, eye_r * 0.5, 0, 2 * math.pi)
            ctx.fill()
        else:
            # Closed eyes (horizontal lines)
            eye_y = head_cy - head_r * 0.15
            eye_w = head_r * 0.25
            ctx.set_line_width(2)
            ctx.set_source_rgba(0.1, 0.1, 0.2, 0.8)
            ctx.move_to(cx - head_r * 0.35 - eye_w/2, eye_y)
            ctx.line_to(cx - head_r * 0.35 + eye_w/2, eye_y)
            ctx.stroke()
            ctx.move_to(cx + head_r * 0.35 - eye_w/2, eye_y)
            ctx.line_to(cx + head_r * 0.35 + eye_w/2, eye_y)
            ctx.stroke()

        # Mouth (animated with lip sync)
        mouth_params = get_mouth_params(anim_frame.mouth_shape)
        mouth_y = head_cy + head_r * 0.35
        mouth_w = head_r * mouth_params['width']
        mouth_h = head_r * mouth_params['height']
        
        ctx.save()
        ctx.translate(cx, mouth_y)
        if mouth_params['roundness'] > 0.5:
            # Round mouth (O shape)
            ctx.scale(mouth_w / mouth_h, 1.0)
            ctx.arc(0, 0, mouth_h / 2, 0, 2 * math.pi)
        else:
            # Elliptical mouth
            ctx.scale(1.0, mouth_h / (mouth_w if mouth_w > 0 else 1.0))
            ctx.arc(0, 0, mouth_w / 2, 0, 2 * math.pi)
        ctx.set_source_rgba(0.1, 0.05, 0.05, 0.8)
        ctx.fill()
        ctx.restore()

        # A faint highlight so the placeholder reads as a face, not a blob.
        ctx.arc(cx - head_r * 0.3, head_cy - head_r * 0.3, head_r * 0.35, 0, 2 * math.pi)
        ctx.set_source_rgba(1.0, 1.0, 1.0, 0.15 + 0.15 * self._audio_level)
        ctx.fill()
        
        ctx.restore()  # Restore from scale/rotate/pulse transform

    def start_speaking(self, text: str, duration: float) -> None:
        """
        Start speaking animation with lip sync.
        
        Args:
            text: Text being spoken
            duration: Estimated speech duration in seconds
        """
        self.animator.start_speaking(text, duration)
    
    def stop_speaking(self) -> None:
        """Stop speaking animation."""
        self.animator.stop_speaking()
    
    @classmethod
    def from_config(cls, config: Optional[UIConfig] = None, sprite_path: Optional[str] = None) -> "Sprite2DRenderer":
        """Build a renderer from a :class:`UIConfig` (mirrors AvatarRenderer)."""
        return cls(config=config, sprite_path=sprite_path)

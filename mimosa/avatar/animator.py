"""
Avatar animation state machine and controller.

Manages animation states, transitions, and coordinates between
emotions, lip sync, and gestures for cohesive avatar behavior.
"""

import time
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from .emotions import EmotionState, EmotionVisuals, get_emotion_visuals, blend_emotions
from .lip_sync import LipSyncEngine, MouthShape
from .gestures import GestureController, GestureType, GestureKeyframe


class AnimationState(Enum):
    """High-level animation states for the avatar."""
    
    IDLE = "idle"
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"


# Map animation states to emotion states
STATE_TO_EMOTION = {
    AnimationState.IDLE: EmotionState.IDLE,
    AnimationState.LISTENING: EmotionState.LISTENING,
    AnimationState.THINKING: EmotionState.THINKING,
    AnimationState.SPEAKING: EmotionState.SPEAKING,
}


@dataclass
class AnimationFrame:
    """Complete animation state for a single frame."""
    
    # Emotion/visual state
    emotion_visuals: EmotionVisuals
    
    # Mouth state (for lip sync)
    mouth_shape: MouthShape
    
    # Gesture state
    gesture_keyframe: Optional[GestureKeyframe] = None
    
    # Additional animation parameters
    time: float = 0.0  # Current animation time
    blink_closed: bool = False  # Whether eyes are currently blinking


class Animator:
    """
    Main animation controller for the avatar.
    
    Manages state transitions, blending, and coordinates all animation
    subsystems (emotions, lip sync, gestures).
    """
    
    def __init__(self):
        # Current state
        self.current_state = AnimationState.IDLE
        self.target_state = AnimationState.IDLE
        
        # Transition blending
        self.transition_progress: float = 1.0  # 1.0 = fully transitioned
        self.transition_duration: float = 0.3  # seconds
        self.transition_start_time: Optional[float] = None
        
        # Subsystems
        self.lip_sync = LipSyncEngine()
        self.gesture_controller = GestureController()
        
        # Timing
        self.animation_start_time = time.time()
        self.last_blink_time = time.time()
        self.next_blink_interval = 4.0  # seconds
        
        # Manual emotion override (for expressions like happy/concerned)
        self.emotion_override: Optional[EmotionState] = None
        self.emotion_override_end_time: Optional[float] = None

        # Optional frame-rate governor for auto-throttling (item #10). Off by
        # default so behaviour is unchanged unless a renderer attaches one.
        self.governor = None
        
    def set_state(self, new_state: AnimationState) -> None:
        """
        Transition to a new animation state.
        
        Args:
            new_state: Target animation state
        """
        if new_state == self.target_state:
            return  # Already transitioning to this state
        
        # Start transition
        self.current_state = self.target_state  # Current becomes old target
        self.target_state = new_state
        self.transition_start_time = time.time()
        self.transition_progress = 0.0
        
    def set_emotion(self, emotion: EmotionState, duration: float = 2.0) -> None:
        """
        Override current emotion temporarily.
        
        Useful for expressing reactions (happy, concerned) that aren't
        tied to the main animation state.
        
        Args:
            emotion: Emotion to display
            duration: How long to display it (seconds)
        """
        self.emotion_override = emotion
        self.emotion_override_end_time = time.time() + duration
        
    def play_gesture(self, gesture_type: GestureType) -> bool:
        """
        Play a gesture animation.
        
        Args:
            gesture_type: Type of gesture to play
            
        Returns:
            True if gesture started
        """
        return self.gesture_controller.play_gesture(gesture_type, time.time())
    
    def start_speaking(self, text: str, duration: float) -> None:
        """
        Start speaking animation with basic lip sync.
        
        Args:
            text: Text being spoken
            duration: Speech duration in seconds
        """
        self.set_state(AnimationState.SPEAKING)
        self.lip_sync.load_from_text(text, duration)
        self.lip_sync.start()
        
    def stop_speaking(self) -> None:
        """Stop speaking animation."""
        self.lip_sync.stop()
        if self.current_state == AnimationState.SPEAKING:
            self.set_state(AnimationState.IDLE)
    
    def update(self) -> AnimationFrame:
        """
        Update animation and return current frame.
        
        Should be called regularly (e.g., 30-60 FPS) to update animation.
        
        Returns:
            Current animation frame with all parameters
        """
        current_time = time.time()

        # Feed the optional frame-rate governor so it can recommend throttling
        # on weak hardware. Sampling only; the renderer decides how to react.
        if self.governor is not None:
            try:
                self.governor.record_frame(current_time)
            except Exception:  # pragma: no cover - never break the anim loop
                pass

        # Update transition progress
        if self.transition_start_time is not None:
            elapsed = current_time - self.transition_start_time
            self.transition_progress = min(1.0, elapsed / self.transition_duration)
            
            if self.transition_progress >= 1.0:
                # Transition complete
                self.current_state = self.target_state
                self.transition_start_time = None
        
        # Determine effective emotion
        effective_emotion = self._get_effective_emotion(current_time)
        
        # Get emotion visuals (with blending if transitioning)
        if self.transition_progress < 1.0:
            current_emotion = STATE_TO_EMOTION.get(self.current_state, EmotionState.IDLE)
            target_emotion = effective_emotion
            emotion_visuals = blend_emotions(
                current_emotion,
                target_emotion,
                self.transition_progress
            )
        else:
            emotion_visuals = get_emotion_visuals(effective_emotion)
        
        # Get mouth shape from lip sync
        mouth_shape = self.lip_sync.get_current_mouth_shape()
        
        # If lip sync finished, stop speaking
        if self.target_state == AnimationState.SPEAKING and self.lip_sync.is_finished():
            self.stop_speaking()
        
        # Get gesture keyframe
        gesture_keyframe = self.gesture_controller.get_current_keyframe(current_time)
        
        # Update blinking
        blink_closed = self._update_blinking(current_time, emotion_visuals.blink_rate)
        
        return AnimationFrame(
            emotion_visuals=emotion_visuals,
            mouth_shape=mouth_shape,
            gesture_keyframe=gesture_keyframe,
            time=current_time - self.animation_start_time,
            blink_closed=blink_closed
        )
    
    def _get_effective_emotion(self, current_time: float) -> EmotionState:
        """
        Get the effective emotion considering overrides.
        
        Args:
            current_time: Current time
            
        Returns:
            Effective emotion state
        """
        # Check if emotion override is active
        if self.emotion_override is not None:
            if self.emotion_override_end_time is None or current_time < self.emotion_override_end_time:
                return self.emotion_override
            else:
                # Override expired
                self.emotion_override = None
                self.emotion_override_end_time = None
        
        # Use state-based emotion
        return STATE_TO_EMOTION.get(self.target_state, EmotionState.IDLE)
    
    def _update_blinking(self, current_time: float, blink_rate: float) -> bool:
        """
        Update blinking animation.
        
        Args:
            current_time: Current time
            blink_rate: Blinks per minute from emotion
            
        Returns:
            True if eyes should be closed for blinking
        """
        # Check if it's time for next blink
        if current_time - self.last_blink_time > self.next_blink_interval:
            self.last_blink_time = current_time
            
            # Calculate next blink interval based on blink rate
            # blink_rate is in blinks/minute
            if blink_rate > 0:
                avg_interval = 60.0 / blink_rate
                # Add some randomness (±30%)
                import random
                self.next_blink_interval = avg_interval * random.uniform(0.7, 1.3)
            else:
                self.next_blink_interval = 999999  # Very long time
        
        # Blink duration is ~0.15 seconds
        blink_duration = 0.15
        time_since_blink = current_time - self.last_blink_time
        
        return time_since_blink < blink_duration
    
    def reset(self) -> None:
        """Reset animator to initial state."""
        self.current_state = AnimationState.IDLE
        self.target_state = AnimationState.IDLE
        self.transition_progress = 1.0
        self.transition_start_time = None
        self.emotion_override = None
        self.emotion_override_end_time = None
        self.lip_sync.stop()
        self.gesture_controller.stop_gesture()
        self.animation_start_time = time.time()
        self.last_blink_time = time.time()

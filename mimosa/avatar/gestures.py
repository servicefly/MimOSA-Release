"""
Gesture system for avatar body language.

Defines arm/hand gestures that can be triggered during conversation
to add expressiveness beyond facial animation.
"""

from dataclasses import dataclass
from enum import Enum
from typing import List, Tuple, Optional


class GestureType(Enum):
    """Types of gestures the avatar can perform."""
    
    NONE = "none"  # No gesture / neutral
    WAVE = "wave"  # Friendly wave
    THINKING = "thinking"  # Hand to chin
    EXPLAINING = "explaining"  # Hand gestures while speaking
    THUMBS_UP = "thumbs_up"  # Approval
    SHRUG = "shrug"  # Uncertainty
    POINT = "point"  # Pointing gesture


@dataclass
class GestureKeyframe:
    """A single keyframe in a gesture animation."""
    
    time: float  # Time in seconds from gesture start
    
    # Left arm position (x, y relative to body center)
    left_arm_x: float = 0.0
    left_arm_y: float = 0.0
    
    # Right arm position
    right_arm_x: float = 0.0
    right_arm_y: float = 0.0
    
    # Arm rotation (degrees)
    left_arm_rotation: float = 0.0
    right_arm_rotation: float = 0.0
    
    # Hand state
    left_hand_open: bool = True
    right_hand_open: bool = True


@dataclass
class Gesture:
    """A complete gesture animation."""
    
    gesture_type: GestureType
    duration: float  # Total duration in seconds
    keyframes: List[GestureKeyframe]
    loop: bool = False  # Whether to loop the gesture
    priority: int = 0  # Higher priority gestures interrupt lower


# Gesture library - predefined animations
GESTURE_LIBRARY = {
    GestureType.WAVE: Gesture(
        gesture_type=GestureType.WAVE,
        duration=1.5,
        loop=False,
        priority=5,
        keyframes=[
            # Start: arms at sides
            GestureKeyframe(time=0.0, right_arm_x=0.3, right_arm_y=-0.2),
            # Raise arm
            GestureKeyframe(time=0.3, right_arm_x=0.3, right_arm_y=0.5, right_arm_rotation=20),
            # Wave left
            GestureKeyframe(time=0.6, right_arm_x=0.2, right_arm_y=0.5, right_arm_rotation=30),
            # Wave right
            GestureKeyframe(time=0.9, right_arm_x=0.4, right_arm_y=0.5, right_arm_rotation=10),
            # Wave left again
            GestureKeyframe(time=1.2, right_arm_x=0.2, right_arm_y=0.5, right_arm_rotation=30),
            # Lower arm
            GestureKeyframe(time=1.5, right_arm_x=0.3, right_arm_y=-0.2, right_arm_rotation=0),
        ]
    ),
    
    GestureType.THINKING: Gesture(
        gesture_type=GestureType.THINKING,
        duration=2.0,
        loop=True,
        priority=3,
        keyframes=[
            # Hand to chin
            GestureKeyframe(
                time=0.0,
                right_arm_x=0.1,
                right_arm_y=0.3,
                right_arm_rotation=-30,
                right_hand_open=False
            ),
            # Slight movement
            GestureKeyframe(
                time=1.0,
                right_arm_x=0.12,
                right_arm_y=0.32,
                right_arm_rotation=-28,
                right_hand_open=False
            ),
            # Back to start
            GestureKeyframe(
                time=2.0,
                right_arm_x=0.1,
                right_arm_y=0.3,
                right_arm_rotation=-30,
                right_hand_open=False
            ),
        ]
    ),
    
    GestureType.EXPLAINING: Gesture(
        gesture_type=GestureType.EXPLAINING,
        duration=3.0,
        loop=True,
        priority=4,
        keyframes=[
            # Both hands out
            GestureKeyframe(
                time=0.0,
                left_arm_x=-0.3,
                left_arm_y=0.1,
                right_arm_x=0.3,
                right_arm_y=0.1,
                left_hand_open=True,
                right_hand_open=True
            ),
            # Left hand up
            GestureKeyframe(
                time=0.75,
                left_arm_x=-0.35,
                left_arm_y=0.3,
                right_arm_x=0.25,
                right_arm_y=0.0,
                left_hand_open=True,
                right_hand_open=True
            ),
            # Right hand up
            GestureKeyframe(
                time=1.5,
                left_arm_x=-0.25,
                left_arm_y=0.0,
                right_arm_x=0.35,
                right_arm_y=0.3,
                left_hand_open=True,
                right_hand_open=True
            ),
            # Both hands center
            GestureKeyframe(
                time=2.25,
                left_arm_x=-0.2,
                left_arm_y=0.2,
                right_arm_x=0.2,
                right_arm_y=0.2,
                left_hand_open=True,
                right_hand_open=True
            ),
            # Back to start
            GestureKeyframe(
                time=3.0,
                left_arm_x=-0.3,
                left_arm_y=0.1,
                right_arm_x=0.3,
                right_arm_y=0.1,
                left_hand_open=True,
                right_hand_open=True
            ),
        ]
    ),
    
    GestureType.THUMBS_UP: Gesture(
        gesture_type=GestureType.THUMBS_UP,
        duration=1.2,
        loop=False,
        priority=6,
        keyframes=[
            # Start
            GestureKeyframe(time=0.0, right_arm_x=0.3, right_arm_y=-0.2),
            # Raise thumb
            GestureKeyframe(
                time=0.4,
                right_arm_x=0.3,
                right_arm_y=0.2,
                right_arm_rotation=15,
                right_hand_open=False
            ),
            # Hold
            GestureKeyframe(
                time=0.8,
                right_arm_x=0.3,
                right_arm_y=0.2,
                right_arm_rotation=15,
                right_hand_open=False
            ),
            # Lower
            GestureKeyframe(time=1.2, right_arm_x=0.3, right_arm_y=-0.2, right_arm_rotation=0),
        ]
    ),
    
    GestureType.SHRUG: Gesture(
        gesture_type=GestureType.SHRUG,
        duration=1.5,
        loop=False,
        priority=5,
        keyframes=[
            # Start
            GestureKeyframe(
                time=0.0,
                left_arm_x=-0.3,
                left_arm_y=-0.2,
                right_arm_x=0.3,
                right_arm_y=-0.2
            ),
            # Raise shoulders and hands
            GestureKeyframe(
                time=0.5,
                left_arm_x=-0.4,
                left_arm_y=0.3,
                right_arm_x=0.4,
                right_arm_y=0.3,
                left_arm_rotation=45,
                right_arm_rotation=-45,
                left_hand_open=True,
                right_hand_open=True
            ),
            # Hold
            GestureKeyframe(
                time=1.0,
                left_arm_x=-0.4,
                left_arm_y=0.3,
                right_arm_x=0.4,
                right_arm_y=0.3,
                left_arm_rotation=45,
                right_arm_rotation=-45,
                left_hand_open=True,
                right_hand_open=True
            ),
            # Lower
            GestureKeyframe(
                time=1.5,
                left_arm_x=-0.3,
                left_arm_y=-0.2,
                right_arm_x=0.3,
                right_arm_y=-0.2
            ),
        ]
    ),
    
    GestureType.POINT: Gesture(
        gesture_type=GestureType.POINT,
        duration=1.0,
        loop=False,
        priority=4,
        keyframes=[
            # Start
            GestureKeyframe(time=0.0, right_arm_x=0.3, right_arm_y=-0.2),
            # Point
            GestureKeyframe(
                time=0.3,
                right_arm_x=0.5,
                right_arm_y=0.1,
                right_arm_rotation=-20,
                right_hand_open=False
            ),
            # Hold
            GestureKeyframe(
                time=0.7,
                right_arm_x=0.5,
                right_arm_y=0.1,
                right_arm_rotation=-20,
                right_hand_open=False
            ),
            # Return
            GestureKeyframe(time=1.0, right_arm_x=0.3, right_arm_y=-0.2, right_arm_rotation=0),
        ]
    ),
}


class GestureController:
    """Manages gesture playback and blending."""
    
    def __init__(self):
        self.current_gesture: Optional[Gesture] = None
        self.gesture_start_time: Optional[float] = None
        self.is_playing: bool = False
        
    def play_gesture(self, gesture_type: GestureType, start_time: float) -> bool:
        """
        Start playing a gesture.
        
        Args:
            gesture_type: Type of gesture to play
            start_time: Time when gesture should start
            
        Returns:
            True if gesture started, False if blocked by higher priority
        """
        new_gesture = GESTURE_LIBRARY.get(gesture_type)
        
        if new_gesture is None:
            return False
        
        # Check priority - can we interrupt current gesture?
        if self.current_gesture is not None and self.is_playing:
            if new_gesture.priority <= self.current_gesture.priority:
                return False  # Current gesture has higher/equal priority
        
        self.current_gesture = new_gesture
        self.gesture_start_time = start_time
        self.is_playing = True
        
        return True
    
    def stop_gesture(self) -> None:
        """Stop current gesture."""
        self.is_playing = False
        self.current_gesture = None
        self.gesture_start_time = None
    
    def get_current_keyframe(self, current_time: float) -> Optional[GestureKeyframe]:
        """
        Get the interpolated keyframe for the current time.
        
        Args:
            current_time: Current time in seconds
            
        Returns:
            Interpolated keyframe, or None if no gesture playing
        """
        if not self.is_playing or self.current_gesture is None:
            return None
        
        if self.gesture_start_time is None:
            return None
        
        # Calculate elapsed time in gesture
        elapsed = current_time - self.gesture_start_time
        
        # Check if gesture finished
        if elapsed >= self.current_gesture.duration:
            if self.current_gesture.loop:
                # Loop gesture
                elapsed = elapsed % self.current_gesture.duration
            else:
                # Gesture finished
                self.stop_gesture()
                return None
        
        # Find keyframes to interpolate between
        keyframes = self.current_gesture.keyframes
        
        if not keyframes:
            return None
        
        # Find surrounding keyframes
        prev_kf = keyframes[0]
        next_kf = keyframes[0]
        
        for kf in keyframes:
            if kf.time <= elapsed:
                prev_kf = kf
            if kf.time >= elapsed and next_kf.time < elapsed:
                next_kf = kf
                break
        
        # If same keyframe or at exact time, return it
        if prev_kf.time == next_kf.time or elapsed == prev_kf.time:
            return prev_kf
        
        # Interpolate between keyframes
        t = (elapsed - prev_kf.time) / (next_kf.time - prev_kf.time)
        t = max(0.0, min(1.0, t))  # Clamp
        
        return GestureKeyframe(
            time=elapsed,
            left_arm_x=prev_kf.left_arm_x + (next_kf.left_arm_x - prev_kf.left_arm_x) * t,
            left_arm_y=prev_kf.left_arm_y + (next_kf.left_arm_y - prev_kf.left_arm_y) * t,
            right_arm_x=prev_kf.right_arm_x + (next_kf.right_arm_x - prev_kf.right_arm_x) * t,
            right_arm_y=prev_kf.right_arm_y + (next_kf.right_arm_y - prev_kf.right_arm_y) * t,
            left_arm_rotation=prev_kf.left_arm_rotation + (next_kf.left_arm_rotation - prev_kf.left_arm_rotation) * t,
            right_arm_rotation=prev_kf.right_arm_rotation + (next_kf.right_arm_rotation - prev_kf.right_arm_rotation) * t,
            left_hand_open=next_kf.left_hand_open,  # Discrete state
            right_hand_open=next_kf.right_hand_open,
        )

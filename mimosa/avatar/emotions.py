"""
Avatar emotion states and visual parameters.

Defines the core emotional states for the 2D avatar and their
visual characteristics (colors, scales, animations).
"""

from dataclasses import dataclass
from enum import Enum
from typing import Tuple, Optional


class EmotionState(Enum):
    """Core emotion states for avatar animation."""
    
    IDLE = "idle"
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"
    HAPPY = "happy"
    CONCERNED = "concerned"


@dataclass(frozen=True)
class EmotionVisuals:
    """Visual parameters for an emotion state."""
    
    # Color modulation (RGB, 0.0-1.0)
    color_tint: Tuple[float, float, float] = (1.0, 1.0, 1.0)
    
    # Scale modulation (1.0 = normal)
    scale: float = 1.0
    
    # Brightness modulation (1.0 = normal)
    brightness: float = 1.0
    
    # Pulse/breathing rate (Hz, 0 = none)
    pulse_rate: float = 0.0
    
    # Pulse amplitude (0.0-1.0)
    pulse_amplitude: float = 0.0
    
    # Rotation angle (degrees, for head tilt)
    rotation: float = 0.0
    
    # Blink rate (blinks per minute)
    blink_rate: float = 15.0
    
    # Expression intensity (0.0-1.0)
    intensity: float = 0.5


# Emotion visual presets
EMOTION_VISUALS = {
    EmotionState.IDLE: EmotionVisuals(
        color_tint=(0.9, 0.95, 1.0),  # Slight cool tint
        scale=1.0,
        brightness=0.8,
        pulse_rate=0.2,  # Slow breathing
        pulse_amplitude=0.05,
        blink_rate=15.0,
        intensity=0.3
    ),
    
    EmotionState.LISTENING: EmotionVisuals(
        color_tint=(0.7, 0.9, 1.0),  # Blue tint (attentive)
        scale=1.05,  # Slightly larger (alert)
        brightness=1.0,
        pulse_rate=0.5,  # Faster breathing
        pulse_amplitude=0.08,
        blink_rate=10.0,  # Less blinking (focused)
        intensity=0.7
    ),
    
    EmotionState.THINKING: EmotionVisuals(
        color_tint=(1.0, 0.95, 0.8),  # Warm yellow tint
        scale=0.98,  # Slightly smaller
        brightness=0.9,
        pulse_rate=0.3,
        pulse_amplitude=0.06,
        rotation=5.0,  # Slight head tilt
        blink_rate=20.0,  # More blinking (processing)
        intensity=0.6
    ),
    
    EmotionState.SPEAKING: EmotionVisuals(
        color_tint=(1.0, 1.0, 1.0),  # Neutral
        scale=1.02,
        brightness=1.1,  # Brighter
        pulse_rate=0.0,  # No breathing pulse (mouth moving)
        pulse_amplitude=0.0,
        blink_rate=12.0,
        intensity=0.8
    ),
    
    EmotionState.HAPPY: EmotionVisuals(
        color_tint=(1.0, 1.0, 0.9),  # Warm tint
        scale=1.08,  # Larger (expressive)
        brightness=1.2,
        pulse_rate=0.8,  # Fast, excited breathing
        pulse_amplitude=0.12,
        blink_rate=8.0,  # Less blinking (open expression)
        intensity=1.0
    ),
    
    EmotionState.CONCERNED: EmotionVisuals(
        color_tint=(1.0, 0.9, 0.85),  # Slightly warm/orange
        scale=0.95,  # Smaller (worried)
        brightness=0.85,
        pulse_rate=0.6,  # Faster breathing (anxious)
        pulse_amplitude=0.10,
        rotation=-3.0,  # Slight opposite tilt
        blink_rate=25.0,  # More blinking (nervous)
        intensity=0.7
    ),
}


def get_emotion_visuals(state: EmotionState) -> EmotionVisuals:
    """
    Get visual parameters for an emotion state.
    
    Args:
        state: The emotion state
        
    Returns:
        Visual parameters for that emotion
    """
    return EMOTION_VISUALS.get(state, EMOTION_VISUALS[EmotionState.IDLE])


def blend_emotions(
    state_a: EmotionState,
    state_b: EmotionState,
    blend: float
) -> EmotionVisuals:
    """
    Blend between two emotion states.
    
    Args:
        state_a: First emotion state
        state_b: Second emotion state
        blend: Blend factor (0.0 = all A, 1.0 = all B)
        
    Returns:
        Blended visual parameters
    """
    vis_a = get_emotion_visuals(state_a)
    vis_b = get_emotion_visuals(state_b)
    
    # Clamp blend to 0-1
    blend = max(0.0, min(1.0, blend))
    inv_blend = 1.0 - blend
    
    # Blend color tint
    color_tint = tuple(
        vis_a.color_tint[i] * inv_blend + vis_b.color_tint[i] * blend
        for i in range(3)
    )
    
    # Blend other parameters
    return EmotionVisuals(
        color_tint=color_tint,
        scale=vis_a.scale * inv_blend + vis_b.scale * blend,
        brightness=vis_a.brightness * inv_blend + vis_b.brightness * blend,
        pulse_rate=vis_a.pulse_rate * inv_blend + vis_b.pulse_rate * blend,
        pulse_amplitude=vis_a.pulse_amplitude * inv_blend + vis_b.pulse_amplitude * blend,
        rotation=vis_a.rotation * inv_blend + vis_b.rotation * blend,
        blink_rate=vis_a.blink_rate * inv_blend + vis_b.blink_rate * blend,
        intensity=vis_a.intensity * inv_blend + vis_b.intensity * blend,
    )

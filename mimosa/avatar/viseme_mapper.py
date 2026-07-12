"""
Viseme mapping utilities for TTS integration.

Provides helpers for TTS engines to convert their output to visemes/phonemes
that the lip sync system can use.
"""

from typing import List, Tuple, Optional
from .lip_sync import PhonemeEvent
from .mouth_shapes import MouthShape


def estimate_speech_duration(text: str, wpm: int = 150) -> float:
    """
    Estimate speech duration from text.
    
    Args:
        text: Text to be spoken
        wpm: Words per minute (speaking rate)
        
    Returns:
        Estimated duration in seconds
    """
    words = len(text.split())
    if words == 0:
        return 0.1  # Minimum duration
    
    # Convert words per minute to seconds
    duration = (words / wpm) * 60.0
    
    # Add small padding
    return max(0.5, duration)


def create_simple_viseme_events(text: str, duration: float) -> List[PhonemeEvent]:
    """
    Create simple viseme events from text for basic lip sync.
    
    This is a fallback when TTS doesn't provide phoneme timing.
    Uses character-based heuristics to create mouth shapes.
    
    Args:
        text: Text being spoken
        duration: Total speech duration
        
    Returns:
        List of phoneme events for lip sync
    """
    if not text or duration <= 0:
        return []
    
    # Simple character-to-mouth-shape mapping
    char_shapes = []
    
    for char in text.lower():
        if char in 'bpm':
            char_shapes.append(('M', MouthShape.CLOSED))
        elif char in 'aeo':
            char_shapes.append(('AA', MouthShape.OPEN))
        elif char in 'iu':
            char_shapes.append(('IY', MouthShape.WIDE))
        elif char in 'ou':
            char_shapes.append(('UW', MouthShape.ROUNDED))
        elif char in 'fv':
            char_shapes.append(('F', MouthShape.FRICATIVE))
        elif char in 'lntd':
            char_shapes.append(('L', MouthShape.TONGUE))
        elif char == ' ':
            char_shapes.append(('', MouthShape.RELAXED))
        else:
            # Keep previous shape for other consonants
            if char_shapes:
                char_shapes.append(char_shapes[-1])
            else:
                char_shapes.append(('', MouthShape.RELAXED))
    
    if not char_shapes:
        return []
    
    # Distribute evenly across duration
    event_duration = duration / len(char_shapes)
    
    events = []
    for i, (phoneme, shape) in enumerate(char_shapes):
        events.append(PhonemeEvent(
            phoneme=phoneme or f"CHAR_{i}",
            start_time=i * event_duration,
            duration=event_duration,
            mouth_shape=shape
        ))
    
    return events


def estimate_syllable_count(text: str) -> int:
    """
    Rough syllable count estimation.
    
    Args:
        text: Text to analyze
        
    Returns:
        Estimated number of syllables
    """
    # Very simple vowel-counting heuristic
    vowels = 'aeiouy'
    text = text.lower()
    count = 0
    prev_was_vowel = False
    
    for char in text:
        is_vowel = char in vowels
        if is_vowel and not prev_was_vowel:
            count += 1
        prev_was_vowel = is_vowel
    
    return max(1, count)

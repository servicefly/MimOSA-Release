"""
Lip sync engine for speech animation.

Synchronizes avatar mouth movements with TTS phoneme timing for
realistic speech animation.
"""

import time
from dataclasses import dataclass
from typing import List, Optional, Tuple
from .mouth_shapes import MouthShape, phoneme_to_mouth_shape, text_to_simple_visemes


@dataclass
class PhonemeEvent:
    """A phoneme with timing information."""
    
    phoneme: str  # ARPAbet phoneme
    start_time: float  # Seconds from speech start
    duration: float  # Duration in seconds
    mouth_shape: Optional[MouthShape] = None
    
    def __post_init__(self):
        """Automatically map phoneme to mouth shape if not provided."""
        if self.mouth_shape is None:
            self.mouth_shape = phoneme_to_mouth_shape(self.phoneme)


class LipSyncEngine:
    """
    Manages lip sync animation based on phoneme timing.
    
    Provides real-time mouth shape based on current playback position.
    """
    
    def __init__(self):
        self.events: List[PhonemeEvent] = []
        self.start_time: Optional[float] = None
        self.is_playing: bool = False
        self._current_index: int = 0
        
    def load_phonemes(self, events: List[PhonemeEvent]) -> None:
        """
        Load phoneme timing data.
        
        Args:
            events: List of phoneme events with timing
        """
        # Sort by start time
        self.events = sorted(events, key=lambda e: e.start_time)
        self._current_index = 0
        
    def load_from_text(self, text: str, duration: float) -> None:
        """
        Create simple phoneme events from text (heuristic fallback).
        
        When TTS doesn't provide phoneme timing, this creates
        evenly-spaced mouth shapes based on the text.
        
        Args:
            text: Text being spoken
            duration: Total speech duration in seconds
        """
        shapes = text_to_simple_visemes(text)
        
        if not shapes:
            self.events = []
            return
        
        # Distribute shapes evenly across duration
        event_duration = duration / len(shapes) if shapes else 0.1
        
        self.events = [
            PhonemeEvent(
                phoneme=f"CHAR_{i}",  # Dummy phoneme
                start_time=i * event_duration,
                duration=event_duration,
                mouth_shape=shape
            )
            for i, shape in enumerate(shapes)
        ]
        
        self._current_index = 0
        
    def start(self) -> None:
        """Start lip sync playback."""
        self.start_time = time.time()
        self.is_playing = True
        self._current_index = 0
        
    def stop(self) -> None:
        """Stop lip sync playback."""
        self.is_playing = False
        self.start_time = None
        self._current_index = 0
        
    def reset(self) -> None:
        """Reset to beginning without stopping."""
        self._current_index = 0
        if self.is_playing and self.start_time is not None:
            self.start_time = time.time()
            
    def get_current_mouth_shape(self) -> MouthShape:
        """
        Get the mouth shape for the current playback position.
        
        Returns:
            Current mouth shape based on playback time
        """
        if not self.is_playing or not self.events:
            return MouthShape.RELAXED
        
        if self.start_time is None:
            return MouthShape.RELAXED
        
        # Calculate current position in speech
        elapsed = time.time() - self.start_time
        
        # Find the phoneme event at current time
        for i in range(self._current_index, len(self.events)):
            event = self.events[i]
            event_end = event.start_time + event.duration
            
            if elapsed < event.start_time:
                # Before this event - return previous or relaxed
                if i > 0:
                    return self.events[i - 1].mouth_shape or MouthShape.RELAXED
                return MouthShape.RELAXED
            
            if event.start_time <= elapsed < event_end:
                # Currently in this event
                self._current_index = i
                return event.mouth_shape or MouthShape.RELAXED
        
        # Past all events - return last shape or relaxed
        if self.events:
            return self.events[-1].mouth_shape or MouthShape.RELAXED
        
        return MouthShape.RELAXED
    
    def get_progress(self) -> float:
        """
        Get current playback progress.
        
        Returns:
            Progress from 0.0 (start) to 1.0 (end), or 0.0 if not playing
        """
        if not self.is_playing or not self.events or self.start_time is None:
            return 0.0
        
        elapsed = time.time() - self.start_time
        
        if not self.events:
            return 0.0
        
        total_duration = self.events[-1].start_time + self.events[-1].duration
        
        if total_duration <= 0:
            return 0.0
        
        return min(1.0, elapsed / total_duration)
    
    def is_finished(self) -> bool:
        """
        Check if playback has finished.
        
        Returns:
            True if all phonemes have been played
        """
        if not self.is_playing or not self.events:
            return True
        
        return self.get_progress() >= 1.0


def create_phoneme_events_from_timing(
    phonemes: List[str],
    timings: List[Tuple[float, float]]
) -> List[PhonemeEvent]:
    """
    Create PhonemeEvent objects from phoneme and timing lists.
    
    Args:
        phonemes: List of phoneme strings
        timings: List of (start_time, duration) tuples
        
    Returns:
        List of PhonemeEvent objects
    """
    if len(phonemes) != len(timings):
        raise ValueError("Phonemes and timings lists must have same length")
    
    return [
        PhonemeEvent(
            phoneme=phoneme,
            start_time=timing[0],
            duration=timing[1]
        )
        for phoneme, timing in zip(phonemes, timings)
    ]

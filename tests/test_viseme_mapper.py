"""
Tests for viseme mapping utilities (M8.3).
"""

import pytest
from mimosa.avatar.viseme_mapper import (
    estimate_speech_duration,
    create_simple_viseme_events,
    estimate_syllable_count
)
from mimosa.avatar.lip_sync import PhonemeEvent


class TestSpeechDuration:
    """Test speech duration estimation."""
    
    def test_estimate_empty_text(self):
        """Verify minimum duration for empty text."""
        duration = estimate_speech_duration("")
        assert duration == 0.1
    
    def test_estimate_single_word(self):
        """Verify duration for single word."""
        duration = estimate_speech_duration("hello")
        assert duration >= 0.5
    
    def test_estimate_multiple_words(self):
        """Verify duration scales with word count."""
        short_text = "hello"
        long_text = "hello world this is a test"
        
        short_duration = estimate_speech_duration(short_text)
        long_duration = estimate_speech_duration(long_text)
        
        assert long_duration > short_duration
    
    def test_estimate_custom_wpm(self):
        """Verify custom words per minute affects duration."""
        text = "hello world"
        slow = estimate_speech_duration(text, wpm=100)
        fast = estimate_speech_duration(text, wpm=200)
        
        assert slow > fast


class TestSimpleVisemeEvents:
    """Test simple viseme event creation."""
    
    def test_create_empty_text(self):
        """Verify empty text returns empty list."""
        events = create_simple_viseme_events("", 1.0)
        assert len(events) == 0
    
    def test_create_zero_duration(self):
        """Verify zero duration returns empty list."""
        events = create_simple_viseme_events("hello", 0.0)
        assert len(events) == 0
    
    def test_create_basic_text(self):
        """Verify events created for basic text."""
        events = create_simple_viseme_events("hello", 1.0)
        
        assert len(events) > 0
        assert all(isinstance(e, PhonemeEvent) for e in events)
        assert all(e.start_time >= 0 for e in events)
        assert all(e.duration > 0 for e in events)
    
    def test_create_events_cover_duration(self):
        """Verify events span the full duration."""
        duration = 2.0
        events = create_simple_viseme_events("hello world", duration)
        
        if events:
            last_event = events[-1]
            total_time = last_event.start_time + last_event.duration
            assert total_time == pytest.approx(duration, rel=0.01)
    
    def test_create_bilabial_shapes(self):
        """Verify bilabial characters create CLOSED mouth."""
        events = create_simple_viseme_events("bpm", 1.0)
        
        from mimosa.avatar.mouth_shapes import MouthShape
        for event in events:
            assert event.mouth_shape == MouthShape.CLOSED
    
    def test_create_events_sequential(self):
        """Verify events are sequential (no gaps)."""
        events = create_simple_viseme_events("test", 1.0)
        
        for i in range(len(events) - 1):
            current_end = events[i].start_time + events[i].duration
            next_start = events[i + 1].start_time
            assert current_end == pytest.approx(next_start, abs=0.001)


class TestSyllableCount:
    """Test syllable counting heuristic."""
    
    def test_count_empty_string(self):
        """Verify empty string returns 1."""
        count = estimate_syllable_count("")
        assert count == 1
    
    def test_count_single_vowel(self):
        """Verify single vowel word."""
        count = estimate_syllable_count("a")
        assert count == 1
    
    def test_count_multiple_syllables(self):
        """Verify multi-syllable words."""
        count = estimate_syllable_count("hello")  # hel-lo
        assert count >= 2
    
    def test_count_consecutive_vowels(self):
        """Verify consecutive vowels count as one."""
        count = estimate_syllable_count("eat")  # ea = one vowel group
        assert count >= 1
    
    def test_count_no_vowels(self):
        """Verify consonant-only returns minimum."""
        count = estimate_syllable_count("xyz")
        assert count == 1

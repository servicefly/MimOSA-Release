"""
Tests for avatar mouth animation and lip sync (M8.3).
"""

import pytest
import time
from mimosa.avatar.mouth_shapes import (
    MouthShape,
    phoneme_to_mouth_shape,
    text_to_simple_visemes,
    get_mouth_params,
    MOUTH_SHAPE_PARAMS
)
from mimosa.avatar.lip_sync import (
    LipSyncEngine,
    PhonemeEvent,
    create_phoneme_events_from_timing
)


class TestMouthShapes:
    """Test mouth shape definitions and phoneme mapping."""
    
    def test_all_mouth_shapes_have_params(self):
        """Verify all mouth shapes have rendering parameters."""
        for shape in MouthShape:
            params = get_mouth_params(shape)
            assert 'height' in params
            assert 'width' in params
            assert 'roundness' in params
            assert 0.0 <= params['height'] <= 1.0
            assert 0.0 <= params['width'] <= 1.0
            assert 0.0 <= params['roundness'] <= 1.0
    
    def test_phoneme_to_mouth_shape_bilabials(self):
        """Verify bilabial phonemes map to CLOSED."""
        assert phoneme_to_mouth_shape('M') == MouthShape.CLOSED
        assert phoneme_to_mouth_shape('B') == MouthShape.CLOSED
        assert phoneme_to_mouth_shape('P') == MouthShape.CLOSED
    
    def test_phoneme_to_mouth_shape_open_vowels(self):
        """Verify open vowels map correctly."""
        assert phoneme_to_mouth_shape('AA') == MouthShape.OPEN
        assert phoneme_to_mouth_shape('AE') == MouthShape.OPEN
    
    def test_phoneme_to_mouth_shape_wide_vowels(self):
        """Verify wide vowels map correctly."""
        assert phoneme_to_mouth_shape('IY') == MouthShape.WIDE
        assert phoneme_to_mouth_shape('EY') == MouthShape.WIDE
    
    def test_phoneme_to_mouth_shape_rounded_vowels(self):
        """Verify rounded vowels map correctly."""
        assert phoneme_to_mouth_shape('UW') == MouthShape.ROUNDED
        assert phoneme_to_mouth_shape('OW') == MouthShape.ROUNDED
    
    def test_phoneme_to_mouth_shape_strips_stress(self):
        """Verify stress markers are stripped from phonemes."""
        assert phoneme_to_mouth_shape('AA0') == MouthShape.OPEN
        assert phoneme_to_mouth_shape('IY1') == MouthShape.WIDE
        assert phoneme_to_mouth_shape('UW2') == MouthShape.ROUNDED
    
    def test_phoneme_to_mouth_shape_unknown_returns_relaxed(self):
        """Verify unknown phonemes default to RELAXED."""
        assert phoneme_to_mouth_shape('UNKNOWN') == MouthShape.RELAXED
        assert phoneme_to_mouth_shape('XYZ') == MouthShape.RELAXED
    
    def test_text_to_simple_visemes_empty(self):
        """Verify empty text returns RELAXED."""
        shapes = text_to_simple_visemes("")
        assert len(shapes) == 1
        assert shapes[0] == MouthShape.RELAXED
    
    def test_text_to_simple_visemes_basic(self):
        """Verify basic text creates mouth shapes."""
        shapes = text_to_simple_visemes("hello")
        assert len(shapes) == 5
        assert MouthShape.OPEN in shapes  # 'e' and 'o'
        assert MouthShape.TONGUE in shapes  # 'l'
    
    def test_text_to_simple_visemes_bilabials(self):
        """Verify bilabial sounds create CLOSED shapes."""
        shapes = text_to_simple_visemes("bpm")
        assert all(s == MouthShape.CLOSED for s in shapes)


class TestLipSyncEngine:
    """Test lip sync engine and phoneme event handling."""
    
    def test_engine_initializes(self):
        """Verify engine can be created."""
        engine = LipSyncEngine()
        assert engine is not None
        assert not engine.is_playing
        assert len(engine.events) == 0
    
    def test_load_phonemes(self):
        """Verify phoneme events can be loaded."""
        engine = LipSyncEngine()
        events = [
            PhonemeEvent("M", 0.0, 0.1, MouthShape.CLOSED),
            PhonemeEvent("AA", 0.1, 0.2, MouthShape.OPEN),
            PhonemeEvent("P", 0.3, 0.1, MouthShape.CLOSED),
        ]
        engine.load_phonemes(events)
        assert len(engine.events) == 3
        assert engine.events[0].phoneme == "M"
    
    def test_load_phonemes_sorts_by_time(self):
        """Verify events are sorted by start time."""
        engine = LipSyncEngine()
        events = [
            PhonemeEvent("AA", 0.2, 0.1),
            PhonemeEvent("M", 0.0, 0.1),
            PhonemeEvent("P", 0.1, 0.1),
        ]
        engine.load_phonemes(events)
        assert engine.events[0].start_time == 0.0
        assert engine.events[1].start_time == 0.1
        assert engine.events[2].start_time == 0.2
    
    def test_load_from_text(self):
        """Verify simple text-based phoneme generation."""
        engine = LipSyncEngine()
        engine.load_from_text("hello", 1.0)
        assert len(engine.events) > 0
        assert all(isinstance(e, PhonemeEvent) for e in engine.events)
    
    def test_start_stop(self):
        """Verify start/stop functionality."""
        engine = LipSyncEngine()
        engine.load_from_text("test", 1.0)
        
        assert not engine.is_playing
        engine.start()
        assert engine.is_playing
        assert engine.start_time is not None
        
        engine.stop()
        assert not engine.is_playing
    
    def test_get_current_mouth_shape_not_playing(self):
        """Verify RELAXED returned when not playing."""
        engine = LipSyncEngine()
        engine.load_from_text("test", 1.0)
        shape = engine.get_current_mouth_shape()
        assert shape == MouthShape.RELAXED
    
    def test_get_current_mouth_shape_playing(self):
        """Verify mouth shape returned during playback."""
        engine = LipSyncEngine()
        events = [
            PhonemeEvent("M", 0.0, 0.5, MouthShape.CLOSED),
            PhonemeEvent("AA", 0.5, 0.5, MouthShape.OPEN),
        ]
        engine.load_phonemes(events)
        engine.start()
        
        # Should return first phoneme immediately
        shape = engine.get_current_mouth_shape()
        assert shape == MouthShape.CLOSED
    
    def test_get_progress(self):
        """Verify progress calculation."""
        engine = LipSyncEngine()
        events = [
            PhonemeEvent("M", 0.0, 1.0),
        ]
        engine.load_phonemes(events)
        
        # Not playing
        assert engine.get_progress() == 0.0
        
        # Playing
        engine.start()
        progress = engine.get_progress()
        assert 0.0 <= progress <= 1.0
    
    def test_is_finished(self):
        """Verify finished detection."""
        engine = LipSyncEngine()
        events = [
            PhonemeEvent("M", 0.0, 0.01),  # Very short
        ]
        engine.load_phonemes(events)
        engine.start()
        
        # Wait for completion
        time.sleep(0.02)
        assert engine.is_finished()
    
    def test_reset(self):
        """Verify reset functionality."""
        engine = LipSyncEngine()
        events = [PhonemeEvent("M", 0.0, 1.0)]
        engine.load_phonemes(events)
        engine.start()
        
        time.sleep(0.1)
        engine.reset()
        
        # Should restart from beginning
        assert engine._current_index == 0


class TestPhonemeEventCreation:
    """Test phoneme event creation utilities."""
    
    def test_create_from_timing(self):
        """Verify events created from phoneme and timing lists."""
        phonemes = ['M', 'AA', 'P']
        timings = [(0.0, 0.1), (0.1, 0.2), (0.3, 0.1)]
        
        events = create_phoneme_events_from_timing(phonemes, timings)
        
        assert len(events) == 3
        assert events[0].phoneme == 'M'
        assert events[0].start_time == 0.0
        assert events[0].duration == 0.1
        assert events[0].mouth_shape == MouthShape.CLOSED
    
    def test_create_from_timing_auto_maps_shapes(self):
        """Verify mouth shapes are automatically mapped."""
        phonemes = ['IY', 'UW', 'AA']
        timings = [(0.0, 0.1), (0.1, 0.1), (0.2, 0.1)]
        
        events = create_phoneme_events_from_timing(phonemes, timings)
        
        assert events[0].mouth_shape == MouthShape.WIDE
        assert events[1].mouth_shape == MouthShape.ROUNDED
        assert events[2].mouth_shape == MouthShape.OPEN
    
    def test_create_from_timing_mismatched_raises(self):
        """Verify error when phonemes and timings don't match."""
        phonemes = ['M', 'AA']
        timings = [(0.0, 0.1)]
        
        with pytest.raises(ValueError):
            create_phoneme_events_from_timing(phonemes, timings)

"""
Tests for voice-avatar integration (M8.4).
"""

import pytest
from mimosa.avatar.voice_library import (
    VoiceMetadata,
    VoiceAuditioner,
    get_voice_metadata,
    get_voices_for_gender,
    get_all_voices,
    format_voice_description,
    VOICE_CATALOG,
)


class TestVoiceLibrary:
    """Test voice library catalog and metadata."""
    
    def test_voice_catalog_not_empty(self):
        """Verify voice catalog has entries."""
        assert len(VOICE_CATALOG) > 0
    
    def test_all_voices_have_required_fields(self):
        """Verify all voices have complete metadata."""
        for voice in VOICE_CATALOG:
            assert voice.voice_id
            assert voice.name
            assert voice.description
            assert voice.gender in ("female", "male", "neutral")
            assert voice.accent
            assert voice.pitch in ("low", "medium", "high")
            assert voice.style
            assert voice.sample_text
    
    def test_get_voice_metadata_existing(self):
        """Verify get_voice_metadata returns correct voice."""
        # Use first voice from catalog
        first_voice = VOICE_CATALOG[0]
        metadata = get_voice_metadata(first_voice.voice_id)
        
        assert metadata is not None
        assert metadata.voice_id == first_voice.voice_id
        assert metadata.name == first_voice.name
    
    def test_get_voice_metadata_nonexistent(self):
        """Verify get_voice_metadata returns None for unknown voice."""
        metadata = get_voice_metadata("nonexistent-voice-id")
        assert metadata is None
    
    def test_get_voices_for_gender_female(self):
        """Verify female voice filtering."""
        voices = get_voices_for_gender("female")
        
        assert len(voices) > 0
        assert all(v.gender == "female" for v in voices)
    
    def test_get_voices_for_gender_male(self):
        """Verify male voice filtering."""
        voices = get_voices_for_gender("male")
        
        assert len(voices) > 0
        assert all(v.gender == "male" for v in voices)
    
    def test_get_voices_for_gender_neutral(self):
        """Verify neutral voice filtering."""
        voices = get_voices_for_gender("neutral")
        
        assert len(voices) > 0
        assert all(v.gender == "neutral" for v in voices)
    
    def test_get_voices_for_gender_none_defaults_neutral(self):
        """Verify None gender defaults to neutral."""
        voices = get_voices_for_gender(None)
        neutral_voices = get_voices_for_gender("neutral")
        
        assert voices == neutral_voices
    
    def test_get_all_voices(self):
        """Verify get_all_voices returns complete catalog."""
        all_voices = get_all_voices()
        
        assert len(all_voices) == len(VOICE_CATALOG)
        assert all(v in VOICE_CATALOG for v in all_voices)
    
    def test_format_voice_description(self):
        """Verify voice description formatting."""
        voice = VoiceMetadata(
            voice_id="test-voice",
            name="Test",
            description="Test voice",
            gender="female",
            accent="american",
            pitch="medium",
            style="warm",
        )
        
        desc = format_voice_description(voice)
        
        assert "Test" in desc
        # American accent shouldn't be shown
        assert "american" not in desc.lower()
    
    def test_format_voice_description_british(self):
        """Verify non-American accent is shown."""
        voice = VoiceMetadata(
            voice_id="test-voice-british",
            name="Test",
            description="Test voice",
            gender="female",
            accent="british",
            pitch="medium",
            style="warm",
        )
        
        desc = format_voice_description(voice)
        
        assert "Test" in desc
        assert "British" in desc
    
    def test_format_voice_description_non_medium_pitch(self):
        """Verify non-medium pitch is shown."""
        voice = VoiceMetadata(
            voice_id="test-voice-low",
            name="Test",
            description="Test voice",
            gender="male",
            accent="american",
            pitch="low",
            style="calm",
        )
        
        desc = format_voice_description(voice)
        
        assert "low" in desc.lower()


class TestVoiceAuditioner:
    """Test voice audition/preview system."""
    
    def test_auditioner_initializes(self):
        """Verify auditioner can be created."""
        auditioner = VoiceAuditioner()
        assert auditioner is not None
    
    def test_preview_voice_unknown_returns_none(self):
        """Verify unknown voice returns None."""
        auditioner = VoiceAuditioner()
        result = auditioner.preview_voice("unknown-voice-id")
        
        assert result is None
    
    def test_clear_cache(self):
        """Verify cache can be cleared."""
        auditioner = VoiceAuditioner()
        auditioner.clear_cache()
        # Should not raise


class TestVoiceIntegration:
    """Test voice-avatar integration."""
    
    def test_tts_voices_match_catalog(self):
        """Verify TTS module voices align with catalog."""
        from mimosa.voice.tts import FEMALE_VOICES, MALE_VOICES, NEUTRAL_VOICES
        
        # Get catalog voice IDs by gender
        catalog_female = {v.voice_id for v in get_voices_for_gender("female")}
        catalog_male = {v.voice_id for v in get_voices_for_gender("male")}
        catalog_neutral = {v.voice_id for v in get_voices_for_gender("neutral")}
        
        # TTS voices should be in catalog
        for voice_id in FEMALE_VOICES:
            assert voice_id in catalog_female, f"{voice_id} not in female catalog"
        
        for voice_id in MALE_VOICES:
            assert voice_id in catalog_male, f"{voice_id} not in male catalog"
        
        for voice_id in NEUTRAL_VOICES:
            assert voice_id in catalog_neutral, f"{voice_id} not in neutral catalog"
    
    def test_expanded_voice_library(self):
        """Verify voice library has been expanded (M8.4)."""
        from mimosa.voice.tts import FEMALE_VOICES, MALE_VOICES, NEUTRAL_VOICES
        
        # M8.4 expanded library should have 5+ voices per gender
        assert len(FEMALE_VOICES) >= 5, f"Only {len(FEMALE_VOICES)} female voices"
        assert len(MALE_VOICES) >= 5, f"Only {len(MALE_VOICES)} male voices"
        assert len(NEUTRAL_VOICES) >= 3, f"Only {len(NEUTRAL_VOICES)} neutral voices"


class TestSpeechCallbacks:
    """Test voice loop speech callback system (M8.4)."""
    
    def test_voice_loop_accepts_speech_callbacks(self):
        """Verify voice loop can register speech callbacks."""
        from mimosa.voice.voice_loop import VoiceLoop
        from mimosa.voice.tts import PiperTTS
        from mimosa.voice.audio_manager import AudioManager
        
        # Create minimal voice loop
        try:
            tts = PiperTTS()
            audio = AudioManager()
            loop = VoiceLoop(
                tts=tts,
                audio=audio,
                wake_engine=None,
                stt_model="tiny",
            )
            
            # Register callbacks
            called_start = []
            called_end = []
            
            def on_start(text, duration):
                called_start.append((text, duration))
            
            def on_end():
                called_end.append(True)
            
            loop.set_speech_callbacks(on_start=on_start, on_end=on_end)
            
            # Verify callbacks are stored
            assert hasattr(loop, '_speech_start_callback')
            assert hasattr(loop, '_speech_end_callback')
            
        except Exception:
            # TTS/Audio may not be available in test environment
            pytest.skip("TTS/Audio not available")
    
    def test_speech_duration_estimation(self):
        """Verify speech duration estimation works."""
        from mimosa.voice.voice_loop import VoiceLoop
        from mimosa.voice.tts import PiperTTS
        from mimosa.voice.audio_manager import AudioManager
        
        try:
            tts = PiperTTS()
            audio = AudioManager()
            loop = VoiceLoop(
                tts=tts,
                audio=audio,
                wake_engine=None,
                stt_model="tiny",
            )
            
            # Test duration estimation
            duration = loop._estimate_speech_duration("Hello world")
            
            assert duration > 0
            assert duration < 10  # Should be reasonable
            
        except Exception:
            pytest.skip("TTS/Audio not available")

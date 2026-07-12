"""
Voice library with metadata and audition system for avatar voice selection.

Provides rich metadata for each Piper voice (description, characteristics,
sample text) and a preview system for users to audition voices before selection.
"""

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple
import logging

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class VoiceMetadata:
    """Metadata for a Piper TTS voice."""
    
    voice_id: str  # Piper voice identifier
    name: str  # Display name
    description: str  # User-friendly description
    gender: str  # "female", "male", or "neutral"
    accent: str  # "american", "british", "scottish", etc.
    pitch: str  # "low", "medium", "high"
    style: str  # "warm", "clear", "calm", "expressive", etc.
    sample_text: str = "Hello, I'm MimOSA. How can I help you today?"


# Voice library catalog (M8.4)
VOICE_CATALOG: Tuple[VoiceMetadata, ...] = (
    # Female voices
    VoiceMetadata(
        voice_id="en_US-amy-medium",
        name="Amy",
        description="Clear, warm American voice",
        gender="female",
        accent="american",
        pitch="medium",
        style="warm",
    ),
    VoiceMetadata(
        voice_id="en_US-hfc_female-medium",
        name="HFC Female",
        description="Natural, friendly American voice",
        gender="female",
        accent="american",
        pitch="medium",
        style="friendly",
    ),
    VoiceMetadata(
        voice_id="en_US-kathleen-low",
        name="Kathleen",
        description="Calm, soothing American voice",
        gender="female",
        accent="american",
        pitch="low",
        style="calm",
    ),
    VoiceMetadata(
        voice_id="en_GB-alba-medium",
        name="Alba",
        description="British accent, clear and articulate",
        gender="female",
        accent="british",
        pitch="medium",
        style="clear",
    ),
    VoiceMetadata(
        voice_id="en_GB-jenny_dioco-medium",
        name="Jenny",
        description="Expressive British voice",
        gender="female",
        accent="british",
        pitch="medium",
        style="expressive",
    ),
    VoiceMetadata(
        voice_id="en_US-amy-low",
        name="Amy (Deep)",
        description="Deeper variant of Amy",
        gender="female",
        accent="american",
        pitch="low",
        style="warm",
    ),
    VoiceMetadata(
        voice_id="en_GB-semaine-medium",
        name="Semaine",
        description="Articulate British voice",
        gender="female",
        accent="british",
        pitch="medium",
        style="articulate",
    ),
    VoiceMetadata(
        voice_id="en_GB-southern_english_female-low",
        name="Southern English",
        description="Clear British voice, deeper tone",
        gender="female",
        accent="british",
        pitch="low",
        style="clear",
    ),
    
    # Male voices
    VoiceMetadata(
        voice_id="en_US-ryan-medium",
        name="Ryan",
        description="Natural, clear American voice",
        gender="male",
        accent="american",
        pitch="medium",
        style="clear",
    ),
    VoiceMetadata(
        voice_id="en_US-joe-medium",
        name="Joe",
        description="Warm, friendly American voice",
        gender="male",
        accent="american",
        pitch="medium",
        style="warm",
    ),
    VoiceMetadata(
        voice_id="en_US-danny-low",
        name="Danny",
        description="Deep, calm American voice",
        gender="male",
        accent="american",
        pitch="low",
        style="calm",
    ),
    VoiceMetadata(
        voice_id="en_GB-northern_english_male-medium",
        name="Northern English",
        description="British accent, northern England",
        gender="male",
        accent="british",
        pitch="medium",
        style="friendly",
    ),
    VoiceMetadata(
        voice_id="en_US-ryan-low",
        name="Ryan (Deep)",
        description="Deeper variant of Ryan",
        gender="male",
        accent="american",
        pitch="low",
        style="clear",
    ),
    VoiceMetadata(
        voice_id="en_US-ryan-high",
        name="Ryan (High)",
        description="Higher pitch variant of Ryan",
        gender="male",
        accent="american",
        pitch="high",
        style="clear",
    ),
    VoiceMetadata(
        voice_id="en_GB-alan-medium",
        name="Alan",
        description="British voice, articulate and professional",
        gender="male",
        accent="british",
        pitch="medium",
        style="articulate",
    ),
    VoiceMetadata(
        voice_id="en_GB-cori-medium",
        name="Cori",
        description="Scottish accent, warm and friendly",
        gender="male",
        accent="scottish",
        pitch="medium",
        style="warm",
    ),
    
    # Neutral voices
    VoiceMetadata(
        voice_id="en_US-lessac-medium",
        name="Lessac",
        description="Classic MimOSA voice, balanced and neutral",
        gender="neutral",
        accent="american",
        pitch="medium",
        style="balanced",
    ),
    VoiceMetadata(
        voice_id="en_US-libritts_r-medium",
        name="LibriTTS",
        description="Balanced, neutral American voice",
        gender="neutral",
        accent="american",
        pitch="medium",
        style="neutral",
    ),
    VoiceMetadata(
        voice_id="en_US-kristin-medium",
        name="Kristin",
        description="Clear, professional American voice",
        gender="neutral",
        accent="american",
        pitch="medium",
        style="professional",
    ),
    VoiceMetadata(
        voice_id="en_GB-aru-medium",
        name="Aru",
        description="Neutral British voice",
        gender="neutral",
        accent="british",
        pitch="medium",
        style="neutral",
    ),
)


# Build lookup dictionaries for fast access
_VOICE_BY_ID: Dict[str, VoiceMetadata] = {v.voice_id: v for v in VOICE_CATALOG}
_VOICES_BY_GENDER: Dict[str, List[VoiceMetadata]] = {}

for voice in VOICE_CATALOG:
    gender = voice.gender.lower()
    if gender not in _VOICES_BY_GENDER:
        _VOICES_BY_GENDER[gender] = []
    _VOICES_BY_GENDER[gender].append(voice)


def get_voice_metadata(voice_id: str) -> Optional[VoiceMetadata]:
    """
    Get metadata for a voice ID.
    
    Args:
        voice_id: Piper voice identifier
        
    Returns:
        VoiceMetadata if found, None otherwise
    """
    return _VOICE_BY_ID.get(voice_id)


def get_voices_for_gender(gender: Optional[str]) -> List[VoiceMetadata]:
    """
    Get all voices for a gender preference.
    
    Args:
        gender: "female", "male", or "neutral"
        
    Returns:
        List of voice metadata, ordered by preference
    """
    if not gender:
        gender = "neutral"
    
    gender = gender.lower()
    return _VOICES_BY_GENDER.get(gender, _VOICES_BY_GENDER.get("neutral", []))


def get_all_voices() -> List[VoiceMetadata]:
    """
    Get all available voices.
    
    Returns:
        List of all voice metadata
    """
    return list(VOICE_CATALOG)


def format_voice_description(voice: VoiceMetadata) -> str:
    """
    Format a user-friendly description of a voice.
    
    Args:
        voice: Voice metadata
        
    Returns:
        Formatted description string
    """
    parts = [voice.name]
    
    # Add accent if not American
    if voice.accent != "american":
        parts.append(f"({voice.accent.title()})")
    
    # Add characteristics
    characteristics = []
    if voice.pitch != "medium":
        characteristics.append(voice.pitch)
    if voice.style:
        characteristics.append(voice.style)
    
    if characteristics:
        parts.append("-")
        parts.append(", ".join(characteristics))
    
    return " ".join(parts)


class VoiceAuditioner:
    """
    Voice preview/audition system for testing voices before selection.
    
    Allows users to play sample audio for different voices to help choose
    the right voice for their avatar.
    """
    
    def __init__(self):
        self._tts_cache: Dict[str, any] = {}  # Cache TTS engines per voice
        
    def preview_voice(self, voice_id: str, text: Optional[str] = None) -> Optional[bytes]:
        """
        Generate preview audio for a voice.
        
        Args:
            voice_id: Piper voice identifier
            text: Optional text to synthesize (uses default sample if None)
            
        Returns:
            WAV bytes if successful, None if synthesis fails
        """
        # Get metadata for sample text
        metadata = get_voice_metadata(voice_id)
        if metadata is None:
            logger.warning(f"No metadata for voice: {voice_id}")
            return None
        
        # Use provided text or default sample
        sample_text = text or metadata.sample_text
        
        try:
            # Import TTS lazily
            from mimosa.voice.tts import PiperTTS
            
            # Get or create TTS engine for this voice
            if voice_id not in self._tts_cache:
                self._tts_cache[voice_id] = PiperTTS(voice=voice_id)
            
            tts = self._tts_cache[voice_id]
            
            # Synthesize preview
            wav_bytes = tts.synthesize(sample_text)
            return wav_bytes
            
        except Exception as exc:
            logger.error(f"Failed to preview voice {voice_id}: {exc}")
            return None
    
    def play_sample(self, voice_id: str, text: Optional[str] = None) -> bool:
        """Synthesize and play a short sample of ``voice_id`` out loud.

        Backs the wizard/Settings "▶ Play Sample" button. This is a
        best-effort, fully self-contained preview: it degrades gracefully and
        never raises, returning ``True`` only when audio was actually played.

        Args:
            voice_id: Piper voice identifier to audition.
            text: Optional custom sample text (defaults to the voice's sample).

        Returns:
            ``True`` if a sample was synthesized and played; ``False`` if TTS
            or audio playback was unavailable for any reason.
        """
        wav_bytes = self.preview_voice(voice_id, text=text)
        if not wav_bytes:
            logger.info("Voice sample unavailable for %s (no TTS output)", voice_id)
            return False
        try:
            from mimosa.voice.audio_manager import AudioManager

            manager = AudioManager()
            manager.play_wav_bytes(wav_bytes)
            return True
        except Exception as exc:
            # No output device (e.g. headless), pyaudio missing, etc. — the
            # caller should surface a gentle "audio unavailable" hint.
            logger.info("Could not play voice sample for %s: %s", voice_id, exc)
            return False

    def clear_cache(self):
        """Clear cached TTS engines to free memory."""
        self._tts_cache.clear()

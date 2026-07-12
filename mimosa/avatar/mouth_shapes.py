"""
Mouth shape definitions and viseme mapping for lip sync.

Maps phonemes to mouth shapes (visemes) for realistic speech animation.
Based on standard ARPAbet phonemes and Disney/animation viseme sets.
"""

from enum import Enum
from typing import Dict, List


class MouthShape(Enum):
    """Standard mouth shapes for lip sync (Disney-style visemes)."""
    
    CLOSED = "closed"  # M, B, P - lips together
    OPEN = "open"  # AA, AE - jaw dropped
    WIDE = "wide"  # IY, EY - lips spread wide
    ROUNDED = "rounded"  # UW, OW - lips rounded
    DENTAL = "dental"  # TH, DH - tongue between teeth
    FRICATIVE = "fricative"  # F, V - bottom lip to teeth
    TONGUE = "tongue"  # L, N, T, D - tongue to roof
    RELAXED = "relaxed"  # Neutral/schwa position


# Phoneme to mouth shape mapping (ARPAbet)
# Based on CMU Pronouncing Dictionary phonemes
PHONEME_TO_MOUTH_SHAPE: Dict[str, MouthShape] = {
    # Closed (bilabials)
    'M': MouthShape.CLOSED,
    'B': MouthShape.CLOSED,
    'P': MouthShape.CLOSED,
    
    # Open vowels
    'AA': MouthShape.OPEN,  # "hot"
    'AE': MouthShape.OPEN,  # "cat"
    'AH': MouthShape.OPEN,  # "hut"
    'AO': MouthShape.ROUNDED,  # "caught"
    
    # Wide vowels
    'IY': MouthShape.WIDE,  # "see"
    'IH': MouthShape.WIDE,  # "sit"
    'EY': MouthShape.WIDE,  # "say"
    'EH': MouthShape.WIDE,  # "pet"
    
    # Rounded vowels
    'UW': MouthShape.ROUNDED,  # "blue"
    'UH': MouthShape.ROUNDED,  # "book"
    'OW': MouthShape.ROUNDED,  # "go"
    'OY': MouthShape.ROUNDED,  # "boy"
    
    # R-colored
    'ER': MouthShape.RELAXED,  # "her"
    'AW': MouthShape.ROUNDED,  # "how"
    'AY': MouthShape.WIDE,  # "my"
    
    # Fricatives
    'F': MouthShape.FRICATIVE,
    'V': MouthShape.FRICATIVE,
    'TH': MouthShape.DENTAL,
    'DH': MouthShape.DENTAL,
    'S': MouthShape.WIDE,
    'Z': MouthShape.WIDE,
    'SH': MouthShape.ROUNDED,
    'ZH': MouthShape.ROUNDED,
    'HH': MouthShape.OPEN,
    
    # Tongue consonants
    'L': MouthShape.TONGUE,
    'N': MouthShape.TONGUE,
    'T': MouthShape.TONGUE,
    'D': MouthShape.TONGUE,
    'K': MouthShape.RELAXED,
    'G': MouthShape.RELAXED,
    'NG': MouthShape.RELAXED,
    
    # Glides/approximants
    'W': MouthShape.ROUNDED,
    'Y': MouthShape.WIDE,
    'R': MouthShape.ROUNDED,
    
    # Affricates
    'CH': MouthShape.ROUNDED,
    'JH': MouthShape.ROUNDED,
}


def phoneme_to_mouth_shape(phoneme: str) -> MouthShape:
    """
    Convert a phoneme to its corresponding mouth shape.
    
    Args:
        phoneme: ARPAbet phoneme (e.g., "AA", "B", "IY")
        
    Returns:
        Corresponding mouth shape
    """
    # Strip stress markers (0, 1, 2) from vowels
    clean_phoneme = phoneme.rstrip('012')
    
    return PHONEME_TO_MOUTH_SHAPE.get(
        clean_phoneme.upper(),
        MouthShape.RELAXED
    )


def text_to_simple_visemes(text: str) -> List[MouthShape]:
    """
    Convert text to a simple sequence of mouth shapes.
    
    This is a simplified heuristic-based approach that doesn't require
    a full phoneme dictionary. Useful for basic animation when no
    TTS phoneme data is available.
    
    Args:
        text: Text to analyze
        
    Returns:
        List of mouth shapes for animation
    """
    shapes = []
    text = text.lower()
    
    for char in text:
        if char in 'bpm':
            shapes.append(MouthShape.CLOSED)
        elif char in 'aeiouy':
            shapes.append(MouthShape.OPEN)
        elif char in 'fv':
            shapes.append(MouthShape.FRICATIVE)
        elif char in 'lntd':
            shapes.append(MouthShape.TONGUE)
        elif char in 'ou':
            shapes.append(MouthShape.ROUNDED)
        elif char in 'ei':
            shapes.append(MouthShape.WIDE)
        elif char == ' ':
            shapes.append(MouthShape.RELAXED)
        else:
            # Keep previous shape for consonants
            if shapes:
                shapes.append(shapes[-1])
            else:
                shapes.append(MouthShape.RELAXED)
    
    return shapes if shapes else [MouthShape.RELAXED]


# Mouth shape visual parameters (for rendering)
MOUTH_SHAPE_PARAMS = {
    MouthShape.CLOSED: {
        'height': 0.1,  # Very narrow
        'width': 0.4,
        'roundness': 0.2,
    },
    MouthShape.OPEN: {
        'height': 0.8,  # Wide open
        'width': 0.6,
        'roundness': 0.5,
    },
    MouthShape.WIDE: {
        'height': 0.3,
        'width': 0.8,  # Stretched wide
        'roundness': 0.1,  # Less round
    },
    MouthShape.ROUNDED: {
        'height': 0.6,
        'width': 0.5,
        'roundness': 0.9,  # Very round
    },
    MouthShape.DENTAL: {
        'height': 0.4,
        'width': 0.5,
        'roundness': 0.3,
    },
    MouthShape.FRICATIVE: {
        'height': 0.35,
        'width': 0.45,
        'roundness': 0.2,
    },
    MouthShape.TONGUE: {
        'height': 0.4,
        'width': 0.5,
        'roundness': 0.4,
    },
    MouthShape.RELAXED: {
        'height': 0.2,
        'width': 0.5,
        'roundness': 0.5,
    },
}


def get_mouth_params(shape: MouthShape) -> dict:
    """
    Get rendering parameters for a mouth shape.
    
    Args:
        shape: Mouth shape
        
    Returns:
        Dict with height, width, roundness parameters (0.0-1.0)
    """
    return MOUTH_SHAPE_PARAMS.get(shape, MOUTH_SHAPE_PARAMS[MouthShape.RELAXED])

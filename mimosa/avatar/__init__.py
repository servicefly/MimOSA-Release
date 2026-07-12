"""MimOSA v2.0.0 avatar system (Milestone 8.1+).

The avatar package houses the animated *character* avatar that replaces the
classic listening circle when a user opts in. It is organised in tiers:

* **2D sprite** (:mod:`mimosa.avatar.renderer_2d`) -- the universal baseline in
  v2.0.0-alpha; runs on any desktop.
* **Live2D / 3D** -- reserved for v2.1.0+ (renderers not yet implemented).

Public surface
--------------
* :class:`BaseAvatarRenderer` -- abstract interface every tier implements.
* :class:`Sprite2DRenderer`   -- the 2D sprite renderer (skeleton in M8.1).
* :data:`AvatarCharacterWindow` -- the GTK4 avatar window (``None`` on headless
  machines where GTK 4 is unavailable).

Import-safety: nothing here imports GTK/Cairo at package import time except the
guarded window module, which degrades to ``None`` when GTK is missing. This
keeps the package importable for logic tests on headless CI.
"""

from __future__ import annotations

from mimosa.avatar.base_renderer import BaseAvatarRenderer
from mimosa.avatar.renderer_2d import Sprite2DRenderer

# The window module guards its own GTK import; AvatarCharacterWindow is None
# when GTK 4 is unavailable. Import defensively so the package stays importable
# even if the window module itself fails to load for any reason.
try:
    from mimosa.avatar.avatar_window import AvatarCharacterWindow, HAS_GTK
except Exception:  # pragma: no cover - defensive
    AvatarCharacterWindow = None
    HAS_GTK = False

# M8.2: Avatar generation components (non-GTK, always available)
from mimosa.avatar.generator import AvatarGenerator
from mimosa.avatar.cache_manager import AvatarCacheManager
from mimosa.avatar.sprite_processor import SpriteProcessor

# M8.2: Preview dialog (GTK-dependent)
try:
    from mimosa.avatar.preview_dialog import AvatarPreviewDialog
except Exception:  # pragma: no cover - defensive
    AvatarPreviewDialog = None

# M8.3: Animation system (non-GTK, always available)
from mimosa.avatar.animator import Animator, AnimationState, AnimationFrame
from mimosa.avatar.emotions import EmotionState, EmotionVisuals
from mimosa.avatar.lip_sync import LipSyncEngine, PhonemeEvent, MouthShape
from mimosa.avatar.gestures import GestureController, GestureType, Gesture

# M8.4: Voice library and audition system
from mimosa.avatar.voice_library import (
    VoiceMetadata,
    VoiceAuditioner,
    get_voice_metadata,
    get_voices_for_gender,
    get_all_voices,
    format_voice_description,
)

__all__ = [
    "BaseAvatarRenderer",
    "Sprite2DRenderer",
    "AvatarCharacterWindow",
    "HAS_GTK",
    "AvatarGenerator",
    "AvatarCacheManager",
    "SpriteProcessor",
    "AvatarPreviewDialog",
    "Animator",
    "AnimationState",
    "AnimationFrame",
    "EmotionState",
    "EmotionVisuals",
    "LipSyncEngine",
    "PhonemeEvent",
    "MouthShape",
    "GestureController",
    "GestureType",
    "Gesture",
    "VoiceMetadata",
    "VoiceAuditioner",
    "get_voice_metadata",
    "get_voices_for_gender",
    "get_all_voices",
    "format_voice_description",
]

"""
Avatar Cache Manager

Manages local storage and retrieval of generated avatar sprites.
Avatars are stored in ~/.local/share/mimosa/avatars/ with metadata.
"""

import json
import shutil
import hashlib
from pathlib import Path
from typing import Optional, List, Dict
from datetime import datetime


class AvatarCacheManager:
    """Manages avatar sprite caching and retrieval."""
    
    def __init__(self, cache_dir: Optional[Path] = None):
        """
        Initialize the avatar cache manager.
        
        Args:
            cache_dir: Optional custom cache directory. Defaults to user data dir.
        """
        if cache_dir is None:
            cache_dir = Path.home() / ".local" / "share" / "mimosa" / "avatars"
        
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        
        self.metadata_file = self.cache_dir / "metadata.json"
        self._metadata: Dict[str, dict] = self._load_metadata()
    
    def _load_metadata(self) -> Dict[str, dict]:
        """Load avatar metadata from disk."""
        if self.metadata_file.exists():
            try:
                with open(self.metadata_file, 'r') as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                return {}
        return {}
    
    def _save_metadata(self):
        """Save avatar metadata to disk."""
        try:
            with open(self.metadata_file, 'w') as f:
                json.dump(self._metadata, f, indent=2)
        except IOError as e:
            print(f"Warning: Failed to save avatar metadata: {e}")
    
    def _generate_avatar_id(self, description: str, gender: str) -> str:
        """Generate a unique ID for an avatar based on its description and gender."""
        content = f"{description}:{gender}:{datetime.now().isoformat()}"
        return hashlib.sha256(content.encode()).hexdigest()[:16]
    
    def save_avatar(
        self,
        sprite_path: Path,
        description: str,
        gender: str,
        **extra_metadata
    ) -> str:
        """
        Save an avatar sprite to the cache with metadata.
        
        Args:
            sprite_path: Path to the sprite image file
            description: Text description of the avatar
            gender: Avatar gender ('female', 'male', 'neutral')
            **extra_metadata: Additional metadata to store
        
        Returns:
            Avatar ID (unique identifier)
        """
        # Generate unique ID
        avatar_id = self._generate_avatar_id(description, gender)
        
        # Copy sprite to cache directory
        cached_sprite_path = self.cache_dir / f"{avatar_id}.png"
        shutil.copy2(sprite_path, cached_sprite_path)
        
        # Store metadata
        self._metadata[avatar_id] = {
            "id": avatar_id,
            "description": description,
            "gender": gender,
            "sprite_path": str(cached_sprite_path),
            "created_at": datetime.now().isoformat(),
            **extra_metadata
        }
        
        self._save_metadata()
        return avatar_id
    
    def load_avatar(self, avatar_id: str) -> Optional[Path]:
        """
        Retrieve an avatar sprite from the cache.
        
        Args:
            avatar_id: Unique avatar identifier
        
        Returns:
            Path to cached sprite, or None if not found
        """
        if avatar_id not in self._metadata:
            return None
        
        sprite_path = Path(self._metadata[avatar_id]["sprite_path"])
        if sprite_path.exists():
            return sprite_path
        
        return None
    
    def get_avatar_metadata(self, avatar_id: str) -> Optional[dict]:
        """Get metadata for a cached avatar."""
        return self._metadata.get(avatar_id)
    
    def list_avatars(self, gender: Optional[str] = None) -> List[dict]:
        """
        List all cached avatars, optionally filtered by gender.
        
        Args:
            gender: Optional gender filter ('female', 'male', 'neutral')
        
        Returns:
            List of avatar metadata dictionaries
        """
        avatars = list(self._metadata.values())
        
        if gender:
            avatars = [a for a in avatars if a.get("gender") == gender]
        
        # Sort by creation date, newest first
        avatars.sort(key=lambda a: a.get("created_at", ""), reverse=True)
        
        return avatars
    
    def delete_avatar(self, avatar_id: str) -> bool:
        """
        Delete an avatar from the cache.
        
        Args:
            avatar_id: Unique avatar identifier
        
        Returns:
            True if deleted successfully, False otherwise
        """
        if avatar_id not in self._metadata:
            return False
        
        # Delete sprite file
        sprite_path = Path(self._metadata[avatar_id]["sprite_path"])
        if sprite_path.exists():
            try:
                sprite_path.unlink()
            except OSError:
                pass
        
        # Remove from metadata
        del self._metadata[avatar_id]
        self._save_metadata()
        
        return True
    
    def clear_cache(self):
        """Delete all cached avatars."""
        for avatar_id in list(self._metadata.keys()):
            self.delete_avatar(avatar_id)
    
    def get_cache_size(self) -> int:
        """Get total size of cached avatars in bytes."""
        total_size = 0
        for avatar_id in self._metadata:
            sprite_path = Path(self._metadata[avatar_id]["sprite_path"])
            if sprite_path.exists():
                total_size += sprite_path.stat().st_size
        return total_size


def get_default_cache_manager() -> AvatarCacheManager:
    """Get the default avatar cache manager instance."""
    return AvatarCacheManager()

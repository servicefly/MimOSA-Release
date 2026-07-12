"""
Avatar Generator

Generates custom avatar sprites from text descriptions.
Supports multiple generation backends with graceful fallbacks.
"""

import tempfile
from pathlib import Path
from typing import Optional, Dict
import shutil

from mimosa.avatar.sprite_processor import SpriteProcessor


class AvatarGenerator:
    """Generates custom 2D avatar sprites."""
    
    # Gender-specific style modifiers for prompts
    GENDER_STYLES = {
        "female": {
            "features": "soft, warm, friendly features",
            "colors": "warm and inviting colors",
            "style": "gentle, approachable character"
        },
        "male": {
            "features": "confident, angular features",
            "colors": "cool, professional tones",
            "style": "strong, reliable character"
        },
        "neutral": {
            "features": "balanced, androgynous features",
            "colors": "neutral, harmonious palette",
            "style": "versatile, welcoming character"
        }
    }
    
    def __init__(self):
        """Initialize the avatar generator."""
        try:
            self.processor = SpriteProcessor()
        except ImportError:
            # PIL not available - sprite processing will be limited
            self.processor = None
        self._default_avatars_dir = self._find_default_avatars()
    
    def _find_default_avatars(self) -> Optional[Path]:
        """Locate the default avatars directory."""
        # Try multiple possible locations
        possible_paths = [
            Path(__file__).parent.parent.parent / "data" / "avatars",
            Path.home() / ".local" / "share" / "mimosa" / "default_avatars",
            Path("/usr/share/mimosa-assistant/data/avatars")
        ]
        
        for path in possible_paths:
            if path.exists() and path.is_dir():
                return path
        
        return None
    
    def generate_avatar(
        self,
        description: str,
        gender: str = "neutral",
        output_path: Optional[Path] = None,
        method: str = "auto"
    ) -> Optional[Path]:
        """
        Generate a custom avatar from a text description.
        
        Args:
            description: Text description of desired avatar
            gender: Gender preference ('female', 'male', 'neutral')
            output_path: Optional specific output path
            method: Generation method ('auto', 'ai', 'default')
        
        Returns:
            Path to generated avatar sprite, or None if generation failed
        """
        # Normalize gender
        gender = gender.lower()
        if gender not in self.GENDER_STYLES:
            gender = "neutral"
        
        # Create output path if not provided
        if output_path is None:
            output_path = Path(tempfile.mkdtemp()) / "avatar.png"
        
        # Attempt generation based on method
        if method == "auto":
            # Try AI first, fall back to default
            result = self._try_ai_generation(description, gender, output_path)
            if result is None:
                result = self._use_default_avatar(gender, output_path)
            return result
        
        elif method == "ai":
            return self._try_ai_generation(description, gender, output_path)
        
        elif method == "default":
            return self._use_default_avatar(gender, output_path)
        
        else:
            raise ValueError(f"Unknown generation method: {method}")
    
    def _try_ai_generation(
        self,
        description: str,
        gender: str,
        output_path: Path
    ) -> Optional[Path]:
        """
        Attempt AI-based avatar generation.
        Tries multiple backends in order of preference.
        """
        # Try Stable Diffusion (local) if available
        result = self._try_local_stable_diffusion(description, gender, output_path)
        if result:
            return result
        
        # Could add other AI backends here (external APIs, etc.)
        # For privacy-focused app, we avoid external APIs by default
        
        return None
    
    def _try_local_stable_diffusion(
        self,
        description: str,
        gender: str,
        output_path: Path
    ) -> Optional[Path]:
        """
        Try generating avatar using local Stable Diffusion.
        Requires diffusers library and local model.
        """
        try:
            from diffusers import StableDiffusionPipeline
            import torch
            
            # Check if CUDA is available
            device = "cuda" if torch.cuda.is_available() else "cpu"
            
            # For CPU, this will be very slow, so we skip it
            if device == "cpu":
                return None
            
            # Build gender-aware prompt
            style_info = self.GENDER_STYLES[gender]
            prompt = (
                f"portrait illustration of {description}, "
                f"{style_info['features']}, "
                f"{style_info['colors']}, "
                f"head and shoulders view, "
                f"friendly expression, professional quality, "
                f"digital art, clean background"
            )
            
            # Load model (this is expensive, consider caching)
            # Using a lightweight model for faster generation
            model_id = "runwayml/stable-diffusion-v1-5"
            pipe = StableDiffusionPipeline.from_pretrained(
                model_id,
                torch_dtype=torch.float16 if device == "cuda" else torch.float32
            )
            pipe = pipe.to(device)
            
            # Generate image
            image = pipe(
                prompt,
                num_inference_steps=30,
                guidance_scale=7.5
            ).images[0]
            
            # Save temporary image
            temp_path = output_path.with_suffix('.tmp.png')
            image.save(temp_path)
            
            # Process sprite (crop, optimize, etc.)
            if self.processor:
                processed = self.processor.process_sprite(temp_path, output_path)
            else:
                # No processor available, just use raw image
                shutil.copy2(temp_path, output_path)
                processed = output_path
            
            # Clean up temp file
            if temp_path.exists():
                temp_path.unlink()
            
            return processed
            
        except ImportError:
            # diffusers or torch not available
            return None
        except Exception as e:
            # Any other error during generation
            print(f"Stable Diffusion generation failed: {e}")
            return None
    
    def _use_default_avatar(
        self,
        gender: str,
        output_path: Path
    ) -> Optional[Path]:
        """
        Use a pre-made default avatar template.
        Always available as ultimate fallback.
        """
        if self._default_avatars_dir is None:
            return None
        
        # Select default avatar based on gender
        default_file = f"default_{gender}.svg"
        default_path = self._default_avatars_dir / default_file
        
        if not default_path.exists():
            # Fallback to neutral if specific gender not found
            default_path = self._default_avatars_dir / "default_neutral.svg"
        
        if not default_path.exists():
            # Use any default.svg as last resort
            default_path = self._default_avatars_dir / "default.svg"
        
        if not default_path.exists():
            return None
        
        # Convert SVG to PNG
        temp_png = output_path.with_suffix('.default.png')
        converted = None
        
        if self.processor:
            converted = self.processor.convert_svg_to_png(
                default_path,
                temp_png,
                size=(512, 512)
            )
        
        if converted is None:
            # If SVG conversion fails or no processor, just copy the SVG
            # The renderer will need to handle SVG directly
            shutil.copy2(default_path, output_path.with_suffix('.svg'))
            return output_path.with_suffix('.svg')
        
        # Process the converted PNG
        if self.processor:
            processed = self.processor.process_sprite(converted, output_path)
        else:
            shutil.copy2(converted, output_path)
            processed = output_path
        
        # Clean up temp file
        if temp_png.exists() and temp_png != processed:
            temp_png.unlink()
        
        return processed
    
    def get_generation_info(self) -> Dict[str, bool]:
        """
        Get information about available generation methods.
        
        Returns:
            Dictionary of method names and their availability
        """
        info = {
            "default_avatars": self._default_avatars_dir is not None,
            "stable_diffusion": False,
            "external_api": False
        }
        
        # Check for Stable Diffusion
        try:
            import diffusers
            import torch
            info["stable_diffusion"] = torch.cuda.is_available()
        except ImportError:
            pass
        
        return info


def get_default_generator() -> AvatarGenerator:
    """Get the default avatar generator instance."""
    return AvatarGenerator()

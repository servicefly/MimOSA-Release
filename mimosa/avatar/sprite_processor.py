"""
Avatar Sprite Processor

Processes, optimizes, and prepares avatar sprites for use in the application.
Handles image cropping, resizing, and PNG optimization.
"""

from pathlib import Path
from typing import Optional, Tuple
import subprocess

# Import PIL only when needed to avoid dependency issues
try:
    from PIL import Image, ImageDraw, ImageOps
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False


class SpriteProcessor:
    """Processes and optimizes avatar sprites."""
    
    # Target dimensions for avatar sprites
    SPRITE_SIZE = (512, 512)
    THUMBNAIL_SIZE = (128, 128)
    
    def __init__(self):
        """Initialize the sprite processor."""
        if not PIL_AVAILABLE:
            raise ImportError(
                "PIL/Pillow is required for sprite processing. "
                "Install it with: pip install Pillow"
            )
    
    def process_sprite(
        self,
        image_path: Path,
        output_path: Path,
        optimize: bool = True
    ) -> Path:
        """
        Process an avatar sprite: crop, resize, and optimize.
        
        Args:
            image_path: Path to input image
            output_path: Path for processed output
            optimize: Whether to apply PNG optimization
        
        Returns:
            Path to processed sprite
        """
        # Load image
        img = Image.open(image_path)
        
        # Convert to RGBA if needed
        if img.mode != 'RGBA':
            img = img.convert('RGBA')
        
        # Crop to square (head and shoulders focus)
        img = self._crop_to_square(img)
        
        # Resize to target dimensions
        img = img.resize(self.SPRITE_SIZE, Image.Resampling.LANCZOS)
        
        # Apply circular mask for avatar
        img = self._apply_circular_mask(img)
        
        # Ensure output directory exists
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Save with optimization
        save_kwargs = {
            'format': 'PNG',
            'optimize': optimize
        }
        
        img.save(output_path, **save_kwargs)
        
        # Additional PNG optimization if tools available
        if optimize:
            self._optimize_png(output_path)
        
        return output_path
    
    def _crop_to_square(self, img: Image.Image) -> Image.Image:
        """Crop image to square, focusing on center."""
        width, height = img.size
        
        if width == height:
            return img
        
        # Calculate crop box
        if width > height:
            # Landscape: crop sides
            left = (width - height) // 2
            crop_box = (left, 0, left + height, height)
        else:
            # Portrait: crop from top-weighted (keep face area)
            top = 0  # Start from top
            crop_box = (0, top, width, top + width)
        
        return img.crop(crop_box)
    
    def _apply_circular_mask(self, img: Image.Image) -> Image.Image:
        """Apply a circular mask to create a round avatar."""
        # Create mask
        mask = Image.new('L', img.size, 0)
        draw = ImageDraw.Draw(mask)
        draw.ellipse((0, 0) + img.size, fill=255)
        
        # Apply mask
        output = Image.new('RGBA', img.size, (0, 0, 0, 0))
        output.paste(img, (0, 0))
        output.putalpha(mask)
        
        return output
    
    def _optimize_png(self, png_path: Path):
        """
        Apply additional PNG optimization using external tools if available.
        Uses optipng or pngcrush if installed.
        """
        # Try optipng first
        if self._is_tool_available('optipng'):
            try:
                subprocess.run(
                    ['optipng', '-quiet', '-o2', str(png_path)],
                    check=False,
                    timeout=30,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
                return
            except (subprocess.SubprocessError, FileNotFoundError):
                pass
        
        # Fallback to pngcrush
        if self._is_tool_available('pngcrush'):
            try:
                temp_path = png_path.with_suffix('.png.tmp')
                subprocess.run(
                    ['pngcrush', '-q', str(png_path), str(temp_path)],
                    check=False,
                    timeout=30,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
                if temp_path.exists():
                    temp_path.replace(png_path)
            except (subprocess.SubprocessError, FileNotFoundError):
                pass
    
    def _is_tool_available(self, tool_name: str) -> bool:
        """Check if a command-line tool is available."""
        try:
            subprocess.run(
                ['which', tool_name],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            return True
        except (subprocess.SubprocessError, FileNotFoundError):
            return False
    
    def create_thumbnail(
        self,
        sprite_path: Path,
        thumbnail_path: Path
    ) -> Path:
        """
        Create a thumbnail version of an avatar sprite.
        
        Args:
            sprite_path: Path to full-size sprite
            thumbnail_path: Path for thumbnail output
        
        Returns:
            Path to thumbnail
        """
        img = Image.open(sprite_path)
        img.thumbnail(self.THUMBNAIL_SIZE, Image.Resampling.LANCZOS)
        
        thumbnail_path.parent.mkdir(parents=True, exist_ok=True)
        img.save(thumbnail_path, format='PNG', optimize=True)
        
        return thumbnail_path
    
    def convert_svg_to_png(
        self,
        svg_path: Path,
        png_path: Path,
        size: Optional[Tuple[int, int]] = None
    ) -> Optional[Path]:
        """
        Convert SVG to PNG using available tools (cairosvg, inkscape, or ImageMagick).
        
        Args:
            svg_path: Path to SVG file
            png_path: Path for PNG output
            size: Optional target size (width, height)
        
        Returns:
            Path to PNG if successful, None otherwise
        """
        if size is None:
            size = self.SPRITE_SIZE
        
        width, height = size
        
        # Try cairosvg first (Python library)
        try:
            import cairosvg
            cairosvg.svg2png(
                url=str(svg_path),
                write_to=str(png_path),
                output_width=width,
                output_height=height
            )
            return png_path
        except ImportError:
            pass
        
        # Try Inkscape CLI
        if self._is_tool_available('inkscape'):
            try:
                subprocess.run(
                    [
                        'inkscape',
                        str(svg_path),
                        '--export-filename', str(png_path),
                        '--export-width', str(width),
                        '--export-height', str(height)
                    ],
                    check=True,
                    timeout=30,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
                return png_path
            except (subprocess.SubprocessError, FileNotFoundError):
                pass
        
        # Try ImageMagick convert
        if self._is_tool_available('convert'):
            try:
                subprocess.run(
                    [
                        'convert',
                        '-background', 'none',
                        '-resize', f'{width}x{height}',
                        str(svg_path),
                        str(png_path)
                    ],
                    check=True,
                    timeout=30,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
                return png_path
            except (subprocess.SubprocessError, FileNotFoundError):
                pass
        
        return None


def get_default_processor() -> SpriteProcessor:
    """Get the default sprite processor instance."""
    return SpriteProcessor()

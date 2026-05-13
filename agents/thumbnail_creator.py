"""
Agent 4: Thumbnail Creator — Premium YouTube Thumbnails.

Generates thumbnails inspired by top-performing music channels:
  1. Full-bleed AI background (NO circular vignette — wastes space)
  2. Bottom gradient overlay (only darkens bottom 40% for text readability)
  3. Bold, large title with multi-line word wrapping
  4. Thick text outline + glow for maximum readability at small sizes
  5. Channel branding badge in corner
  6. No cheap accent lines
"""

from __future__ import annotations

import logging
import random
import urllib.parse
from io import BytesIO
from pathlib import Path

import requests
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from core.config import Config, OUTPUT_DIR, TEMPLATES_DIR

logger = logging.getLogger(__name__)

# Pollinations.ai is a free, no-auth AI image generation API
POLLINATIONS_URL = "https://image.pollinations.ai/prompt/{prompt}?width={w}&height={h}&nologo=true&enhance=true&model=flux-pro&quality=hd&seed={seed}"


class ThumbnailCreator:
    """
    Generates premium YouTube thumbnails with AI backgrounds and branded text.
    Inspired by top channels like Lofi Girl, Chillhop, and Dreamy.
    """

    def __init__(self) -> None:
        self.config = Config()

    def create(
        self,
        title: str,
        thumbnail_prompt: str,
        *,
        output_name: str | None = None,
    ) -> Path:
        """
        Generate a complete thumbnail.

        Args:
            title: Video title text to overlay.
            thumbnail_prompt: AI image generation prompt from the Marketer.
            output_name: Optional custom filename.

        Returns:
            Path to the generated thumbnail JPEG.
        """
        tc = self.config.channel.thumbnail
        colors = self.config.channel.brand_colors

        if output_name is None:
            safe_title = "".join(c for c in title[:30] if c.isalnum() or c == " ").strip()
            output_name = f"thumb_{safe_title.replace(' ', '_')}.jpg"
        output_path = OUTPUT_DIR / output_name

        logger.info("Generating thumbnail: '%s'", title[:50])

        # Step 1: Get AI background image (full-bleed, NO vignette)
        bg_image = self._fetch_ai_image(thumbnail_prompt, tc.width, tc.height)
        if bg_image is None:
            logger.warning("AI image fetch failed. Using gradient fallback.")
            bg_image = self._create_gradient(tc.width, tc.height, colors.secondary, colors.primary)

        # Step 2: Apply SUBTLE edge darkening (NOT circular vignette)
        bg_image = self._apply_edge_darken(bg_image)

        # Step 3: Bottom gradient overlay (only bottom 45% — keeps image visible)
        bg_image = self._add_bottom_gradient(bg_image, opacity=200)

        # Step 4: Load fonts
        title_font = self._load_font(tc.font_title, size=72)
        subtitle_font = self._load_font(tc.font_subtitle, size=28)
        badge_font = self._load_font(tc.font_subtitle, size=20)

        # Step 5: Draw title text with word wrapping at bottom
        draw = ImageDraw.Draw(bg_image)
        display_title = title[:65]  # Allow longer titles

        # Word-wrap the title into 2 lines max
        lines = self._wrap_text(display_title, title_font, max_width=int(tc.width * 0.85))

        # Position text at bottom of image
        text_y = int(tc.height * 0.72)
        line_height = 80

        for i, line in enumerate(lines[:2]):  # Max 2 lines
            self._draw_text_with_outline(
                draw, line, title_font,
                position=(tc.width // 2, text_y + i * line_height),
                fill=colors.text_light,
                outline_color="#000000",
                outline_width=4,
                anchor="mm",
            )

        # Step 6: Channel branding at bottom-right
        channel_name = self.config.channel.name
        brand_x = tc.width - 30
        brand_y = tc.height - 30
        self._draw_text_with_outline(
            draw, channel_name, subtitle_font,
            position=(brand_x, brand_y),
            fill=colors.accent,
            outline_color="#000000",
            outline_width=3,
            anchor="rb",
        )

        # Step 7: Optional accent glow behind title area
        # (applied BEFORE text in the composite — subtle warm glow)

        # Save
        bg_image = bg_image.convert("RGB")
        bg_image.save(output_path, "JPEG", quality=95)

        size_kb = output_path.stat().st_size / 1024
        logger.info("✅ Thumbnail saved: %s (%.0f KB)", output_path.name, size_kb)
        return output_path

    # ── AI Image Fetch ───────────────────────

    def _fetch_ai_image(self, prompt: str, width: int, height: int) -> Image.Image | None:
        """Fetch an AI-generated image from Pollinations.ai (free, no API key)."""
        enhanced_prompt = (
            f"{prompt}, cinematic lighting, atmospheric, ultra detailed, 4K, "
            f"vibrant colors, no text, no watermark, no borders, full frame, "
            f"professional photography, editorial quality"
        )
        encoded = urllib.parse.quote(enhanced_prompt)
        seed = random.randint(1, 100000)
        url = POLLINATIONS_URL.format(prompt=encoded, w=width, h=height, seed=seed)

        try:
            logger.info("Fetching AI thumbnail image...")
            resp = requests.get(url, timeout=60)
            resp.raise_for_status()

            img = Image.open(BytesIO(resp.content))
            img = img.resize((width, height), Image.Resampling.LANCZOS)
            logger.info("AI thumbnail image fetched successfully.")
            return img

        except Exception as exc:
            logger.error("Pollinations.ai fetch failed: %s", exc)
            return None

    # ── Gradient Fallback ────────────────────

    @staticmethod
    def _create_gradient(w: int, h: int, color_top: str, color_bottom: str) -> Image.Image:
        """Create a vertical gradient image as fallback."""
        img = Image.new("RGB", (w, h))
        draw = ImageDraw.Draw(img)

        r1, g1, b1 = _hex_to_rgb(color_top)
        r2, g2, b2 = _hex_to_rgb(color_bottom)

        for y in range(h):
            ratio = y / h
            r = int(r1 + (r2 - r1) * ratio)
            g = int(g1 + (g2 - g1) * ratio)
            b = int(b1 + (b2 - b1) * ratio)
            draw.line([(0, y), (w, y)], fill=(r, g, b))

        return img

    # ── Visual Effects ───────────────────────

    @staticmethod
    def _apply_edge_darken(img: Image.Image) -> Image.Image:
        """Apply SUBTLE edge darkening — NOT a circular porthole.
        
        Uses a wide ellipse that covers the full image with gentle
        falloff at the extreme edges only. The center 80% stays untouched.
        """
        w, h = img.size
        img = img.convert("RGBA")

        # Create a luminance mask — white = fully visible, black = darkened
        mask = Image.new("L", (w, h), 255)  # Start fully white (visible)
        draw = ImageDraw.Draw(mask)

        # Use the DIAGONAL as radius so ellipse covers the full rectangle
        import math
        radius = int(math.sqrt(w**2 + h**2) / 2)

        # Draw concentric ellipses — only the outermost 20% darkens
        for i in range(radius, 0, -1):
            # Ratio from edge (0=center, 1=edge)
            ratio = 1.0 - (i / radius)
            if ratio < 0.75:
                # Inner 75% = fully bright (no darkening)
                brightness = 255
            else:
                # Outer 25% = gradual darkening
                edge_ratio = (ratio - 0.75) / 0.25
                brightness = int(255 * (1.0 - edge_ratio * 0.5))  # Max 50% darken at edges

            x0 = w // 2 - i
            y0 = h // 2 - int(i * h / w)
            x1 = w // 2 + i
            y1 = h // 2 + int(i * h / w)
            draw.ellipse([x0, y0, x1, y1], fill=brightness)

        # Apply: composite original with black using the mask
        black = Image.new("RGBA", (w, h), (0, 0, 0, 255))
        result = Image.composite(img, black, mask)
        return result

    @staticmethod
    def _add_bottom_gradient(img: Image.Image, opacity: int = 200) -> Image.Image:
        """Add a gradient overlay that only darkens the bottom 45%.
        
        This keeps the top of the image vibrant and visible while
        creating a dark area at the bottom for text readability.
        Like professional YouTube thumbnails.
        """
        w, h = img.size
        img = img.convert("RGBA")
        overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)

        # Gradient starts at 55% from top, ends at bottom
        gradient_start = int(h * 0.55)
        for y in range(gradient_start, h):
            # Progress from 0 (start of gradient) to 1 (bottom)
            progress = (y - gradient_start) / (h - gradient_start)
            # Ease-in curve for smooth transition
            alpha = int(opacity * (progress ** 1.5))
            draw.line([(0, y), (w, y)], fill=(0, 0, 0, alpha))

        return Image.alpha_composite(img, overlay)

    # ── Typography ───────────────────────────

    @staticmethod
    def _load_font(font_name: str, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
        """Load a font, with fallback chain for different environments."""
        # Check project fonts directory
        font_paths = [
            TEMPLATES_DIR / "fonts" / f"{font_name}.ttf",
            TEMPLATES_DIR / "fonts" / f"{font_name}.otf",
        ]
        for fp in font_paths:
            if fp.exists() and fp.stat().st_size > 10000:
                return ImageFont.truetype(str(fp), size)

        # Try downloading Outfit-Bold or Inter from Google Fonts (on first run)
        downloaded = ThumbnailCreator._try_download_font(font_name)
        if downloaded and downloaded.exists():
            return ImageFont.truetype(str(downloaded), size)

        # System font fallback chain
        system_fonts = [
            # Linux (CI environment)
            "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
            "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Bold.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            # Windows
            "C:/Windows/Fonts/arialbd.ttf",
            "C:/Windows/Fonts/arial.ttf",
        ]
        for sf in system_fonts:
            try:
                return ImageFont.truetype(sf, size)
            except OSError:
                continue

        logger.warning("Font '%s' not found. Using default.", font_name)
        return ImageFont.load_default()

    @staticmethod
    def _try_download_font(font_name: str) -> Path | None:
        """Try to download a Google Font if not present."""
        fonts_dir = TEMPLATES_DIR / "fonts"
        fonts_dir.mkdir(parents=True, exist_ok=True)
        target = fonts_dir / f"{font_name}.ttf"

        if target.exists() and target.stat().st_size > 10000:
            return target

        # Map font names to Google Fonts download URLs
        font_urls = {
            "Outfit-Bold": "https://github.com/Outfitio/Outfit-Fonts/raw/main/fonts/ttf/Outfit-Bold.ttf",
            "Inter-Regular": "https://github.com/rsms/inter/raw/master/docs/font-files/Inter-Regular.ttf",
            "Inter-Bold": "https://github.com/rsms/inter/raw/master/docs/font-files/Inter-Bold.ttf",
        }

        url = font_urls.get(font_name)
        if not url:
            return None

        try:
            logger.info("Downloading font: %s", font_name)
            resp = requests.get(url, timeout=30)
            resp.raise_for_status()
            target.write_bytes(resp.content)
            logger.info("Font downloaded: %s (%d KB)", font_name, len(resp.content) // 1024)
            return target
        except Exception as exc:
            logger.warning("Font download failed for %s: %s", font_name, exc)
            return None

    @staticmethod
    def _wrap_text(text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
        """Word-wrap text into multiple lines that fit within max_width."""
        words = text.split()
        lines = []
        current_line = []

        for word in words:
            test_line = " ".join(current_line + [word])
            try:
                bbox = font.getbbox(test_line)
                text_width = bbox[2] - bbox[0]
            except Exception:
                text_width = len(test_line) * 30  # rough fallback

            if text_width <= max_width:
                current_line.append(word)
            else:
                if current_line:
                    lines.append(" ".join(current_line))
                current_line = [word]

        if current_line:
            lines.append(" ".join(current_line))

        return lines

    @staticmethod
    def _draw_text_with_outline(
        draw: ImageDraw.Draw,
        text: str,
        font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
        position: tuple[int, int],
        fill: str,
        outline_color: str = "#000000",
        outline_width: int = 4,
        anchor: str = "mm",
    ) -> None:
        """Draw text with thick outline for maximum readability at small sizes.
        
        Unlike a simple drop shadow, an outline creates a border around
        EVERY edge of the text, making it readable on any background.
        This is how professional YouTube thumbnails handle text.
        """
        x, y = position
        or_, og, ob = _hex_to_rgb(outline_color)
        fr, fg, fb = _hex_to_rgb(fill)

        # Draw thick outline by rendering text at multiple offsets
        for dx in range(-outline_width, outline_width + 1):
            for dy in range(-outline_width, outline_width + 1):
                if dx == 0 and dy == 0:
                    continue
                # Only draw at the border distance for performance
                if abs(dx) + abs(dy) <= outline_width:
                    draw.text(
                        (x + dx, y + dy), text, font=font,
                        fill=(or_, og, ob, 255), anchor=anchor,
                    )

        # Main text on top
        draw.text(position, text, font=font, fill=(fr, fg, fb, 255), anchor=anchor)


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    """Convert #RRGGBB to (R, G, B) tuple."""
    h = hex_color.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)

"""
Agent 4: Thumbnail Creator — AI Image + Pillow Typography.

Generates a unique thumbnail by:
  1. Fetching an AI-generated background image (Pollinations.ai — free, no key)
  2. Overlaying the title text with brand-consistent typography
  3. Adding a subtle vignette and glow effect
"""

from __future__ import annotations

import logging
import urllib.parse
from io import BytesIO
from pathlib import Path

import requests
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from core.config import Config, OUTPUT_DIR, TEMPLATES_DIR

logger = logging.getLogger(__name__)

# Pollinations.ai is a free, no-auth AI image generation API
POLLINATIONS_URL = "https://image.pollinations.ai/prompt/{prompt}?width={w}&height={h}&nologo=true&enhance=true&model=flux-pro&quality=hd"


class ThumbnailCreator:
    """
    Generates YouTube thumbnails with AI backgrounds and branded text.
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

        # Step 1: Get AI background image
        bg_image = self._fetch_ai_image(thumbnail_prompt, tc.width, tc.height)
        if bg_image is None:
            # Fallback: create a gradient background
            logger.warning("AI image fetch failed. Using gradient fallback.")
            bg_image = self._create_gradient(tc.width, tc.height, colors.secondary, colors.primary)

        # Step 2: Apply vignette
        bg_image = self._apply_vignette(bg_image)

        # Step 3: Add dark overlay for text readability
        bg_image = self._add_dark_overlay(bg_image, opacity=120)

        # Step 4: Add title text
        draw = ImageDraw.Draw(bg_image)
        title_font = self._load_font(tc.font_title, size=58)
        subtitle_font = self._load_font(tc.font_subtitle, size=24)

        # Truncate title for thumbnail
        display_title = title[:tc.title_max_chars]
        if len(title) > tc.title_max_chars:
            display_title = display_title.rsplit(" ", 1)[0] + "..."

        # Draw title with shadow
        self._draw_text_with_shadow(
            draw, display_title, title_font,
            position=(tc.width // 2, tc.height // 2 - 20),
            fill=colors.text_light,
            shadow_color="#000000",
            anchor="mm",
        )

        # Draw channel name
        channel_name = self.config.channel.name
        self._draw_text_with_shadow(
            draw, channel_name, subtitle_font,
            position=(tc.width // 2, tc.height // 2 + 40),
            fill=colors.accent,
            shadow_color="#000000",
            anchor="mm",
        )

        # Step 5: Add glow accent line
        self._add_glow_line(bg_image, y=tc.height // 2 + 65, color=colors.accent)

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
            f"{prompt}, cinematic lighting, atmospheric, 4K, "
            f"no text, no watermark, professional photography"
        )
        encoded = urllib.parse.quote(enhanced_prompt)
        url = POLLINATIONS_URL.format(prompt=encoded, w=width, h=height)

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
    def _apply_vignette(img: Image.Image) -> Image.Image:
        """Apply a subtle vignette (darkened edges)."""
        w, h = img.size
        vignette = Image.new("L", (w, h), 0)
        draw = ImageDraw.Draw(vignette)

        # Draw concentric ellipses from bright center to dark edges
        for i in range(min(w, h) // 2, 0, -1):
            brightness = int(255 * (i / (min(w, h) // 2)) ** 0.7)
            x0 = w // 2 - i
            y0 = h // 2 - i
            x1 = w // 2 + i
            y1 = h // 2 + i
            draw.ellipse([x0, y0, x1, y1], fill=brightness)

        # Apply as alpha mask
        img = img.convert("RGBA")
        vignette_rgba = Image.merge("RGBA", (
            Image.new("L", (w, h), 0),
            Image.new("L", (w, h), 0),
            Image.new("L", (w, h), 0),
            vignette,
        ))
        # Darken edges by compositing with black
        black = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        result = Image.composite(img, black, vignette)
        return result

    @staticmethod
    def _add_dark_overlay(img: Image.Image, opacity: int = 120) -> Image.Image:
        """Add a semi-transparent dark overlay for text readability."""
        img = img.convert("RGBA")
        overlay = Image.new("RGBA", img.size, (0, 0, 0, opacity))
        return Image.alpha_composite(img, overlay)

    @staticmethod
    def _add_glow_line(img: Image.Image, y: int, color: str, width: int = 3) -> None:
        """Draw a subtle glowing accent line."""
        draw = ImageDraw.Draw(img)
        r, g, b = _hex_to_rgb(color)
        w = img.size[0]
        margin = int(w * 0.3)

        # Glow (thicker, semi-transparent)
        for offset in range(-2, 3):
            draw.line(
                [(margin, y + offset), (w - margin, y + offset)],
                fill=(r, g, b, 60),
                width=1,
            )
        # Core line
        draw.line([(margin, y), (w - margin, y)], fill=(r, g, b, 200), width=width)

    # ── Typography ───────────────────────────

    @staticmethod
    def _load_font(font_name: str, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
        """Load a font, falling back to default if not available."""
        font_paths = [
            TEMPLATES_DIR / "fonts" / f"{font_name}.ttf",
            TEMPLATES_DIR / "fonts" / f"{font_name}.otf",
        ]
        for fp in font_paths:
            if fp.exists():
                return ImageFont.truetype(str(fp), size)

        # Try system fonts
        try:
            return ImageFont.truetype("arial.ttf", size)
        except OSError:
            pass
        try:
            return ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", size)
        except OSError:
            pass

        logger.warning("Font '%s' not found. Using default.", font_name)
        return ImageFont.load_default()

    @staticmethod
    def _draw_text_with_shadow(
        draw: ImageDraw.Draw,
        text: str,
        font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
        position: tuple[int, int],
        fill: str,
        shadow_color: str = "#000000",
        anchor: str = "mm",
        shadow_offset: int = 3,
    ) -> None:
        """Draw text with a drop shadow for depth."""
        x, y = position
        sr, sg, sb = _hex_to_rgb(shadow_color)
        fr, fg, fb = _hex_to_rgb(fill)

        # Shadow
        draw.text(
            (x + shadow_offset, y + shadow_offset),
            text, font=font, fill=(sr, sg, sb, 180), anchor=anchor,
        )
        # Main text
        draw.text(position, text, font=font, fill=(fr, fg, fb, 255), anchor=anchor)


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    """Convert #RRGGBB to (R, G, B) tuple."""
    h = hex_color.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)

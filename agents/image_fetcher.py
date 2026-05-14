"""
Agent 2B: The Image Fetcher — AI Image Generation Pipeline.

Downloads AI-generated images based on Director prompts using a
multi-tier generation system:
  1. PRIMARY: Gemini Imagen (highest quality, uses existing API keys)
  2. BACKUP:  Cloudflare Workers AI / FLUX (free, reliable fallback)
  3. LEGACY:  Pollinations.ai (last resort)

All images are post-processed with PIL for sharpness and color enhancement.
"""

import logging
import os
import random
import time
import urllib.parse
from pathlib import Path

from agents.director import CreativeBrief
from core.config import TEMP_DIR

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────
#  Post-Processing Pipeline (applies to ALL images)
# ──────────────────────────────────────────────
# Target output resolution for all images (Full HD 1080p)
TARGET_WIDTH = 1920
TARGET_HEIGHT = 1080


def _enhance_image(path: Path) -> Path:
    """Upscale to 1920x1080, sharpen, and color-enhance an image using PIL."""
    try:
        from PIL import Image, ImageEnhance, ImageFilter

        img = Image.open(path).convert("RGB")

        # Step 1: Upscale to exactly 1920x1080 if smaller
        if img.width < TARGET_WIDTH or img.height < TARGET_HEIGHT:
            # Scale up maintaining aspect ratio, then crop to exact 16:9
            scale = max(TARGET_WIDTH / img.width, TARGET_HEIGHT / img.height)
            new_w = int(img.width * scale)
            new_h = int(img.height * scale)
            img = img.resize((new_w, new_h), Image.LANCZOS)

            # Center crop to exact 1920x1080
            left = (new_w - TARGET_WIDTH) // 2
            top = (new_h - TARGET_HEIGHT) // 2
            img = img.crop((left, top, left + TARGET_WIDTH, top + TARGET_HEIGHT))
            logger.info("Upscaled %s to %dx%d", path.name, TARGET_WIDTH, TARGET_HEIGHT)

        # Step 2: Unsharp mask for sharpening
        img = img.filter(ImageFilter.UnsharpMask(radius=2, percent=150, threshold=3))

        # Step 3: Boost contrast slightly
        img = ImageEnhance.Contrast(img).enhance(1.08)

        # Step 4: Boost color saturation for vibrant aesthetics
        img = ImageEnhance.Color(img).enhance(1.12)

        # Step 5: Slight brightness boost for dark scenes
        img = ImageEnhance.Brightness(img).enhance(1.03)

        img.save(path, "JPEG", quality=95)
        return path
    except Exception as exc:
        logger.warning("Image enhancement failed for %s: %s", path.name, exc)
        return path


# ──────────────────────────────────────────────
#  Tier 1: Gemini Imagen
# ──────────────────────────────────────────────
class GeminiImageGenerator:
    """Generate images using Google Gemini's native image generation."""

    def __init__(self):
        self._keys = []
        for i in range(1, 7):
            key = os.getenv(f"GEMINI_API_KEY_{i}", "")
            if key:
                self._keys.append(key)
        self._key_index = 0

    @property
    def available(self) -> bool:
        return len(self._keys) > 0

    def _next_key(self) -> str:
        key = self._keys[self._key_index % len(self._keys)]
        self._key_index += 1
        return key

    def generate(self, prompt: str, output_path: Path, retries: int = 3) -> Path | None:
        """Generate a single image via Gemini Imagen API using google.genai SDK."""
        try:
            import google.genai as genai
        except ImportError:
            logger.warning("google-genai not installed. Skipping Gemini Imagen.")
            return None

        for attempt in range(retries):
            api_key = self._next_key()
            try:
                client = genai.Client(api_key=api_key)

                enhanced_prompt = (
                    f"{prompt}, wide landscape 16:9 aspect ratio, "
                    f"masterpiece quality, ultra high resolution, "
                    f"ultra detailed, sharp focus, professional photography, "
                    f"cinematic lighting, best quality, no text no watermark"
                )

                response = client.models.generate_content(
                    model="gemini-2.5-flash-image",
                    contents=enhanced_prompt,
                    config=genai.types.GenerateContentConfig(
                        response_modalities=["IMAGE", "TEXT"],
                    ),
                )

                # Extract image from response
                if response.candidates:
                    for part in response.candidates[0].content.parts:
                        if hasattr(part, "inline_data") and part.inline_data:
                            image_data = part.inline_data.data
                            with open(output_path, "wb") as f:
                                f.write(image_data)

                            if output_path.exists() and output_path.stat().st_size > 500:
                                logger.info("Gemini Imagen generated: %s (%d KB)",
                                            output_path.name,
                                            output_path.stat().st_size // 1024)
                                return output_path

                logger.warning("Gemini Imagen returned no image data (attempt %d/%d)",
                               attempt + 1, retries)

            except Exception as exc:
                exc_str = str(exc).lower()
                if "429" in exc_str or "resource_exhausted" in exc_str:
                    logger.warning("Gemini Imagen rate limited (key %d). Rotating...",
                                   self._key_index)
                    time.sleep(10 + random.uniform(0, 5))
                elif "safety" in exc_str or "block" in exc_str:
                    logger.warning("Gemini Imagen blocked prompt (safety filter): %s",
                                   prompt[:80])
                    return None  # Don't retry safety blocks — go to backup
                elif "404" in exc_str or "not found" in exc_str:
                    logger.warning("Gemini Imagen error: Model not found. Skipping.")
                    return None
                elif "400" in exc_str or "invalid_argument" in exc_str or "paid plan" in exc_str:
                    logger.warning("Gemini Imagen requires paid plan. Skipping.")
                    return None
                elif "limit: 0" in exc_str or "quota exceeded" in exc_str:
                    logger.warning("Gemini Imagen quota exceeded (free tier?). Skipping.")
                    return None
                else:
                    logger.warning("Gemini Imagen error (attempt %d/%d): %s",
                                   attempt + 1, retries, exc)
                    time.sleep(5 + attempt * 5)

        return None


# ──────────────────────────────────────────────
#  Tier 2: Cloudflare Workers AI (FLUX)
# ──────────────────────────────────────────────
class CloudflareImageGenerator:
    """Generate images using Cloudflare Workers AI (FLUX model)."""

    def __init__(self):
        self.account_id = os.getenv("CLOUDFLARE_ACCOUNT_ID", "")
        self.api_token = os.getenv("CLOUDFLARE_API_TOKEN", "").replace('"', '')

    @property
    def available(self) -> bool:
        return bool(self.account_id and self.api_token)

    def generate(self, prompt: str, output_path: Path, retries: int = 3) -> Path | None:
        """Generate a single image via Cloudflare Workers AI."""
        import requests

        # Use FLUX model for best quality on Cloudflare
        model = "@cf/black-forest-labs/flux-1-schnell"
        url = f"https://api.cloudflare.com/client/v4/accounts/{self.account_id}/ai/run/{model}"

        enhanced_prompt = (
            f"{prompt}, masterpiece quality, highly detailed, "
            f"cinematic lighting, sharp focus, professional, 8k"
        )

        for attempt in range(retries):
            try:
                response = requests.post(
                    url,
                    headers={
                        "Authorization": f"Bearer {self.api_token}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "prompt": enhanced_prompt,
                        "num_steps": 8,
                        "width": 1024,
                        "height": 576,
                    },
                    timeout=120,
                )

                if response.status_code == 200:
                    # Cloudflare returns raw image bytes
                    content_type = response.headers.get("Content-Type", "")
                    if "application/json" in content_type:
                        try:
                            data = response.json()
                            if data.get("success") and "result" in data and "image" in data["result"]:
                                import base64
                                image_data = base64.b64decode(data["result"]["image"])
                                with open(output_path, "wb") as f:
                                    f.write(image_data)

                                if output_path.exists() and output_path.stat().st_size > 500:
                                    logger.info("Cloudflare FLUX generated: %s (%d KB)",
                                                output_path.name,
                                                output_path.stat().st_size // 1024)
                                    return output_path
                            else:
                                logger.warning("Cloudflare returned JSON but no image: %s", str(data)[:200])
                        except Exception as e:
                            logger.warning("Cloudflare failed to parse JSON: %s", e)
                    elif "image" in content_type or len(response.content) > 1000:
                        with open(output_path, "wb") as f:
                            f.write(response.content)

                        if output_path.exists() and output_path.stat().st_size > 500:
                            logger.info("Cloudflare FLUX generated: %s (%d KB)",
                                        output_path.name,
                                        output_path.stat().st_size // 1024)
                            return output_path

                elif response.status_code == 429:
                    logger.warning("Cloudflare rate limited. Sleeping...")
                    time.sleep(10 + random.uniform(0, 5))
                else:
                    logger.warning("Cloudflare error %d (attempt %d/%d): %s",
                                   response.status_code, attempt + 1, retries,
                                   response.text[:200])

            except Exception as exc:
                logger.warning("Cloudflare request failed (attempt %d/%d): %s",
                               attempt + 1, retries, exc)
                time.sleep(2 + attempt * 3)

        return None


# ──────────────────────────────────────────────
#  Tier 3: Pollinations.ai (Legacy Fallback)
# ──────────────────────────────────────────────
class PollinationsImageGenerator:
    """Legacy fallback — free Pollinations.ai API."""

    def generate(self, prompt: str, output_path: Path, retries: int = 3) -> Path | None:
        import requests

        encoded_prompt = urllib.parse.quote(
            f"{prompt}, masterpiece, 8k resolution, cinematic lighting, "
            f"ultra detailed, sharp focus, best quality, professional"
        )
        seed = random.randint(1, 100000)
        negative = urllib.parse.quote(
            "blurry, low quality, distorted, ugly, bad anatomy, watermark, "
            "text, signature, jpeg artifacts, deformed, extra limbs"
        )
        url = (
            f"https://image.pollinations.ai/prompt/{encoded_prompt}"
            f"?width=1920&height=1080&nologo=true&seed={seed}"
            f"&model=flux-pro&enhance=true&quality=hd&negative={negative}"
        )

        for attempt in range(retries):
            try:
                response = requests.get(
                    url,
                    headers={"User-Agent": "Mozilla/5.0"},
                    timeout=120,
                )
                response.raise_for_status()

                content_type = response.headers.get("Content-Type", "")
                if content_type not in ["image/jpeg", "image/png", "image/webp"]:
                    raise ValueError(f"Non-image content type: {content_type}")

                with open(output_path, "wb") as f:
                    f.write(response.content)

                if output_path.stat().st_size > 100:
                    logger.info("Pollinations generated: %s", output_path.name)
                    return output_path

            except Exception as exc:
                logger.warning("Pollinations failed (attempt %d/%d): %s",
                               attempt + 1, retries, exc)
                time.sleep(5 * (2 ** attempt) + random.uniform(0, 3))

        return None


# ──────────────────────────────────────────────
#  Main Image Fetcher (Multi-Tier Orchestrator)
# ──────────────────────────────────────────────
class ImageFetcher:
    """
    Downloads AI-generated images using a tiered generation system:
      1. Gemini Imagen (best quality)
      2. Cloudflare FLUX (reliable backup)
      3. Pollinations.ai (legacy fallback)

    All images are post-processed with PIL sharpening and color enhancement.
    """

    def __init__(self):
        self.temp_dir = TEMP_DIR
        self.temp_dir.mkdir(exist_ok=True)

        self.gemini = GeminiImageGenerator()
        self.cloudflare = CloudflareImageGenerator()
        self.pollinations = PollinationsImageGenerator()

        # Log available generators
        tiers = []
        if self.gemini.available:
            tiers.append(f"Gemini ({len(self.gemini._keys)} keys)")
        if self.cloudflare.available:
            tiers.append("Cloudflare FLUX")
        tiers.append("Pollinations (always)")
        logger.info("Image generators available: %s", " → ".join(tiers))

    def fetch_images(self, brief: CreativeBrief) -> list[Path]:
        """
        Generate and download images based on Director's creative brief.
        Uses section-specific prompts from Creative Engine when available.
        """
        # Prefer section-specific prompts from Creative Engine
        if brief.has_sections:
            prompts = [sec.image_prompt for sec in brief.sections if sec.image_prompt]
            target_count = len(brief.sections)
            logger.info("Using %d section-specific image prompts from Creative Engine",
                        len(prompts))
        else:
            prompts = brief.image_prompts
            target_count = 5

        if not prompts:
            logger.warning("No image prompts found. Generating default prompts.")
            prompts = [
                f"{brief.visual_style}, {brief.mood} vibe, highly detailed 4k"
            ] * target_count

        # Derive art style suffix from the Director's analysis
        style_suffix = self._get_style_suffix(brief)

        # Ensure correct prompt count
        while len(prompts) < target_count:
            prompts.append(prompts[-1])
        prompts = prompts[:target_count]

        logger.info("Generating %d images (Gemini → Cloudflare → Pollinations)...",
                     len(prompts))

        # Style anchor: prepend visual_style for consistency
        visual_anchor = brief.visual_style if brief.visual_style else ""

        image_paths: list[Path | None] = [None] * len(prompts)

        # Generate images with tiered fallback (sequential to respect rate limits)
        for i, prompt in enumerate(prompts):
            output_path = self.temp_dir / f"bg_img_{i:02d}.jpg"
            full_prompt = f"{visual_anchor} style, {prompt}, {style_suffix}"

            # Stagger requests to avoid rate limiting (Gemini Imagen needs breathing room)
            # ~8s between requests × 10 images = ~80s total wait, well within pipeline budget
            if i > 0:
                time.sleep(8)

            # Tier 1: Gemini Imagen
            result = None
            if self.gemini.available:
                result = self.gemini.generate(full_prompt, output_path)

            # Tier 2: Cloudflare FLUX
            if result is None and self.cloudflare.available:
                logger.info("Falling back to Cloudflare for image %d/%d",
                            i + 1, len(prompts))
                result = self.cloudflare.generate(full_prompt, output_path)

            # Tier 3: Pollinations.ai
            if result is None:
                logger.info("Falling back to Pollinations for image %d/%d",
                            i + 1, len(prompts))
                result = self.pollinations.generate(full_prompt, output_path)

            # Post-process: sharpen + color enhance
            if result is not None:
                result = _enhance_image(result)

            image_paths[i] = result

        # Fill failed slots with previous successful image
        final_paths: list[Path] = []
        last_good: Path | None = None
        for path in image_paths:
            if path is not None:
                final_paths.append(path)
                last_good = path
            elif last_good is not None:
                logger.warning("Falling back to previous image for a failed slot.")
                final_paths.append(last_good)

        if not final_paths:
            raise RuntimeError("Failed to generate any images for the video.")

        # Log generation stats
        gemini_count = sum(1 for p in image_paths if p is not None)
        logger.info("Image generation complete: %d/%d successful, all enhanced",
                     gemini_count, len(prompts))

        return final_paths

    @staticmethod
    def _get_style_suffix(brief: CreativeBrief) -> str:
        """Derive an art style suffix from the Director's genre/mood analysis.

        Instead of hardcoding 'anime lo-fi style' for every song, this maps
        the detected genre and energy to an appropriate visual style.
        """
        genre = getattr(brief.song_dna, 'genre', '').lower() if hasattr(brief, 'song_dna') else ''
        mood = brief.mood.lower()
        energy = brief.energy.lower()

        # Check genre keywords for specific styles
        if any(kw in genre for kw in ['lo-fi', 'lofi', 'chill', 'ambient']):
            return "anime lo-fi style, soft pastel tones, cozy aesthetic"
        elif any(kw in genre for kw in ['party', 'dance', 'edm', 'club', 'pop']):
            return "vibrant photo-realistic style, neon lights, dynamic energy"
        elif any(kw in genre for kw in ['devotional', 'spiritual', 'bhajan', 'kirtan']):
            return "ethereal spiritual art style, warm golden tones, divine atmosphere"
        elif any(kw in genre for kw in ['festive', 'holi', 'diwali', 'celebration']):
            return "vibrant festive photo-realistic style, rich saturated colors, joyful energy"
        elif any(kw in genre for kw in ['romantic', 'love']):
            return "cinematic romantic style, warm soft lighting, dreamy bokeh"
        elif any(kw in genre for kw in ['sad', 'melanchol', 'heartbreak', 'breakup']):
            return "cinematic moody style, desaturated tones, atmospheric fog"
        elif any(kw in genre for kw in ['rap', 'hip hop', 'hip-hop', 'trap']):
            return "urban street art style, bold contrasts, gritty cinematic"
        elif any(kw in genre for kw in ['rock', 'metal', 'punk']):
            return "dark cinematic concert style, dramatic lighting, high contrast"

        # Fall back to mood/energy if genre doesn't match
        if energy == 'high':
            return "vibrant cinematic style, dynamic lighting, bold colors"
        elif mood in ('melancholic', 'dark', 'anxious'):
            return "cinematic moody style, desaturated cool tones, atmospheric"
        elif mood in ('dreamy', 'peaceful'):
            return "soft dreamy anime style, pastel tones, ethereal glow"
        elif mood in ('romantic',):
            return "cinematic romantic style, warm golden hour lighting"
        elif mood in ('nostalgic',):
            return "vintage film style, warm faded tones, nostalgic atmosphere"

        # Ultimate fallback
        return f"{brief.visual_style} style, highly detailed"

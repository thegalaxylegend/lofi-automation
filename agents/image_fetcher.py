import urllib.parse
import urllib.request
import logging
from pathlib import Path

from agents.director import CreativeBrief
from core.config import TEMP_DIR

logger = logging.getLogger(__name__)

class ImageFetcher:
    """
    Downloads AI-generated images using the free Pollinations.ai API based on Director prompts.
    """
    def __init__(self):
        self.temp_dir = TEMP_DIR
        self.temp_dir.mkdir(exist_ok=True)

    def fetch_images(self, brief: CreativeBrief) -> list[Path]:
        """
        Generates and downloads images based on Director's creative brief.
        Uses section-specific prompts from Creative Engine when available,
        falls back to generic image_prompts otherwise.
        """
        # Prefer section-specific prompts from Creative Engine
        if brief.has_sections:
            prompts = [sec.image_prompt for sec in brief.sections if sec.image_prompt]
            target_count = len(brief.sections)
            logger.info("Using %d section-specific image prompts from Creative Engine", len(prompts))
        else:
            prompts = brief.image_prompts
            target_count = 5

        if not prompts:
            logger.warning("No image prompts found. Generating default prompts.")
            prompts = [f"{brief.visual_style}, {brief.mood} vibe, highly detailed 4k"] * target_count

        # Derive art style suffix from the Director's analysis instead of hardcoding "lo-fi"
        style_suffix = self._get_style_suffix(brief)

        # Ensure we have the right number of prompts
        while len(prompts) < target_count:
            prompts.append(prompts[-1])
        prompts = prompts[:target_count]

        logger.info(f"Generating {len(prompts)} images via Pollinations.ai...")

        image_paths = [None] * len(prompts)
        
        import requests
        import time
        import random
        from concurrent.futures import ThreadPoolExecutor, as_completed

        def download_single(i, prompt):
            # Stagger startup to prevent hammering the API instantly
            time.sleep(i * 1.5)

            safe_name = f"bg_img_{i:02d}.jpg"
            output_path = self.temp_dir / safe_name
            
            # Style anchoring: visual_style goes FIRST so Pollinations AI
            # treats it as the primary style instruction for consistency
            visual_anchor = brief.visual_style if brief.visual_style else ""
            full_prompt = f"{visual_anchor} style, {prompt}, {style_suffix}, masterpiece, 8k resolution, cinematic lighting, ultra detailed, sharp focus, best quality, professional, consistent art style throughout"
            encoded_prompt = urllib.parse.quote(full_prompt)
            # Added a random seed to bypass cache and ensure unique images
            seed = random.randint(1, 100000)
            # Use flux-pro for higher quality, request 2K resolution for sharper downscale
            url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?width=2560&height=1440&nologo=true&seed={seed}&model=flux-pro&enhance=true&quality=hd"
            
            for attempt in range(5):
                try:
                    logger.info(f"Downloading image {i+1}/{target_count} (Attempt {attempt+1})...")
                    headers = {
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                    }
                    response = requests.get(url, headers=headers, timeout=120)
                    response.raise_for_status()
                    
                    content_type = response.headers.get("Content-Type", "")
                    if content_type not in ["image/jpeg", "image/png", "image/webp"]:
                        raise ValueError(f"Received non-image content type: {content_type}")

                    with open(output_path, "wb") as f:
                        f.write(response.content)
                    
                    if output_path.stat().st_size < 100:
                        raise ValueError("Downloaded image is too small (likely corrupt).")
                        
                    return i, output_path
                except requests.exceptions.HTTPError as e:
                    logger.error(f"Failed to generate image {i+1} on attempt {attempt+1}: {e}")
                    if attempt < 4:
                        if e.response is not None and e.response.status_code == 429:
                            retry_after = int(e.response.headers.get("Retry-After", 15))
                            logger.info(f"Rate limited (429). Sleeping {retry_after}s...")
                            time.sleep(retry_after)
                        else:
                            wait_time = 5 * (2 ** attempt)
                            time.sleep(wait_time + random.uniform(0, 3))
                except Exception as e:
                    logger.error(f"Failed to generate image {i+1} on attempt {attempt+1}: {e}")
                    if attempt < 4:
                        wait_time = 5 * (2 ** attempt)
                        logger.info(f"Sleeping {wait_time}s before retrying slot {i+1}...")
                        time.sleep(wait_time + random.uniform(0, 3))
            
            logger.error(f"Slot {i+1} completely failed.")
            return i, None

        # Execute in parallel with 2 workers to prevent Pollinations.ai 429 Rate Limits
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(download_single, i, p) for i, p in enumerate(prompts)]
            
            # Iterate through futures in the order they were submitted (preserves narrative order)
            for i, future in enumerate(futures):
                try:
                    idx, path = future.result()
                    image_paths[idx] = path
                except Exception as e:
                    logger.error(f"Catastrophic thread failure for slot {i+1}: {e}")
                    image_paths[i] = None

        # Fill in any failed slots with the previous successful image
        final_paths = []
        last_good = None
        for path in image_paths:
            if path is not None:
                final_paths.append(path)
                last_good = path
            elif last_good is not None:
                logger.warning("Falling back to previous image for a failed slot.")
                final_paths.append(last_good)

        if not final_paths:
            raise RuntimeError("Failed to generate any images for the video.")
            
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
        visual = brief.visual_style.lower()

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

        # Ultimate fallback: use the Director's visual_style directly
        return f"{brief.visual_style} style, highly detailed"


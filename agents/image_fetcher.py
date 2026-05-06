import urllib.parse
import urllib.request
import logging
from pathlib import Path

from agents.director import CreativeBrief

logger = logging.getLogger(__name__)

class ImageFetcher:
    """
    Downloads AI-generated images using the free Pollinations.ai API based on Director prompts.
    """
    def __init__(self):
        self.temp_dir = Path("temp")
        self.temp_dir.mkdir(exist_ok=True)

    def fetch_images(self, brief: CreativeBrief) -> list[Path]:
        """
        Generates and downloads a sequence of images for the video in parallel.
        """
        prompts = brief.image_prompts
        if not prompts:
            logger.warning("No image prompts found. Generating a default prompt.")
            prompts = [f"lo-fi aesthetic, {brief.mood} vibe, highly detailed 4k"] * 10

        # Ensure exactly 10 images
        while len(prompts) < 10:
            prompts.append(prompts[-1])
        prompts = prompts[:10]

        logger.info(f"Generating {len(prompts)} images via Pollinations.ai (Parallel)...")

        image_paths = [None] * 10
        
        import requests
        import time
        import random
        from concurrent.futures import ThreadPoolExecutor, as_completed

        def download_single(i, prompt):
            # Stagger startup to prevent hammering the API instantly
            time.sleep(i * 1.5)

            safe_name = f"bg_img_{i:02d}.jpg"
            output_path = self.temp_dir / safe_name
            
            full_prompt = f"{prompt}, anime lo-fi style, masterpiece, 8k resolution, cinematic lighting"
            encoded_prompt = urllib.parse.quote(full_prompt)
            # Added a random seed to bypass cache and ensure unique images
            seed = random.randint(1, 100000)
            # explicitly ask for flux model which is best for 16:9
            url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?width=1920&height=1080&nologo=true&seed={seed}&model=flux"
            
            for attempt in range(5):
                try:
                    logger.info(f"Downloading image {i+1}/10 (Attempt {attempt+1})...")
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

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
        Generates and downloads a sequence of images for the video.
        """
        image_paths = []
        prompts = brief.image_prompts
        if not prompts:
            logger.warning("No image prompts found. Generating a default prompt.")
            prompts = [f"lo-fi aesthetic, {brief.mood} vibe, highly detailed 4k"] * 10

        # Ensure we have at least 10 images (if they provided fewer, we repeat the last one)
        while len(prompts) < 10:
            prompts.append(prompts[-1])
            
        # We only need 10 images to avoid taking too long
        prompts = prompts[:10]

        logger.info(f"Generating {len(prompts)} images via Pollinations.ai...")

        for i, prompt in enumerate(prompts):
            # Safe filename
            safe_name = f"bg_img_{i:02d}.jpg"
            output_path = self.temp_dir / safe_name
            
            # Encode prompt for URL
            # Add some base style tags to ensure consistency
            full_prompt = f"{prompt}, anime lo-fi style, masterpiece, 8k resolution, cinematic lighting"
            encoded_prompt = urllib.parse.quote(full_prompt)
            
            url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?width=1920&height=1080&nologo=true"
            
            try:
                logger.info(f"Downloading image {i+1}/10...")
                import requests
                headers = {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                }
                response = requests.get(url, headers=headers, timeout=60)
                response.raise_for_status()
                with open(output_path, "wb") as f:
                    f.write(response.content)
                image_paths.append(output_path)
            except Exception as e:
                logger.error(f"Failed to generate image {i+1}: {e}")
                
        if not image_paths:
            raise RuntimeError("Failed to generate any images for the video.")
            
        return image_paths

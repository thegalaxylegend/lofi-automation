"""
Agent 10: The Distributor — AI-Directed YouTube Shorts Factory.

Uses the Director's creative brief to create intelligent Shorts:
  1. AI-selected best segment (not blind middle 40 seconds)
  2. Vertical crop with slow pan for visual movement
  3. Hook text in first 1.5 seconds (POV style)
  4. Emotional mood text in the middle
  5. No fade out (encourages replay/loop)

Supports FFmpeg builds with or without drawtext filter.
When drawtext is unavailable, text is burned into the background image via Pillow.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from agents.director import CreativeBrief
from core.config import Config, OUTPUT_DIR, TEMP_DIR, TEMPLATES_DIR

logger = logging.getLogger(__name__)


def _check_drawtext_support() -> bool:
    """Check if the installed FFmpeg has drawtext filter support."""
    try:
        result = subprocess.run(
            ["ffmpeg", "-filters"],
            capture_output=True, text=True, timeout=10,
        )
        return "drawtext" in result.stdout
    except (subprocess.SubprocessError, FileNotFoundError):
        return False


def _burn_text_on_image_for_short(
    image_path: Path,
    texts: list[tuple[str, str, int, float]],  # [(text, position, font_size, opacity), ...]
    font_path: Path,
    target_w: int,
    target_h: int,
) -> Path:
    """
    Burn multiple text overlays onto a vertical image for Shorts.
    Returns path to the new image.
    """
    from PIL import Image, ImageDraw, ImageFont

    img = Image.open(image_path).convert("RGBA")
    # Resize to target dimensions
    img = img.resize((target_w, target_h), Image.LANCZOS)
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    for text, position, font_size, opacity in texts:
        if not text:
            continue
        try:
            font = ImageFont.truetype(str(font_path), font_size)
        except Exception:
            font = ImageFont.load_default()

        bbox = draw.textbbox((0, 0), text, font=font)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]
        w, h = img.size

        pos_map = {
            "hook": ((w - tw) // 2, int(h * 0.35)),
            "mood": ((w - tw) // 2, int(h * 0.42)),
        }
        xy = pos_map.get(position, ((w - tw) // 2, int(h * 0.40)))

        alpha = int(255 * opacity)
        # Draw text with dark border for readability
        border_w = 2
        for dx in range(-border_w, border_w + 1):
            for dy in range(-border_w, border_w + 1):
                if dx != 0 or dy != 0:
                    draw.text((xy[0] + dx, xy[1] + dy), text, font=font, fill=(0, 0, 0, int(alpha * 0.6)))
        draw.text(xy, text, font=font, fill=(255, 255, 255, alpha))

    result = Image.alpha_composite(img, overlay).convert("RGB")
    out_path = image_path.parent / f"{image_path.stem}_short_text{image_path.suffix}"
    result.save(out_path, quality=95)
    return out_path


class Distributor:
    """Creates AI-directed YouTube Shorts from the Director's creative brief."""

    def __init__(self) -> None:
        self.config = Config()
        self._has_drawtext = _check_drawtext_support()
        if not self._has_drawtext:
            logger.warning("Distributor: drawtext not available — using Pillow text burn-in for Shorts")

    def create_short(
        self,
        audio_path: str | Path,
        background_path: str | Path | None = None,
        brief: CreativeBrief = None,
        shorts_text: str = "",
        *,
        video_source_path: str | Path | None = None,
        output_name: str | None = None,
    ) -> Path:
        """
        Creates a YouTube Short by taking the best segment from the exported video.
        FITS horizontal video into vertical format via letterboxing (no zoom).
        Removes all text overlays as per user request.
        """
        audio_path = Path(audio_path)
        sc = self.config.channel.shorts

        if output_name is None:
            output_name = f"{audio_path.stem}_short.mp4"
        output_path = OUTPUT_DIR / output_name

        w, h = sc.width, sc.height  # 1080x1920
        max_dur = sc.max_duration_sec

        # Get audio duration
        duration = self._get_duration(audio_path)
        if duration <= 0:
            raise ValueError(f"Cannot get duration for {audio_path}")

        # AI-selected segment from Director's brief
        if brief and brief.has_sections and brief.shorts.duration_sec > 0:
            start_time = brief.shorts.recommended_start_sec
            segment_dur = min(brief.shorts.duration_sec, max_dur)
            logger.info(
                "AI-selected Short segment: %.1fs-%.1fs (%s)",
                start_time, start_time + segment_dur, brief.shorts.reasoning[:60],
            )
        else:
            # Fallback: middle portion
            if duration > max_dur:
                start_time = max(0, (duration - max_dur) / 2)
                segment_dur = max_dur
            else:
                start_time, segment_dur = 0, duration

        # Ensure segment doesn't exceed audio
        if start_time + segment_dur > duration:
            start_time = max(0, duration - segment_dur)

        logger.info(
            "Distributor: Short from %.1fs-%.1fs (%.1fs)",
            start_time, start_time + segment_dur, segment_dur,
        )

        # Decide on source: Prioritize video_source_path (the exported 1080p video)
        is_video = False
        if video_source_path and Path(video_source_path).exists():
            source_path = Path(video_source_path)
            is_video = True
            logger.info("Using exported video as Short source: %s", source_path.name)
        elif background_path and Path(background_path).exists():
            source_path = Path(background_path)
            logger.info("Falling back to image as Short source: %s", source_path.name)
        else:
            raise ValueError("No valid video or image source provided for Short.")

        # Build filter chain: Letterbox 16:9 into 9:16 (No zoom allowed)
        # force_original_aspect_ratio=decrease + pad ensures letterboxing
        filters: list[str] = []
        filters.append(
            f"[0:v]scale={w}:{h}:force_original_aspect_ratio=decrease,"
            f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:black,"
            f"setpts=PTS-STARTPTS[padded]"
        )

        # Fade in only (no fade out to encourage loops)
        filters.append(f"[padded]fade=t=in:st=0:d=0.8[final]")
        
        filter_complex = ";".join(filters)

        cmd = ["ffmpeg", "-y"]
        if not is_video:
            cmd.extend(["-loop", "1"])
        
        # Seek source and audio
        cmd.extend([
            "-ss", f"{start_time:.3f}",
            "-t", f"{segment_dur:.3f}",
            "-i", str(source_path),
        ])
        
        # If using video source, it already has audio, but we map it explicitly or use audio_path
        # User requested "song with the exported video clip", so we use audio_path for best quality
        cmd.extend([
            "-ss", f"{start_time:.3f}",
            "-t", f"{segment_dur:.3f}",
            "-i", str(audio_path),
        ])

        cmd.extend([
            "-filter_complex", filter_complex,
            "-map", "[final]",
            "-map", "1:a",  # Use the high-quality normalized audio
            "-c:v", "libx264",
            "-preset", "slow", # Better quality for Shorts
            "-crf", "18",      # High quality
            "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            "-b:a", "192k",
            "-movflags", "+faststart",
            str(output_path),
        ])

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
            if result.returncode != 0:
                err = result.stderr[-500:] if result.stderr else "Unknown error"
                logger.error("Short render failed: %s", err)
                raise RuntimeError(f"Short render failed: {err}")
        except subprocess.TimeoutExpired:
            raise RuntimeError("Short render timed out")

        if not output_path.exists() or output_path.stat().st_size < 1024:
            raise RuntimeError(f"Short output missing: {output_path}")

        size_mb = output_path.stat().st_size / (1024 * 1024)
        logger.info("✅ Short rendered: %s (%.1f MB)", output_path.name, size_mb)
        return output_path

    @staticmethod
    def _get_duration(path: Path) -> float:
        try:
            cmd = [
                "ffprobe", "-v", "quiet",
                "-show_entries", "format=duration",
                "-of", "csv=p=0", str(path),
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=15, check=True)
            return float(result.stdout.strip())
        except Exception:
            return 0.0

    @staticmethod
    def _ensure_font() -> Path:
        """Reuse the same font logic as VideoEditor."""
        fonts_dir = TEMPLATES_DIR / "fonts"
        fonts_dir.mkdir(parents=True, exist_ok=True)
        font_path = fonts_dir / "Roboto-Regular.ttf"
        if font_path.exists() and font_path.stat().st_size > 50_000:
            return font_path

        for sys_font in [
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
            Path("/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf"),
            Path("/usr/share/fonts/TTF/DejaVuSans.ttf"),
            Path("C:/Windows/Fonts/arial.ttf"),
        ]:
            if sys_font.exists():
                return sys_font

        return font_path  # Will use default if missing

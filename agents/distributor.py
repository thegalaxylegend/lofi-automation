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
        background_path: str | Path,
        brief: CreativeBrief,
        shorts_text: str = "",
        *,
        output_name: str | None = None,
    ) -> Path:
        audio_path = Path(audio_path)
        background_path = Path(background_path)
        sc = self.config.channel.shorts

        if output_name is None:
            output_name = f"{audio_path.stem}_short.mp4"
        output_path = OUTPUT_DIR / output_name

        w, h = sc.width, sc.height
        max_dur = sc.max_duration_sec

        # Get audio duration
        duration = self._get_duration(audio_path)
        if duration <= 0:
            raise ValueError(f"Cannot get duration for {audio_path}")

        # AI-selected segment from Director's brief
        if brief.has_sections and brief.shorts.duration_sec > 0:
            start_time = brief.shorts.recommended_start_sec
            segment_dur = min(brief.shorts.duration_sec, max_dur)
            hook_text = brief.shorts.hook_text
            mood_text = brief.shorts.mood_text
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
            hook_text = ""
            mood_text = shorts_text or brief.text_overlay_suggestion or ""

        # Ensure segment doesn't exceed audio
        if start_time + segment_dur > duration:
            start_time = max(0, duration - segment_dur)

        logger.info(
            "Distributor: Short from %.1fs-%.1fs (%.1fs)",
            start_time, start_time + segment_dur, segment_dur,
        )

        # Get font
        font_path = self._ensure_font()
        safe_font = str(font_path.absolute()).replace("\\", "/").replace(":", "\\:")

        # If drawtext not available, burn text into the background image
        if not self._has_drawtext and (hook_text or mood_text):
            texts = []
            if hook_text:
                texts.append((hook_text, "hook", 32, 0.95))
            if mood_text:
                texts.append((mood_text, "mood", 36, 0.9))
            background_path = _burn_text_on_image_for_short(
                background_path, texts, font_path, w, h,
            )

        # Build filter chain
        filters: list[str] = []

        # Scale to fit width, pad with black bars on top/bottom
        filters.append(
            f"[0:v]scale={w}:{h}:force_original_aspect_ratio=decrease,"
            f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:black,"
            f"setpts=PTS-STARTPTS[padded]"
        )

        last_label = "padded"

        # Fade in (but NO fade out — encourages replay)
        filters.append(f"[{last_label}]fade=t=in:st=0:d=0.5[faded]")
        last_label = "faded"

        # Hook text and mood text only if drawtext is available
        if self._has_drawtext:
            if hook_text:
                hook_file = TEMP_DIR / f"{audio_path.stem}_hook.txt"
                hook_file.write_text(hook_text, encoding="utf-8")
                safe_hook = str(hook_file.absolute()).replace("\\", "/").replace(":", "\\:")
                filters.append(
                    f"[{last_label}]drawtext="
                    f"textfile='{safe_hook}':"
                    f"fontsize=42:fontcolor=white:"
                    f"box=1:boxcolor=black@0.7:boxborderw=20:"
                    f"x=(w-tw)/2:y=h*0.25:"
                    f"fontfile='{safe_font}':"
                    f"borderw=3:bordercolor=black:"
                    f"enable='between(t,0.3,4.0)'[hooked]"
                )
                last_label = "hooked"

            if mood_text:
                mood_file = TEMP_DIR / f"{audio_path.stem}_mood.txt"
                mood_file.write_text(mood_text, encoding="utf-8")
                safe_mood = str(mood_file.absolute()).replace("\\", "/").replace(":", "\\:")
                mid_start = segment_dur * 0.35
                mid_end = segment_dur * 0.70
                filters.append(
                    f"[{last_label}]drawtext="
                    f"textfile='{safe_mood}':"
                    f"fontsize=46:fontcolor=white:"
                    f"box=1:boxcolor=black@0.7:boxborderw=20:"
                    f"x=(w-tw)/2:y=h*0.32:"
                    f"fontfile='{safe_font}':"
                    f"borderw=3:bordercolor=black:"
                    f"enable='between(t,{mid_start:.1f},{mid_end:.1f})'[final]"
                )
                last_label = "final"

        filter_complex = ";".join(filters)

        cmd = [
            "ffmpeg", "-y",
            "-loop", "1",
            "-i", str(background_path),
            "-ss", str(start_time),
            "-i", str(audio_path),
            "-filter_complex", filter_complex,
            "-map", f"[{last_label}]",
            "-map", "1:a",
            "-c:v", "libx264",
            "-preset", "medium",
            "-crf", "20",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            "-b:a", "192k",
            "-t", str(segment_dur),
            "-movflags", "+faststart",
            str(output_path),
        ]

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
            if result.returncode != 0:
                logger.error("Short render failed: %s", result.stderr[-500:])
                raise RuntimeError("Short render failed")
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

"""
Agent 10: The Distributor — AI-Directed YouTube Shorts Factory.

Uses the Director's creative brief to create intelligent Shorts:
  1. AI-selected best segment (not blind middle 40 seconds)
  2. Vertical crop with slow pan for visual movement
  3. Hook text in first 1.5 seconds (POV style)
  4. Emotional mood text in the middle
  5. No fade out (encourages replay/loop)
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from agents.director import CreativeBrief
from core.config import Config, OUTPUT_DIR, TEMP_DIR, TEMPLATES_DIR

logger = logging.getLogger(__name__)


class Distributor:
    """Creates AI-directed YouTube Shorts from the Director's creative brief."""

    def __init__(self) -> None:
        self.config = Config()

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

        # Build filter chain
        filters: list[str] = []

        # Scale + vertical center crop with slow zoom for movement
        zoom_frames = int(segment_dur * 30)
        filters.append(
            f"[0:v]scale=-2:{h}:force_original_aspect_ratio=increase,"
            f"crop={w}:{h}:(iw-{w})/2:0,"
            f"zoompan=z='zoom+0.0001':d={zoom_frames}:s={w}x{h}:fps=30,"
            f"setpts=PTS-STARTPTS[cropped]"
        )

        last_label = "cropped"

        # Fade in (but NO fade out — encourages replay)
        filters.append(f"[{last_label}]fade=t=in:st=0:d=0.5[faded]")
        last_label = "faded"

        # Hook text (first 1.5 seconds — stops scrollers)
        if hook_text:
            hook_file = TEMP_DIR / f"{audio_path.stem}_hook.txt"
            hook_file.write_text(hook_text, encoding="utf-8")
            safe_hook = str(hook_file.absolute()).replace("\\", "/").replace(":", "\\:")
            filters.append(
                f"[{last_label}]drawtext="
                f"textfile='{safe_hook}':"
                f"fontsize=32:fontcolor=white@0.95:"
                f"x=(w-tw)/2:y=h*0.35:"
                f"fontfile='{safe_font}':"
                f"borderw=2:bordercolor=black@0.6:"
                f"enable='between(t,0.3,4.0)'[hooked]"
            )
            last_label = "hooked"

        # Mood text (middle of the short — emotional punch)
        if mood_text:
            mood_file = TEMP_DIR / f"{audio_path.stem}_mood.txt"
            mood_file.write_text(mood_text, encoding="utf-8")
            safe_mood = str(mood_file.absolute()).replace("\\", "/").replace(":", "\\:")
            mid_start = segment_dur * 0.35
            mid_end = segment_dur * 0.70
            filters.append(
                f"[{last_label}]drawtext="
                f"textfile='{safe_mood}':"
                f"fontsize=36:fontcolor=white@0.9:"
                f"x=(w-tw)/2:y=h*0.42:"
                f"fontfile='{safe_font}':"
                f"borderw=2:bordercolor=black@0.5:"
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
            Path("C:/Windows/Fonts/arial.ttf"),
        ]:
            if sys_font.exists():
                return sys_font

        return font_path  # Will use default if missing

"""
Agent 10: The Distributor — YouTube Shorts Factory.

Takes a finished long-form video and creates a vertical Short:
  1. Extracts the most energetic 30-50 second segment
  2. Crops to 9:16 vertical (1080x1920)
  3. Adds floating POV/relatable text overlay
  4. Renders via FFmpeg
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from agents.director import CreativeBrief
from core.config import Config, OUTPUT_DIR

logger = logging.getLogger(__name__)


class Distributor:
    """
    Creates YouTube Shorts from long-form videos.
    Extracts the best segment and reformats for vertical.
    """

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
        """
        Create a vertical YouTube Short.

        Args:
            audio_path: Original MP3 file.
            background_path: Background video.
            brief: CreativeBrief from the Director.
            shorts_text: POV text to overlay.
            output_name: Optional custom filename.

        Returns:
            Path to the rendered Short MP4.
        """
        audio_path = Path(audio_path)
        background_path = Path(background_path)
        sc = self.config.channel.shorts
        colors = self.config.channel.brand_colors

        if output_name is None:
            stem = audio_path.stem
            output_name = f"{stem}_short.mp4"
        output_path = OUTPUT_DIR / output_name

        max_dur = sc.max_duration_sec
        w, h = sc.width, sc.height

        # Get audio duration
        duration = self._get_duration(audio_path)
        if duration <= 0:
            raise ValueError(f"Cannot get duration for {audio_path}")

        # Pick the segment: use the middle portion for best energy
        if duration > max_dur:
            start_time = max(0, (duration - max_dur) / 2)
            segment_dur = max_dur
        else:
            start_time = 0
            segment_dur = duration

        logger.info(
            "Distributor: creating Short from %.1fs-%.1fs (%.1fs total)",
            start_time, start_time + segment_dur, segment_dur,
        )

        # Prepare text overlay
        if not shorts_text:
            shorts_text = brief.text_overlay_suggestion or ""
        safe_text = shorts_text.replace("'", "\\'").replace(":", "\\:")

        # Build FFmpeg filter for vertical crop + text
        filters: list[str] = []

        # Crop to vertical center
        filters.append(
            f"[0:v]scale=-2:{h},crop={w}:{h}:(iw-{w})/2:0[cropped]"
        )

        # Add text overlay if provided
        if safe_text:
            filters.append(
                f"[cropped]drawtext="
                f"text='{safe_text}':"
                f"fontsize=36:fontcolor=white@0.9:"
                f"x=(w-tw)/2:y=h*0.42:"
                f"font='sans-serif':"
                f"borderw=2:bordercolor=black@0.5[final]"
            )
            last_label = "final"
        else:
            last_label = "cropped"

        filter_complex = ";".join(filters)

        cmd = [
            "ffmpeg", "-y",
            "-ss", str(start_time),
            "-stream_loop", "-1",
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
            raise RuntimeError(f"Short output missing or corrupted: {output_path}")

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

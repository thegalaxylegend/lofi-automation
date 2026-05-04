"""
Agent 2: The Video Editor — FFmpeg Assembly Engine.

Takes the Director's CreativeBrief + a background video + the MP3 audio
and renders a production-quality video with:
  - Background looped/trimmed to match audio duration
  - Audio-reactive visualizer (FFmpeg showcqt)
  - Dynamic color grading based on mood palette
  - Film grain / VHS texture overlay
  - Text overlay (channel name + mood line)
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from pathlib import Path

from agents.director import CreativeBrief
from core.config import Config, OUTPUT_DIR, TEMP_DIR
from core.memory import editor_memory

logger = logging.getLogger(__name__)


def _get_duration(file_path: str | Path) -> float:
    """Get the duration of an audio or video file in seconds using ffprobe."""
    cmd = [
        "ffprobe", "-v", "quiet",
        "-show_entries", "format=duration",
        "-of", "csv=p=0",
        str(file_path),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30, check=True)
        return float(result.stdout.strip())
    except (subprocess.SubprocessError, ValueError) as exc:
        logger.error("ffprobe failed for %s: %s", file_path, exc)
        return 0.0


def _hex_to_ffmpeg_color(hex_color: str) -> str:
    """Convert #RRGGBB to FFmpeg's 0xRRGGBB format."""
    return "0x" + hex_color.lstrip("#")


class VideoEditor:
    """
    Renders production-quality lo-fi videos using FFmpeg.

    This agent constructs complex FFmpeg filter graphs dynamically
    based on the Director's CreativeBrief.
    """

    def __init__(self) -> None:
        self.config = Config()
        self.memory = editor_memory()

        # Verify FFmpeg is installed
        if not shutil.which("ffmpeg"):
            raise EnvironmentError(
                "FFmpeg not found. Install it: https://ffmpeg.org/download.html"
            )
        if not shutil.which("ffprobe"):
            raise EnvironmentError("ffprobe not found. It ships with FFmpeg.")

    def render(
        self,
        audio_path: str | Path,
        background_path: str | Path,
        brief: CreativeBrief,
        *,
        output_name: str | None = None,
    ) -> Path:
        """
        Render the final video.

        Args:
            audio_path: Path to the MP3 file.
            background_path: Path to the background video.
            brief: CreativeBrief from the Director.
            output_name: Optional custom output filename.

        Returns:
            Path to the rendered MP4 file in the output/ directory.
        """
        audio_path = Path(audio_path)
        background_path = Path(background_path)

        if not audio_path.exists():
            raise FileNotFoundError(f"Audio not found: {audio_path}")
        if not background_path.exists():
            raise FileNotFoundError(f"Background not found: {background_path}")

        # Determine durations
        audio_duration = _get_duration(audio_path)
        if audio_duration <= 0:
            raise ValueError(f"Could not determine audio duration for {audio_path}")

        video_duration = _get_duration(background_path)
        if video_duration <= 0:
            raise ValueError(f"Could not determine video duration for {background_path}")

        logger.info(
            "Rendering: audio=%.1fs, bg_video=%.1fs, mood=%s",
            audio_duration, video_duration, brief.mood,
        )

        # Get video settings from brand config
        vs = self.config.channel.video
        channel_name = self.config.channel.name

        # Build output path
        if output_name is None:
            stem = audio_path.stem
            output_name = f"{stem}_final.mp4"
        output_path = OUTPUT_DIR / output_name

        # Build the FFmpeg command
        cmd = self._build_ffmpeg_command(
            audio_path=audio_path,
            background_path=background_path,
            output_path=output_path,
            audio_duration=audio_duration,
            video_duration=video_duration,
            brief=brief,
            resolution=vs.resolution,
            fps=vs.fps,
            crf=vs.crf,
            codec=vs.codec,
            preset=vs.preset,
            pixel_format=vs.pixel_format,
            audio_bitrate=vs.audio_bitrate,
            channel_name=channel_name,
        )

        # Execute
        logger.info("FFmpeg command: %s", " ".join(cmd[:10]) + " ...")
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=600,  # 10 minute max for short videos
            )
            if result.returncode != 0:
                error_msg = result.stderr[-2000:] if result.stderr else "Unknown error"
                logger.error("FFmpeg render failed:\n%s", error_msg)
                self._log_crash(brief, error_msg)
                raise RuntimeError(f"FFmpeg render failed: {error_msg}")

        except subprocess.TimeoutExpired:
            logger.error("FFmpeg render timed out after 600s.")
            self._log_crash(brief, "Timeout after 600s")
            raise

        # Verify output
        if not output_path.exists() or output_path.stat().st_size < 1024:
            raise RuntimeError(f"Output file missing or too small: {output_path}")

        size_mb = output_path.stat().st_size / (1024 * 1024)
        logger.info("✅ Render complete: %s (%.1f MB)", output_path.name, size_mb)
        return output_path

    def _build_ffmpeg_command(
        self,
        *,
        audio_path: Path,
        background_path: Path,
        output_path: Path,
        audio_duration: float,
        video_duration: float,
        brief: CreativeBrief,
        resolution: str,
        fps: int,
        crf: int,
        codec: str,
        preset: str,
        pixel_format: str,
        audio_bitrate: str,
        channel_name: str,
    ) -> list[str]:
        """Construct the FFmpeg command with dynamic filter graph."""

        width, height = resolution.split("x")
        w, h = int(width), int(height)

        # ── Color grading values based on mood ──
        color_params = self._mood_color_grade(brief)

        # ── Build filter graph ──
        # Step 1: Loop background video to match audio duration
        # Step 2: Scale to target resolution
        # Step 3: Apply color grading
        # Step 4: Add film grain
        # Step 5: Add text overlay (channel name + mood text)

        # Determine loop count
        loops = max(1, int(audio_duration / video_duration) + 1)

        filters: list[str] = []

        # Input 0: background video (looped)
        # Input 1: audio file
        # Input 2: audio (for visualizer)

        # Loop and trim the background
        filters.append(
            f"[0:v]loop={loops}:{int(video_duration * fps)}:0,"
            f"setpts=N/FRAME_RATE/TB,"
            f"trim=duration={audio_duration},"
            f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
            f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black,"
            f"fps={fps}[bg]"
        )

        # Color grading using curves and eq
        filters.append(
            f"[bg]eq=brightness={color_params['brightness']}:"
            f"contrast={color_params['contrast']}:"
            f"saturation={color_params['saturation']},"
            f"hue=s=0[graded]"
        )

        # Film grain using noise filter
        grain_amount = 15 if brief.mood in ("nostalgic", "melancholic", "dark") else 8
        filters.append(
            f"[graded]noise=c0s={grain_amount}:c0f=t+u:allf=t+u[grained]"
        )

        # Audio visualizer (showcqt — musical frequency display)
        viz_h = self._visualizer_height(brief, h)
        primary_color = brief.color_palette[0] if brief.color_palette else "#6C3CE1"
        accent_color = brief.color_palette[-1] if len(brief.color_palette) > 1 else "#FF6B9D"

        filters.append(
            f"[1:a]showcqt=s={w}x{viz_h}:"
            f"count=6:fcount=2:"
            f"sono_h=0:bar_h=1:"
            f"sono_g=4:bar_g=2:"
            f"font='sans':"
            f"fontcolor='{_hex_to_ffmpeg_color(primary_color)}':"
            f"tc=0.33:tlength=2[viz]"
        )

        # Make the visualizer semi-transparent and overlay at bottom
        viz_y = h - viz_h - 20
        filters.append(
            f"[viz]format=rgba,"
            f"colorchannelmixer=aa=0.6[vizt]"
        )
        filters.append(
            f"[grained][vizt]overlay=0:{viz_y}:shortest=1[withviz]"
        )

        # Text overlay: channel name (bottom right)
        safe_channel = channel_name.replace("'", "\\'")
        filters.append(
            f"[withviz]drawtext="
            f"text='{safe_channel}':"
            f"fontsize=24:fontcolor=white@0.7:"
            f"x=w-tw-30:y=h-th-30:"
            f"font='sans-serif'[withtext]"
        )

        # Text overlay: mood line (center, fades in/out)
        if brief.text_overlay_suggestion:
            safe_text = brief.text_overlay_suggestion.replace("'", "\\'").replace(":", "\\:")
            filters.append(
                f"[withtext]drawtext="
                f"text='{safe_text}':"
                f"fontsize=28:fontcolor=white@0.8:"
                f"x=(w-tw)/2:y=h*0.15:"
                f"font='sans-serif':"
                f"enable='between(t,3,{audio_duration - 2})'[final]"
            )
            last_label = "final"
        else:
            last_label = "withtext"

        filter_complex = ";".join(filters)

        cmd = [
            "ffmpeg", "-y",
            "-stream_loop", "-1",
            "-i", str(background_path),  # Input 0: video
            "-i", str(audio_path),       # Input 1: audio
            "-filter_complex", filter_complex,
            "-map", f"[{last_label}]",
            "-map", "1:a",
            "-c:v", codec,
            "-preset", preset,
            "-crf", str(crf),
            "-pix_fmt", pixel_format,
            "-c:a", "aac",
            "-b:a", audio_bitrate,
            "-t", str(audio_duration),
            "-movflags", "+faststart",
            str(output_path),
        ]

        return cmd

    @staticmethod
    def _mood_color_grade(brief: CreativeBrief) -> dict[str, float]:
        """Return FFmpeg eq filter values based on mood."""
        grades = {
            "melancholic": {"brightness": -0.05, "contrast": 1.1, "saturation": 0.7},
            "energetic":   {"brightness": 0.02,  "contrast": 1.2, "saturation": 1.3},
            "peaceful":    {"brightness": 0.0,   "contrast": 1.0, "saturation": 0.9},
            "nostalgic":   {"brightness": -0.03, "contrast": 1.05, "saturation": 0.6},
            "dark":        {"brightness": -0.08, "contrast": 1.15, "saturation": 0.5},
            "dreamy":      {"brightness": 0.03,  "contrast": 0.95, "saturation": 1.1},
            "romantic":    {"brightness": 0.01,  "contrast": 1.05, "saturation": 1.2},
            "anxious":     {"brightness": -0.04, "contrast": 1.2, "saturation": 0.8},
        }
        return grades.get(brief.mood, grades["peaceful"])

    @staticmethod
    def _visualizer_height(brief: CreativeBrief, video_height: int) -> int:
        """Determine visualizer bar height based on intensity setting."""
        ratios = {
            "subtle": 0.08,
            "moderate": 0.12,
            "intense": 0.18,
        }
        ratio = ratios.get(brief.visualizer_intensity, 0.08)
        return max(60, int(video_height * ratio))

    def _log_crash(self, brief: CreativeBrief, error: str) -> None:
        """Log a crash to editor memory for future avoidance."""
        self.memory.append_to_list("crash_log", {
            "mood": brief.mood,
            "visualizer_intensity": brief.visualizer_intensity,
            "error_snippet": error[:300],
        })

"""
Agent 2: The Video Editor — Data-Driven Parallax Engine.

Takes a list of AI-generated images, the MP3 audio, and the mathematical
analysis of the audio (BPM, beats, energy) to render a production-quality video.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

from agents.director import CreativeBrief
from core.config import Config, OUTPUT_DIR

logger = logging.getLogger(__name__)

def _get_duration(file_path: str | Path) -> float:
    cmd = [
        "ffprobe", "-v", "quiet",
        "-show_entries", "format=duration",
        "-of", "csv=p=0",
        str(file_path),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30, check=True)
        return float(result.stdout.strip())
    except (subprocess.SubprocessError, ValueError):
        return 0.0

def _hex_to_ffmpeg_color(hex_color: str) -> str:
    return "0x" + hex_color.lstrip("#")

class VideoEditor:
    """
    Renders production-quality lo-fi videos using FFmpeg Parallax Engine.
    """

    def __init__(self) -> None:
        self.config = Config()
        if not shutil.which("ffmpeg"):
            raise EnvironmentError("FFmpeg not found.")

    def render(
        self,
        audio_path: str | Path,
        image_paths: list[Path],
        brief: CreativeBrief,
        audio_math: dict,
        *,
        output_name: str | None = None,
    ) -> Path:
        
        audio_path = Path(audio_path)
        if not audio_path.exists():
            raise FileNotFoundError(f"Audio not found: {audio_path}")
        if not image_paths:
            raise ValueError("No images provided for the slideshow.")

        audio_duration = _get_duration(audio_path)
        if audio_duration <= 0:
            raise ValueError(f"Could not determine audio duration for {audio_path}")

        logger.info(
            f"Rendering Parallax Engine: {len(image_paths)} images, audio={audio_duration:.1f}s, "
            f"BPM={audio_math.get('bpm', 0):.1f}, Energy={audio_math.get('energy', 0):.4f}"
        )

        vs = self.config.channel.video
        channel_name = self.config.channel.name

        if output_name is None:
            stem = audio_path.stem
            output_name = f"{stem}_final.mp4"
        output_path = OUTPUT_DIR / output_name

        cmd = self._build_ffmpeg_command(
            audio_path=audio_path,
            image_paths=image_paths,
            output_path=output_path,
            audio_duration=audio_duration,
            brief=brief,
            audio_math=audio_math,
            resolution=vs.resolution,
            fps=vs.fps,
            crf=vs.crf,
            codec=vs.codec,
            preset=vs.preset,
            pixel_format=vs.pixel_format,
            audio_bitrate=vs.audio_bitrate,
            channel_name=channel_name,
        )

        logger.info("FFmpeg command executing...")
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=1200)
            if result.returncode != 0:
                error_msg = result.stderr[-2000:] if result.stderr else "Unknown error"
                logger.error(f"FFmpeg render failed:\n{error_msg}")
                raise RuntimeError(f"FFmpeg render failed: {error_msg}")
        except subprocess.TimeoutExpired:
            logger.error("FFmpeg render timed out after 1200s.")
            raise

        if not output_path.exists() or output_path.stat().st_size < 1024:
            raise RuntimeError(f"Output file missing or too small: {output_path}")

        logger.info(f"✅ Render complete: {output_path.name}")
        return output_path

    def _build_ffmpeg_command(
        self,
        *,
        audio_path: Path,
        image_paths: list[Path],
        output_path: Path,
        audio_duration: float,
        brief: CreativeBrief,
        audio_math: dict,
        resolution: str,
        fps: int,
        crf: int,
        codec: str,
        preset: str,
        pixel_format: str,
        audio_bitrate: str,
        channel_name: str,
    ) -> list[str]:
        
        width, height = resolution.split("x")
        w, h = int(width), int(height)
        
        bpm = audio_math.get("bpm", 90)
        energy = audio_math.get("energy", 0.1)

        color_params = self._mood_color_grade(brief)
        
        # Calculate timing per image based on beat structure
        num_images = len(image_paths)
        duration_per_img = audio_duration / num_images
        frames_per_img = max(1, int(duration_per_img * fps))

        filters = []
        concat_inputs = ""
        
        # 1. Build Parallax Slideshow
        for i in range(num_images):
            # Dynamic zoom speed tied to BPM
            zoom_speed = 0.0005 * (bpm / 80.0) 
            zoom_max = 1.0 + (bpm / 500.0)
            
            # Use simple zoom addition to avoid min() comma parsing errors in FFmpeg
            z_expr = f"zoom+{zoom_speed}"
                
            filters.append(
                f"[{i}:v]scale={w}:{h}:force_original_aspect_ratio=increase,"
                f"crop={w}:{h},"
                f"zoompan=z='{z_expr}':d={frames_per_img}:s={w}x{h}:fps={fps}[v{i}]"
            )
            concat_inputs += f"[v{i}]"

        if num_images == 1:
            filters.append(f"[v0]copy[slideshow]")
        else:
            filters.append(f"{concat_inputs}concat=n={num_images}:v=1:a=0[slideshow]")

        # 2. Color grading
        filters.append(
            f"[slideshow]eq=brightness={color_params['brightness']}:"
            f"contrast={color_params['contrast']}:"
            f"saturation={color_params['saturation']},"
            f"hue=s=0[graded]"
        )

        # 3. Dynamic Film Grain (Intensity tied to Audio Energy)
        base_grain = 8
        energy_spike = int(energy * 100) # e.g. 0.1 -> 10
        grain_amount = min(24, base_grain + energy_spike)
        
        filters.append(
            f"[graded]noise=c0s={grain_amount}:c0f=t+u:allf=t+u[grained]"
        )

        # 4. Audio Visualizer
        viz_h = self._visualizer_height(brief, h)
        primary_color = brief.color_palette[0] if brief.color_palette else "#6C3CE1"
        
        # Audio is at input index len(image_paths)
        audio_idx = len(image_paths)
        
        filters.append(
            f"[{audio_idx}:a]showcqt=s={w}x{viz_h}:"
            f"count=6:fcount=2:sono_h=0:bar_h=1:sono_g=4:bar_g=2:"
            f"font='sans':fontcolor='{_hex_to_ffmpeg_color(primary_color)}':tc=0.33:tlength=2[viz]"
        )

        viz_y = h - viz_h - 20
        filters.append(
            f"[viz]format=rgba,colorchannelmixer=aa=0.6[vizt]"
        )
        filters.append(
            f"[grained][vizt]overlay=0:{viz_y}:shortest=1[withviz]"
        )

        # 5. Text Overlay
        # Replace apostrophes with smart quotes to prevent breaking FFmpeg's single-quote parser
        safe_channel = channel_name.replace("'", "’").replace(":", "\\:").replace(",", "\\,")
        filters.append(
            f"[withviz]drawtext="
            f"text='{safe_channel}':"
            f"fontsize=24:fontcolor=white@0.7:"
            f"x=w-tw-30:y=h-th-30:"
            f"font='sans-serif'[withtext]"
        )

        if brief.text_overlay_suggestion:
            safe_text = brief.text_overlay_suggestion.replace("'", "’").replace(":", "\\:").replace(",", "\\,")
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

        cmd = ["ffmpeg", "-y"]
        # Add all images as inputs
        for img in image_paths:
            # We loop the image so zoompan has enough frames
            cmd.extend(["-loop", "1", "-t", str(duration_per_img), "-i", str(img)])
            
        # Add audio as input
        cmd.extend(["-i", str(audio_path)])

        cmd.extend([
            "-filter_complex", filter_complex,
            "-map", f"[{last_label}]",
            "-map", f"{audio_idx}:a",
            "-c:v", codec,
            "-preset", preset,
            "-crf", str(crf),
            "-pix_fmt", pixel_format,
            "-c:a", "aac",
            "-b:a", audio_bitrate,
            "-t", str(audio_duration),
            "-movflags", "+faststart",
            str(output_path),
        ])

        return cmd

    @staticmethod
    def _mood_color_grade(brief: CreativeBrief) -> dict[str, float]:
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
        ratios = {"subtle": 0.08, "moderate": 0.12, "intense": 0.18}
        ratio = ratios.get(brief.visualizer_intensity, 0.08)
        raw_h = max(60, int(video_height * ratio))
        return raw_h if raw_h % 2 == 0 else raw_h + 1


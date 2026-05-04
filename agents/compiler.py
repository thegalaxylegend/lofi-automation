"""
Agent 7: The Compiler — Weekly Compilation Mix Builder.

Every week, takes all individual songs and stitches them into a single
30-60 minute "Study Session" mix with:
  - Crossfade transitions between songs
  - Chapter markers in the description
  - A unified thumbnail
  - Maximum watch-time generation for monetization
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from core.config import Config, OUTPUT_DIR, TEMP_DIR
from core.memory import Memory

logger = logging.getLogger(__name__)


@dataclass
class ChapterMarker:
    """A chapter marker for the YouTube description."""
    timestamp: str  # "00:00", "03:24", etc.
    title: str

    def to_line(self) -> str:
        return f"{self.timestamp} {self.title}"


@dataclass
class CompilationResult:
    """Result of a compilation build."""
    output_path: Path | None = None
    chapters: list[ChapterMarker] = field(default_factory=list)
    total_duration_sec: float = 0.0
    track_count: int = 0
    success: bool = False

    def chapters_text(self) -> str:
        """Generate YouTube-compatible chapter text for the description."""
        return "\n".join(ch.to_line() for ch in self.chapters)


class Compiler:
    """
    Stitches multiple audio tracks into a single long-form compilation
    video for maximum YouTube watch time.
    """

    def __init__(self) -> None:
        self.config = Config()
        self.memory = Memory("compiler_history")

    def compile(
        self,
        audio_files: list[Path],
        background_path: Path,
        *,
        compilation_title: str = "Weekly Study Mix",
        crossfade_sec: float = 3.0,
        output_name: str | None = None,
    ) -> CompilationResult:
        """
        Merge multiple MP3 files into one long video.

        Args:
            audio_files: List of MP3 file paths (in order).
            background_path: Background video to loop.
            compilation_title: Title prefix for the mix.
            crossfade_sec: Crossfade duration between tracks.
            output_name: Custom output filename.

        Returns:
            CompilationResult with the output path and chapter markers.
        """
        result = CompilationResult()

        if len(audio_files) < 2:
            logger.warning("Compiler needs at least 2 tracks. Got %d.", len(audio_files))
            return result

        logger.info("Compiler: merging %d tracks into compilation...", len(audio_files))

        # Step 1: Get durations and build chapter markers
        durations: list[float] = []
        chapters: list[ChapterMarker] = []
        running_time = 0.0

        for i, af in enumerate(audio_files):
            dur = self._get_duration(af)
            if dur <= 0:
                logger.warning("Skipping %s (cannot read duration).", af.name)
                continue
            durations.append(dur)

            # Build chapter marker
            mins = int(running_time // 60)
            secs = int(running_time % 60)
            timestamp = f"{mins:02d}:{secs:02d}"
            track_title = af.stem.replace("_", " ").replace("-", " ").title()
            chapters.append(ChapterMarker(timestamp=timestamp, title=track_title))

            # Account for crossfade overlap (except last track)
            if i < len(audio_files) - 1:
                running_time += dur - crossfade_sec
            else:
                running_time += dur

        if len(durations) < 2:
            logger.error("Not enough valid tracks for compilation.")
            return result

        result.chapters = chapters
        result.track_count = len(durations)
        result.total_duration_sec = running_time

        # Step 2: Concatenate audio with crossfade using FFmpeg
        merged_audio = TEMP_DIR / "compilation_audio.mp3"
        if not self._merge_audio(audio_files, merged_audio, crossfade_sec):
            return result

        # Step 3: Render video with the merged audio
        if output_name is None:
            date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
            output_name = f"compilation_{date_str}.mp4"
        output_path = OUTPUT_DIR / output_name

        if not self._render_video(merged_audio, background_path, output_path, running_time):
            return result

        result.output_path = output_path
        result.success = True

        # Log to memory
        self.memory.append_to_list("compilations", {
            "date": datetime.now(timezone.utc).isoformat(),
            "tracks": result.track_count,
            "duration_min": round(result.total_duration_sec / 60, 1),
        })

        logger.info(
            "✅ Compilation complete: %d tracks, %.1f min → %s",
            result.track_count,
            result.total_duration_sec / 60,
            output_path.name,
        )
        return result

    def _merge_audio(
        self, files: list[Path], output: Path, crossfade: float
    ) -> bool:
        """Merge multiple MP3 files with crossfade transitions."""
        if len(files) == 0:
            return False

        # Build FFmpeg concat with crossfade filter
        inputs: list[str] = []
        for f in files:
            inputs.extend(["-i", str(f)])

        # For 2+ files, chain acrossfade filters
        # [0][1] -> acrossfade -> [01]
        # [01][2] -> acrossfade -> [012]
        # etc.
        filter_parts: list[str] = []
        n = len(files)

        if n == 1:
            # Single file, just copy
            cmd = ["ffmpeg", "-y", "-i", str(files[0]), "-c:a", "libmp3lame", str(output)]
        else:
            # Build crossfade chain
            prev_label = "[0:a]"
            for i in range(1, n):
                curr_label = f"[{i}:a]"
                out_label = f"[cf{i}]" if i < n - 1 else "[out]"
                filter_parts.append(
                    f"{prev_label}{curr_label}acrossfade=d={crossfade}:c1=tri:c2=tri{out_label}"
                )
                prev_label = out_label

            filter_complex = ";".join(filter_parts)
            cmd = [
                "ffmpeg", "-y",
                *inputs,
                "-filter_complex", filter_complex,
                "-map", "[out]",
                "-c:a", "libmp3lame", "-q:a", "2",
                str(output),
            ]

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
            if result.returncode != 0:
                logger.error("Audio merge failed: %s", result.stderr[-500:])
                return False
            return output.exists()
        except subprocess.TimeoutExpired:
            logger.error("Audio merge timed out.")
            return False

    def _render_video(
        self, audio: Path, background: Path, output: Path, duration: float
    ) -> bool:
        """Render the compilation video with looped background."""
        vs = self.config.channel.video
        channel_name = self.config.channel.name
        w, h = vs.width, vs.height

        safe_name = channel_name.replace("'", "\\'")

        filter_complex = (
            f"[0:v]scale={w}:{h}:force_original_aspect_ratio=decrease,"
            f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black,"
            f"fps={vs.fps},"
            f"eq=brightness=-0.03:contrast=1.05:saturation=0.8,"
            f"noise=c0s=10:c0f=t+u:allf=t+u,"
            f"drawtext=text='{safe_name}':"
            f"fontsize=28:fontcolor=white@0.6:"
            f"x=w-tw-30:y=h-th-30:font='sans-serif'[v]"
        )

        cmd = [
            "ffmpeg", "-y",
            "-stream_loop", "-1",
            "-i", str(background),
            "-i", str(audio),
            "-filter_complex", filter_complex,
            "-map", "[v]",
            "-map", "1:a",
            "-c:v", vs.codec,
            "-preset", vs.preset,
            "-crf", str(vs.crf),
            "-pix_fmt", vs.pixel_format,
            "-c:a", "aac",
            "-b:a", vs.audio_bitrate,
            "-t", str(duration),
            "-movflags", "+faststart",
            str(output),
        ]

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
            if result.returncode != 0:
                logger.error("Compilation render failed: %s", result.stderr[-500:])
                return False
            return output.exists() and output.stat().st_size > 1024
        except subprocess.TimeoutExpired:
            logger.error("Compilation render timed out (30 min).")
            return False

    @staticmethod
    def _get_duration(path: Path) -> float:
        try:
            cmd = [
                "ffprobe", "-v", "quiet",
                "-show_entries", "format=duration",
                "-of", "csv=p=0", str(path),
            ]
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=15, check=True)
            return float(out.stdout.strip())
        except Exception:
            return 0.0

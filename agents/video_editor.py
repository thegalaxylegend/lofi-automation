"""
Agent 2: The Video Editor — AI-Directed Creative Engine.

Consumes the Director's rich section-by-section creative brief and renders
a production-quality video with:
  - Per-section color grading (each section looks different)
  - Per-section zoom direction and speed
  - Variable transition types between sections (dissolve/fade/cut)
  - Per-section grain intensity
  - Timed text overlays at meaningful moments
  - Professional fade in/out
  - Audio normalization

Supports FFmpeg builds with or without drawtext filter.
When drawtext is unavailable, text is burned into images via Pillow.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import urllib.request
from pathlib import Path

from agents.director import CreativeBrief, SongSection
from core.config import Config, OUTPUT_DIR, TEMP_DIR, TEMPLATES_DIR

logger = logging.getLogger(__name__)


def _get_duration(file_path: str | Path) -> float:
    cmd = [
        "ffprobe", "-v", "quiet",
        "-show_entries", "format=duration",
        "-of", "csv=p=0", str(file_path),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30, check=True)
        return float(result.stdout.strip())
    except (subprocess.SubprocessError, ValueError):
        return 0.0


def _safe_ffmpeg_path(path: Path) -> str:
    """Escape path for FFmpeg filter_complex (colons, backslashes)."""
    s = str(path.absolute()).replace("\\", "/")
    s = s.replace(":", "\\:")
    return s


def _zoom_params(direction: str, speed: str) -> str:
    """Convert zoom direction + speed into a zoompan expression."""
    speeds = {"very_slow": 0.00010, "slow": 0.00020, "medium": 0.00035, "fast": 0.00050}
    spd = speeds.get(speed, 0.00020)

    if direction == "zoom_out":
        return f"if(eq(on,1),1.5,zoom-{spd:.5f})"
    elif direction == "pan_left":
        return f"zoom+{spd:.5f}"  # combined with x movement via x expression
    elif direction == "pan_right":
        return f"zoom+{spd:.5f}"
    elif direction == "static":
        return "1.0"
    else:  # zoom_in (default)
        return f"zoom+{spd:.5f}"


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


def _burn_text_on_image(
    image_path: Path,
    text: str,
    position: str,
    font_path: Path,
    font_size: int = 30,
    opacity: float = 0.85,
) -> Path:
    """
    Burn text onto an image using Pillow. Returns path to the new image.
    Used as fallback when FFmpeg drawtext is not available.
    """
    from PIL import Image, ImageDraw, ImageFont

    img = Image.open(image_path).convert("RGBA")
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    try:
        font = ImageFont.truetype(str(font_path), font_size)
    except Exception:
        font = ImageFont.load_default()

    # Get text bounding box
    bbox = draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    w, h = img.size

    # Calculate position
    pos_map = {
        "center": ((w - tw) // 2, (h - th) // 2),
        "center_bottom": ((w - tw) // 2, int(h * 0.80)),
        "top_center": ((w - tw) // 2, int(h * 0.10)),
        "bottom_right": (w - tw - 30, h - th - 30),
    }
    xy = pos_map.get(position, pos_map["center"])

    alpha = int(255 * opacity)
    draw.text(xy, text, font=font, fill=(255, 255, 255, alpha))

    result = Image.alpha_composite(img, overlay).convert("RGB")
    out_path = image_path.parent / f"{image_path.stem}_text{image_path.suffix}"
    result.save(out_path, quality=95)
    return out_path


class VideoEditor:
    """Renders production-quality videos using the Director's creative brief."""

    def __init__(self) -> None:
        self.config = Config()
        if not shutil.which("ffmpeg"):
            raise EnvironmentError("FFmpeg not found.")
        self._has_drawtext = _check_drawtext_support()
        if self._has_drawtext:
            logger.info("FFmpeg drawtext filter: AVAILABLE")
        else:
            logger.warning("FFmpeg drawtext filter: NOT AVAILABLE — using Pillow text burn-in")

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
            raise ValueError("No images provided.")

        audio_duration = _get_duration(audio_path)
        if audio_duration <= 0:
            raise ValueError(f"Could not get duration for {audio_path}")

        vs = self.config.channel.video
        channel_name = self.config.channel.name

        if output_name is None:
            output_name = f"{audio_path.stem}_final.mp4"
        output_path = OUTPUT_DIR / output_name
        output_path.parent.mkdir(parents=True, exist_ok=True)

        filter_script_path = TEMP_DIR / f"{audio_path.stem}_filters.txt"

        # Choose rendering path based on Director's output richness
        if brief.has_sections:
            logger.info(
                "Creative Engine render: %d sections, arc=%s, %d images",
                len(brief.sections), brief.emotional_journey.arc_type, len(image_paths),
            )
            cmd, filter_text = self._build_creative_engine(
                audio_path, image_paths, output_path, audio_duration,
                brief, audio_math, vs, channel_name, filter_script_path,
            )
        else:
            logger.info(
                "Fallback render: %d images, BPM=%.1f",
                len(image_paths), audio_math.get('bpm', 0),
            )
            cmd, filter_text = self._build_fallback(
                audio_path, image_paths, output_path, audio_duration,
                brief, audio_math, vs, channel_name, filter_script_path,
            )

        # Write filter script and execute FFmpeg
        with open(filter_script_path, "w", encoding="utf-8") as f:
            f.write(filter_text)

        logger.info("FFmpeg executing...")
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
            if result.returncode != 0:
                err = result.stderr[-2000:] if result.stderr else "Unknown"
                logger.error("FFmpeg failed:\n%s", err)
                raise RuntimeError(f"FFmpeg failed: {err}")
        except subprocess.TimeoutExpired:
            logger.error("FFmpeg timed out.")
            raise
        finally:
            filter_script_path.unlink(missing_ok=True)

        if not output_path.exists() or output_path.stat().st_size < 1024:
            raise RuntimeError(f"Output missing or too small: {output_path}")

        logger.info("✅ Render complete: %s", output_path.name)
        return output_path

    # ──────────────────────────────────────────
    #  Creative Engine (per-section rendering)
    # ──────────────────────────────────────────
    def _build_creative_engine(
        self, audio_path, image_paths, output_path, audio_duration,
        brief, audio_math, vs, channel_name, filter_script_path,
    ) -> tuple[list[str], str]:

        w, h = vs.width, vs.height
        fps = vs.fps
        num_images = len(image_paths)
        sections = brief.sections

        # Match images to sections (1 image per section, cycle if needed)
        section_images = []
        for i in range(len(sections)):
            section_images.append(image_paths[i % num_images])

        # If drawtext is NOT available, burn watermark + text overlays into images via Pillow
        if not self._has_drawtext:
            font_path = self._ensure_font()
            section_images = self._burn_all_text_into_images(
                section_images, sections, channel_name, font_path,
            )

        font_path = self._ensure_font()
        safe_font = _safe_ffmpeg_path(font_path)

        filters = []
        img_durations = []

        # Step 1: Scale + zoompan each section's image
        for i, sec in enumerate(sections):
            sec_dur = max(1.0, sec.end_sec - sec.start_sec)
            frames = int(sec_dur * fps)
            img_durations.append(sec_dur)

            z_expr = _zoom_params(sec.zoom.direction, sec.zoom.speed)

            filters.append(
                f"[{i}:v]scale={w}:{h}:force_original_aspect_ratio=increase,"
                f"crop={w}:{h},"
                f"zoompan=z='{z_expr}':d={frames}:s={w}x{h}:fps={fps},"
                f"setpts=PTS-STARTPTS,"
                # Per-section color grading
                f"eq=brightness={sec.color_grade.brightness}:"
                f"contrast={sec.color_grade.contrast}:"
                f"saturation={sec.color_grade.saturation},"
                # Per-section color balance via colorbalance
                f"colorbalance="
                f"rs={sec.color_grade.red_shift}:"
                f"gs={sec.color_grade.green_shift}:"
                f"bs={sec.color_grade.blue_shift},"
                # Per-section grain
                f"noise=c0s={sec.grain_intensity}:c0f=t+u"
                f"[v{i}]"
            )

        # Step 2: Chain transitions between sections
        num_secs = len(sections)
        if num_secs == 1:
            filters.append("[v0]copy[slideshow]")
        else:
            # Get transition types from the brief
            trans_map = {}
            for t in brief.transitions:
                key = f"{t.from_section}->{t.to_section}"
                trans_map[key] = t

            if num_secs == 2:
                t = self._get_transition(trans_map, sections[0].name, sections[1].name)
                xfade_type = self._map_transition_type(t.type)
                offset = max(0, img_durations[0] - t.duration_sec)
                filters.append(
                    f"[v0][v1]xfade=transition={xfade_type}:"
                    f"duration={t.duration_sec}:offset={offset:.3f}[slideshow]"
                )
            else:
                # Chain: v0+v1->x0, x0+v2->x1, ... last->slideshow
                t = self._get_transition(trans_map, sections[0].name, sections[1].name)
                xfade_type = self._map_transition_type(t.type)
                offset = max(0, img_durations[0] - t.duration_sec)
                filters.append(
                    f"[v0][v1]xfade=transition={xfade_type}:"
                    f"duration={t.duration_sec}:offset={offset:.3f}[x0]"
                )
                cumulative = img_durations[0] + img_durations[1] - t.duration_sec

                for i in range(2, num_secs):
                    t = self._get_transition(trans_map, sections[i-1].name, sections[i].name)
                    xfade_type = self._map_transition_type(t.type)
                    prev = f"x{i - 2}"
                    out = "slideshow" if i == num_secs - 1 else f"x{i - 1}"
                    offset = max(0, cumulative - t.duration_sec)
                    filters.append(
                        f"[{prev}][v{i}]xfade=transition={xfade_type}:"
                        f"duration={t.duration_sec}:offset={offset:.3f}[{out}]"
                    )
                    cumulative += img_durations[i] - t.duration_sec

        # Step 3: Professional fade in/out
        fade_in = 2.0
        fade_out = 3.0
        fade_out_start = max(0, audio_duration - fade_out)
        filters.append(
            f"[slideshow]fade=t=in:st=0:d={fade_in},"
            f"fade=t=out:st={fade_out_start:.2f}:d={fade_out}[faded]"
        )

        # Step 4 & 5: Text overlays (only if drawtext is available)
        if self._has_drawtext:
            # Channel watermark via drawtext
            ch_txt = TEMP_DIR / f"{audio_path.stem}_ch.txt"
            ch_txt.write_text(channel_name, encoding="utf-8")
            filters.append(
                f"[faded]drawtext=textfile='{_safe_ffmpeg_path(ch_txt)}':"
                f"fontsize=22:fontcolor=white@0.25:"
                f"x=w-tw-30:y=h-th-30:"
                f"fontfile='{safe_font}'[watermarked]"
            )

            # Section-specific text overlays
            last_label = "watermarked"
            text_idx = 0
            for sec in sections:
                if sec.text_overlay.text and sec.text_overlay.duration_sec > 0:
                    txt_file = TEMP_DIR / f"{audio_path.stem}_txt{text_idx}.txt"
                    txt_file.write_text(sec.text_overlay.text, encoding="utf-8")
                    new_label = f"txt{text_idx}"
                    appear = sec.text_overlay.appear_at_sec
                    end = appear + sec.text_overlay.duration_sec

                    pos_map = {
                        "center": "x=(w-tw)/2:y=(h-th)/2",
                        "center_bottom": "x=(w-tw)/2:y=h*0.80",
                        "top_center": "x=(w-tw)/2:y=h*0.10",
                    }
                    pos = pos_map.get(sec.text_overlay.position, pos_map["center"])

                    filters.append(
                        f"[{last_label}]drawtext="
                        f"textfile='{_safe_ffmpeg_path(txt_file)}':"
                        f"fontsize=30:fontcolor=white@0.85:"
                        f"{pos}:"
                        f"fontfile='{safe_font}':"
                        f"enable='between(t,{appear:.1f},{end:.1f})'[{new_label}]"
                    )
                    last_label = new_label
                    text_idx += 1

            # Final label
            if last_label != "watermarked":
                filters.append(f"[{last_label}]copy[final]")
            else:
                filters.append("[watermarked]copy[final]")
        else:
            # No drawtext: text was already burned into images via Pillow
            filters.append("[faded]copy[final]")

        # Audio normalization
        audio_idx = len(sections)
        filters.append(f"[{audio_idx}:a]loudnorm=I=-16:TP=-1.5:LRA=11[anorm]")

        filter_text = ";\n".join(filters)

        # Build command — use -filter_complex instead of deprecated -filter_complex_script
        cmd = ["ffmpeg", "-y"]
        for i in range(len(sections)):
            cmd.extend(["-loop", "1", "-t", str(img_durations[i]), "-i", str(section_images[i])])
        cmd.extend(["-i", str(audio_path)])

        cmd.extend([
            "-filter_complex", filter_text,
            "-map", "[final]",
            "-map", "[anorm]",
            "-c:v", vs.codec, "-preset", vs.preset,
            "-crf", str(vs.crf), "-pix_fmt", vs.pixel_format,
            "-c:a", "aac", "-b:a", vs.audio_bitrate,
            "-t", str(audio_duration),
            "-movflags", "+faststart",
            str(output_path),
        ])

        return cmd, filter_text

    # ──────────────────────────────────────────
    #  Fallback (if Director returns old format)
    # ──────────────────────────────────────────
    def _build_fallback(
        self, audio_path, image_paths, output_path, audio_duration,
        brief, audio_math, vs, channel_name, filter_script_path,
    ) -> tuple[list[str], str]:
        """Simple render when Director doesn't produce sections."""
        w, h = vs.width, vs.height
        fps = vs.fps
        num_images = len(image_paths)
        bpm = audio_math.get("bpm", 90)
        color = self._mood_color_grade(brief)

        total_frames = int(audio_duration * fps)
        frames_per = max(1, total_frames // num_images)
        extra = total_frames % num_images
        xfade_dur = 1.5 if bpm < 100 else 1.0
        zoom_speed = 0.0002 * (bpm / 80.0)

        font_path = self._ensure_font()
        safe_font = _safe_ffmpeg_path(font_path)

        # If no drawtext, burn watermark into images via Pillow
        if not self._has_drawtext:
            image_paths = [
                _burn_text_on_image(img, channel_name, "bottom_right", font_path, 22, 0.3)
                for img in image_paths
            ]

        filters = []
        img_durs = []

        for i in range(num_images):
            nf = frames_per + (1 if i < extra else 0)
            img_durs.append(nf / fps)
            z = f"zoom+{zoom_speed:.6f}" if i % 2 == 0 else f"if(eq(on,1),1.5,zoom-{zoom_speed:.6f})"
            filters.append(
                f"[{i}:v]scale={w}:{h}:force_original_aspect_ratio=increase,"
                f"crop={w}:{h},"
                f"zoompan=z='{z}':d={nf}:s={w}x{h}:fps={fps},"
                f"setpts=PTS-STARTPTS[v{i}]"
            )

        if num_images == 1:
            filters.append("[v0]copy[slideshow]")
        elif num_images == 2:
            off = max(0, img_durs[0] - xfade_dur)
            filters.append(f"[v0][v1]xfade=transition=fade:duration={xfade_dur}:offset={off:.3f}[slideshow]")
        else:
            off = max(0, img_durs[0] - xfade_dur)
            filters.append(f"[v0][v1]xfade=transition=fade:duration={xfade_dur}:offset={off:.3f}[x0]")
            cum = img_durs[0] + img_durs[1] - xfade_dur
            for i in range(2, num_images):
                prev, out = f"x{i-2}", ("slideshow" if i == num_images - 1 else f"x{i-1}")
                off = max(0, cum - xfade_dur)
                filters.append(f"[{prev}][v{i}]xfade=transition=fade:duration={xfade_dur}:offset={off:.3f}[{out}]")
                cum += img_durs[i] - xfade_dur

        filters.append(f"[slideshow]eq=brightness={color['brightness']}:contrast={color['contrast']}:saturation={color['saturation']}[graded]")
        filters.append(f"[graded]noise=c0s=3:c0f=t+u[grained]")

        fade_out_st = max(0, audio_duration - 3)
        filters.append(f"[grained]fade=t=in:st=0:d=2,fade=t=out:st={fade_out_st:.2f}:d=3[faded]")

        if self._has_drawtext:
            ch_txt = TEMP_DIR / f"{audio_path.stem}_ch.txt"
            ch_txt.write_text(channel_name, encoding="utf-8")
            filters.append(
                f"[faded]drawtext=textfile='{_safe_ffmpeg_path(ch_txt)}':"
                f"fontsize=22:fontcolor=white@0.3:x=w-tw-30:y=h-th-30:"
                f"fontfile='{safe_font}'[final]"
            )
        else:
            # Text already burned into images via Pillow
            filters.append("[faded]copy[final]")

        audio_idx = num_images
        filters.append(f"[{audio_idx}:a]loudnorm=I=-16:TP=-1.5:LRA=11[anorm]")

        filter_text = ";\n".join(filters)

        # Build command — use -filter_complex instead of deprecated -filter_complex_script
        cmd = ["ffmpeg", "-y"]
        for i, img in enumerate(image_paths):
            cmd.extend(["-loop", "1", "-t", str(img_durs[i]), "-i", str(img)])
        cmd.extend(["-i", str(audio_path)])
        cmd.extend([
            "-filter_complex", filter_text,
            "-map", "[final]", "-map", "[anorm]",
            "-c:v", vs.codec, "-preset", vs.preset,
            "-crf", str(vs.crf), "-pix_fmt", vs.pixel_format,
            "-c:a", "aac", "-b:a", vs.audio_bitrate,
            "-t", str(audio_duration), "-movflags", "+faststart",
            str(output_path),
        ])

        return cmd, filter_text

    # ──────────────────────────────────────────
    #  Helpers
    # ──────────────────────────────────────────
    @staticmethod
    def _get_transition(trans_map, from_name, to_name):
        """Look up a transition from the Director's brief, with defaults."""
        from agents.director import SongTransition
        key = f"{from_name}->{to_name}"
        return trans_map.get(key, SongTransition(
            from_section=from_name, to_section=to_name,
            type="slow_dissolve", duration_sec=1.5,
        ))

    @staticmethod
    def _map_transition_type(t_type: str) -> str:
        """Map Director's transition names to FFmpeg xfade transition names."""
        mapping = {
            "slow_dissolve": "fade",
            "fast_dissolve": "fade",
            "fade_through_black": "fadeblack",
            "direct_cut": "fade",  # use very short fade as "cut"
        }
        return mapping.get(t_type, "fade")

    @staticmethod
    def _mood_color_grade(brief: CreativeBrief) -> dict[str, float]:
        grades = {
            "melancholic": {"brightness": -0.05, "contrast": 1.1,  "saturation": 0.7},
            "energetic":   {"brightness": 0.02,  "contrast": 1.2,  "saturation": 1.3},
            "peaceful":    {"brightness": 0.0,   "contrast": 1.0,  "saturation": 0.9},
            "nostalgic":   {"brightness": -0.03, "contrast": 1.05, "saturation": 0.6},
            "dark":        {"brightness": -0.08, "contrast": 1.15, "saturation": 0.5},
            "dreamy":      {"brightness": 0.03,  "contrast": 0.95, "saturation": 1.1},
            "romantic":    {"brightness": 0.01,  "contrast": 1.05, "saturation": 1.2},
            "anxious":     {"brightness": -0.04, "contrast": 1.2,  "saturation": 0.8},
        }
        return grades.get(brief.mood, grades["peaceful"])

    def _burn_all_text_into_images(
        self,
        section_images: list[Path],
        sections: list,
        channel_name: str,
        font_path: Path,
    ) -> list[Path]:
        """Burn channel watermark (and text overlays) into section images via Pillow."""
        result = []
        for i, (img_path, sec) in enumerate(zip(section_images, sections)):
            # Burn watermark
            out = _burn_text_on_image(img_path, channel_name, "bottom_right", font_path, 22, 0.25)
            # Burn section text overlay if present
            if sec.text_overlay.text and sec.text_overlay.duration_sec > 0:
                pos = sec.text_overlay.position or "center"
                out = _burn_text_on_image(out, sec.text_overlay.text, pos, font_path, 30, 0.85)
            result.append(out)
        return result

    @staticmethod
    def _ensure_font() -> Path:
        fonts_dir = TEMPLATES_DIR / "fonts"
        fonts_dir.mkdir(parents=True, exist_ok=True)
        font_path = fonts_dir / "Roboto-Regular.ttf"

        if font_path.exists() and font_path.stat().st_size > 50_000:
            return font_path

        # Updated URLs — the old google/fonts paths moved to googlefonts org
        urls = [
            "https://github.com/googlefonts/roboto/releases/download/v2.138/roboto-unhinted.zip",
            "https://github.com/googlefonts/roboto-classic/raw/main/fonts/ttf/Roboto-Regular.ttf",
            "https://github.com/google/fonts/raw/main/apache/roboto/Roboto%5Bwdth%2Cwght%5D.ttf",
        ]

        # Try direct TTF downloads first
        for url in urls[1:]:
            try:
                logger.info("Downloading font: %s", url[:80])
                urllib.request.urlretrieve(url, font_path)
                if font_path.exists() and font_path.stat().st_size > 50_000:
                    return font_path
                font_path.unlink(missing_ok=True)
            except Exception as e:
                logger.warning("Font download failed: %s", e)
                font_path.unlink(missing_ok=True)

        # System font fallbacks (most reliable on CI)
        for sys_font in [
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
            Path("/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf"),
            Path("/usr/share/fonts/TTF/DejaVuSans.ttf"),
            Path("C:/Windows/Fonts/arial.ttf"),
        ]:
            if sys_font.exists():
                logger.info("Using system font: %s", sys_font)
                return sys_font

        raise RuntimeError("Could not find any font. Install: sudo apt install fonts-dejavu-core")

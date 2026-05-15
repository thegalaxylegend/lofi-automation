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
from agents.audio_event_map import AudioEventMap, AudioEvent
from core.config import Config, OUTPUT_DIR, TEMP_DIR, TEMPLATES_DIR

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────
#  VFX Profile Definitions (audio event → visual response)
# ──────────────────────────────────────────────
VFX_PROFILES = {
    "aggressive": {
        "kick_zoom": 1.05,       # Zoom punch intensity on kick
        "snare_flash": 0.10,     # Brightness spike on snare
        "drop_transition": "fade",  # xfade type on bass drop
        "drop_rgb_split": True,  # Chromatic aberration on drops
        "speed_ramp_slow": 0.5,  # Slow-mo during builds
        "speed_ramp_fast": 1.5,  # Fast during drops
        "base_grain": 1,
        "cooldown": 1.5,         # Min seconds between major VFX
    },
    "vintage_analog": {
        "kick_zoom": 1.02,
        "snare_flash": 0.04,
        "drop_transition": "dissolve",
        "drop_rgb_split": False,
        "speed_ramp_slow": 1.0,
        "speed_ramp_fast": 1.0,
        "base_grain": 5,
        "cooldown": 3.0,
    },
    "ethereal": {
        "kick_zoom": 1.01,
        "snare_flash": 0.02,
        "drop_transition": "smoothup",
        "drop_rgb_split": False,
        "speed_ramp_slow": 0.85,
        "speed_ramp_fast": 1.0,
        "base_grain": 1,
        "cooldown": 5.0,
    },
    "cinematic_drama": {
        "kick_zoom": 1.03,
        "snare_flash": 0.05,
        "drop_transition": "fadeblack",
        "drop_rgb_split": False,
        "speed_ramp_slow": 0.6,
        "speed_ramp_fast": 1.0,
        "base_grain": 2,
        "cooldown": 2.5,
    },
    "raw_minimal": {
        "kick_zoom": 1.0,
        "snare_flash": 0.0,
        "drop_transition": "fade",
        "drop_rgb_split": False,
        "speed_ramp_slow": 1.0,
        "speed_ramp_fast": 1.0,
        "base_grain": 0,
        "cooldown": 3.0,
    },
}


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


def _zoom_params(direction: str, speed: str) -> dict[str, str]:
    """Convert zoom direction + speed into zoompan z/x/y expressions.
    
    Returns a dict with 'z', 'x', 'y' expressions for the zoompan filter.
    Creates organic, cinematic movement instead of static slides.
    """
    speeds = {"very_slow": 0.00008, "slow": 0.00015, "medium": 0.00028, "fast": 0.00042}
    spd = speeds.get(speed, 0.00015)

    # All motions start slightly zoomed in (1.08x) to allow room for panning
    # without showing black edges
    base_zoom = 1.08

    motions = {
        # Classic zoom in — slow push toward subject
        "zoom_in": {
            "z": f"if(eq(on,1),{base_zoom},zoom+{spd:.5f})",
            "x": "iw/2-(iw/zoom/2)",
            "y": "ih/2-(ih/zoom/2)",
        },
        # Zoom out — reveal the full scene gradually
        "zoom_out": {
            "z": f"if(eq(on,1),1.5,zoom-{spd:.5f})",
            "x": "iw/2-(iw/zoom/2)",
            "y": "ih/2-(ih/zoom/2)",
        },
        # Slow left pan with slight zoom — cinematic tracking shot feel
        "pan_left": {
            "z": f"{base_zoom}+{spd*0.3:.5f}*on/{1}",
            "x": f"iw*0.15-on*{spd*25:.3f}",
            "y": "ih/2-(ih/zoom/2)",
        },
        # Slow right pan with slight zoom
        "pan_right": {
            "z": f"{base_zoom}+{spd*0.3:.5f}*on/{1}",
            "x": f"on*{spd*25:.3f}",
            "y": "ih/2-(ih/zoom/2)",
        },
        # Diagonal drift — top-left to bottom-right (like slow camera float)
        "drift_diagonal": {
            "z": f"if(eq(on,1),{base_zoom},zoom+{spd*0.5:.5f})",
            "x": f"on*{spd*15:.3f}",
            "y": f"on*{spd*10:.3f}",
        },
        # Breathing zoom — subtle oscillating zoom that feels organic/alive
        "breathing": {
            "z": f"{base_zoom}+0.02*sin(on*0.015)",
            "x": "iw/2-(iw/zoom/2)",
            "y": "ih/2-(ih/zoom/2)",
        },
        # Ken Burns top-left to bottom-right corner pan
        "ken_burns_tl_br": {
            "z": f"if(eq(on,1),1.3,zoom-{spd*0.5:.5f})",
            "x": f"on*{spd*20:.3f}",
            "y": f"on*{spd*12:.3f}",
        },
        # Ken Burns bottom-right to top-left
        "ken_burns_br_tl": {
            "z": f"if(eq(on,1),{base_zoom},zoom+{spd*0.7:.5f})",
            "x": f"iw*0.2-on*{spd*15:.3f}",
            "y": f"ih*0.2-on*{spd*10:.3f}",
        },
        # Slow upward drift — like the camera is gently rising
        "drift_up": {
            "z": f"{base_zoom}+{spd*0.4:.5f}*on/{1}",
            "x": "iw/2-(iw/zoom/2)",
            "y": f"ih*0.2-on*{spd*12:.3f}",
        },
        # Static with micro-drift — appears static but has subtle life
        "static": {
            "z": f"{base_zoom}+0.008*sin(on*0.01)",
            "x": f"iw/2-(iw/zoom/2)+3*sin(on*0.008)",
            "y": f"ih/2-(ih/zoom/2)+2*sin(on*0.012)",
        },
    }

    motion = motions.get(direction, motions["zoom_in"])
    return motion


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
    font_size: int = 44,
    opacity: float = 0.92,
) -> Path:
    """
    Burn text onto an image using Pillow. Returns path to the new image.
    Used as fallback when FFmpeg drawtext is not available.
    Now includes thick outline for readability on any background.
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

    # Draw thick black outline for readability on any background
    border_w = 3
    for dx in range(-border_w, border_w + 1):
        for dy in range(-border_w, border_w + 1):
            if dx == 0 and dy == 0:
                continue
            draw.text((xy[0] + dx, xy[1] + dy), text, font=font,
                      fill=(0, 0, 0, int(alpha * 0.8)))

    # Main text on top
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
    #  Stock Video Rendering Pipeline
    # ──────────────────────────────────────────
    def render_with_video(
        self,
        audio_path: str | Path,
        clip_paths: list[Path | None],
        brief: CreativeBrief,
        event_map: AudioEventMap,
        *,
        output_name: str | None = None,
    ) -> Path:
        """
        Render a video using stock clips + audio event-driven VFX.

        Args:
            audio_path: Path to the source MP3.
            clip_paths: List of clip paths (one per section, None = missing).
            brief: The Director's CreativeBrief.
            event_map: Audio event map from AudioAnalyzer.
            output_name: Optional custom output filename.
        """
        audio_path = Path(audio_path)
        if not audio_path.exists():
            raise FileNotFoundError(f"Audio not found: {audio_path}")

        audio_duration = _get_duration(audio_path)
        if audio_duration <= 0:
            raise ValueError(f"Could not get duration for {audio_path}")

        vs = self.config.channel.video
        channel_name = self.config.channel.name

        if output_name is None:
            output_name = f"{audio_path.stem}_final.mp4"
        output_path = OUTPUT_DIR / output_name
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # Step 1: Normalize all clips to 30fps, 1920x1080, no audio
        normalized = self._normalize_clips(clip_paths, vs)
        if not any(normalized):
            raise ValueError("No valid video clips after normalization.")

        # Step 2: Get VFX profile
        profile_name = brief.vfx_profile or "cinematic_drama"
        profile = VFX_PROFILES.get(profile_name, VFX_PROFILES["cinematic_drama"])
        logger.info("VFX Profile: %s", profile_name)

        # Step 3: Build the filter graph
        sections = brief.sections if brief.has_sections else []
        filters = []
        input_count = 0
        section_labels = []

        for i, sec in enumerate(sections):
            clip = normalized[i] if i < len(normalized) else None
            if clip is None:
                continue

            sec_dur = max(1.0, sec.end_sec - sec.start_sec)

            # Input: loop the clip to cover the section duration
            # (actual -stream_loop is in the command, not filter)

            # Per-section color grading
            cg = sec.color_grade
            label = f"v{input_count}"
            filters.append(
                f"[{input_count}:v]"
                f"eq=brightness={cg.brightness}:"
                f"contrast={cg.contrast}:"
                f"saturation={cg.saturation},"
                f"colorbalance="
                f"rs={cg.red_shift}:"
                f"gs={cg.green_shift}:"
                f"bs={cg.blue_shift},"
                f"vignette=PI/4,"
                f"noise=c0s={profile['base_grain']}:c0f=t+u,"
                f"setpts=PTS-STARTPTS"
                f"[{label}]"
            )
            section_labels.append((label, sec_dur, sec))
            input_count += 1

        if not section_labels:
            raise ValueError("No valid sections to render.")

        # Step 4: Chain transitions — speed matches song tempo
        # Slow sad song = long dissolves, fast rap = quick hard cuts
        def _get_transition_for_energy(energy: str, bpm: float) -> tuple[str, float]:
            """Return (xfade_type, duration) based on section energy + BPM."""
            beat_dur = 60.0 / max(bpm, 60)
            if energy in ("very_high", "high"):
                # Fast songs: hard cuts, 1-2 beat duration
                return "wipeleft", max(0.3, beat_dur * 0.5)
            elif energy == "medium":
                # Medium: smooth dissolve, 2-4 beat duration
                return profile["drop_transition"], min(2.0, beat_dur * 2)
            elif energy in ("low", "very_low", "fading"):
                # Slow songs: long soft dissolve, 4-8 beat duration
                return "dissolve", min(3.0, beat_dur * 4)
            return profile["drop_transition"], 1.0

        if len(section_labels) == 1:
            filters.append(f"[{section_labels[0][0]}]copy[slideshow]")
        else:
            # First pair
            next_energy = section_labels[1][2].energy if len(section_labels) > 1 else "medium"
            xfade_type, trans_dur = _get_transition_for_energy(next_energy, event_map.bpm)

            # First pair
            offset = max(0, section_labels[0][1] - trans_dur)
            filters.append(
                f"[{section_labels[0][0]}][{section_labels[1][0]}]"
                f"xfade=transition={xfade_type}:"
                f"duration={trans_dur}:offset={offset:.3f}[x0]"
            )
            cumulative = section_labels[0][1] + section_labels[1][1] - trans_dur

            for j in range(2, len(section_labels)):
                prev = f"x{j - 2}"
                out = "slideshow" if j == len(section_labels) - 1 else f"x{j - 1}"

                # Dynamic: each transition adapts to the INCOMING section's energy
                incoming_energy = section_labels[j][2].energy
                xfade_type, trans_dur = _get_transition_for_energy(
                    incoming_energy, event_map.bpm
                )

                # Override with Director's explicit transitions if available
                if j - 1 < len(brief.transitions):
                    t = brief.transitions[j - 1]
                    xfade_type = self._map_transition_type(t.type)
                    trans_dur = t.duration_sec

                offset = max(0, cumulative - trans_dur)
                filters.append(
                    f"[{prev}][{section_labels[j][0]}]"
                    f"xfade=transition={xfade_type}:"
                    f"duration={trans_dur}:offset={offset:.3f}[{out}]"
                )
                cumulative += section_labels[j][1] - trans_dur

        # Step 5: Add audio-reactive VFX using the event map
        # Apply kick zoom pulses and snare flashes via eq enable expressions
        vfx_filters = self._build_audio_reactive_vfx(
            event_map, profile, audio_duration
        )
        if vfx_filters:
            filters.append(f"[slideshow]{','.join(vfx_filters)}[vfxed]")
            last_video = "vfxed"
        else:
            last_video = "slideshow"

        # Step 6: Fade in/out
        fade_in = 2.0
        fade_out = 3.0
        fade_out_start = max(0, audio_duration - fade_out)
        filters.append(
            f"[{last_video}]fade=t=in:st=0:d={fade_in},"
            f"fade=t=out:st={fade_out_start:.2f}:d={fade_out}[faded]"
        )

        # Step 7: Channel watermark (if drawtext available)
        if self._has_drawtext:
            font_path = self._ensure_font()
            safe_font = _safe_ffmpeg_path(font_path)
            ch_txt = TEMP_DIR / f"{audio_path.stem}_ch.txt"
            ch_txt.write_text(channel_name, encoding="utf-8")
            filters.append(
                f"[faded]drawtext=textfile='{_safe_ffmpeg_path(ch_txt)}':"
                f"fontsize=26:fontcolor=white@0.45:"
                f"borderw=2:bordercolor=black@0.3:"
                f"x=w-tw-30:y=h-th-30:"
                f"fontfile='{safe_font}'[final]"
            )
        else:
            filters.append("[faded]copy[final]")

        # Step 8: Audio normalization
        audio_input_idx = input_count
        filters.append(f"[{audio_input_idx}:a]loudnorm=I=-16:TP=-1.5:LRA=11[anorm]")

        filter_text = ";\n".join(filters)

        # Step 9: Build FFmpeg command
        filter_script_path = TEMP_DIR / f"{audio_path.stem}_video_filters.txt"
        with open(filter_script_path, "w", encoding="utf-8") as f:
            f.write(filter_text)

        cmd = ["ffmpeg", "-y"]
        for i, (label, sec_dur, sec) in enumerate(section_labels):
            clip = normalized[[j for j in range(len(normalized)) if normalized[j]][i]]
            cmd.extend([
                "-stream_loop", "-1",
                "-t", f"{sec_dur:.3f}",
                "-i", str(clip),
            ])
        cmd.extend(["-i", str(audio_path)])

        cmd.extend([
            "-filter_complex_script", str(filter_script_path),
            "-map", "[final]",
            "-map", "[anorm]",
            "-c:v", vs.codec, "-preset", vs.preset,
            "-crf", str(vs.crf), "-pix_fmt", vs.pixel_format,
            "-c:a", "aac", "-b:a", vs.audio_bitrate,
            "-t", str(audio_duration),
            "-movflags", "+faststart",
            str(output_path),
        ])

        # Execute
        logger.info("FFmpeg video render executing (%d clips)...", len(section_labels))
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
            if result.returncode != 0:
                err = result.stderr[-2000:] if result.stderr else "Unknown"
                logger.error("FFmpeg video render failed:\n%s", err)
                raise RuntimeError(f"FFmpeg video render failed: {err}")
        except subprocess.TimeoutExpired:
            logger.error("FFmpeg video render timed out.")
            raise
        finally:
            filter_script_path.unlink(missing_ok=True)

        if not output_path.exists() or output_path.stat().st_size < 1024:
            raise RuntimeError(f"Video output missing or too small: {output_path}")

        size_mb = output_path.stat().st_size / (1024 * 1024)
        logger.info("✅ Video render complete: %s (%.1f MB)", output_path.name, size_mb)
        return output_path

    def _normalize_clips(
        self, clip_paths: list[Path | None], vs
    ) -> list[Path | None]:
        """Normalize all clips to exact 30fps, 1920x1080, no audio."""
        normalized = []
        norm_dir = TEMP_DIR / "normalized"
        norm_dir.mkdir(parents=True, exist_ok=True)

        for i, clip in enumerate(clip_paths):
            if clip is None or not clip.exists():
                normalized.append(None)
                continue

            out = norm_dir / f"norm_{i:02d}.mp4"
            cmd = [
                "ffmpeg", "-y", "-i", str(clip),
                "-r", str(vs.fps),
                "-vf", (
                    f"scale={vs.width}:{vs.height}:"
                    f"force_original_aspect_ratio=increase,"
                    f"crop={vs.width}:{vs.height}"
                ),
                "-an",  # Strip audio from stock clips
                "-c:v", "libx264", "-crf", "18", "-preset", "fast",
                "-pix_fmt", "yuv420p",
                str(out),
            ]
            try:
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
                if result.returncode == 0 and out.exists() and out.stat().st_size > 1024:
                    normalized.append(out)
                    logger.info("Normalized clip %d: %s", i, out.name)
                else:
                    normalized.append(None)
                    logger.warning("Clip %d normalization failed.", i)
            except Exception as exc:
                normalized.append(None)
                logger.error("Clip %d normalization error: %s", i, exc)

        return normalized

    def _build_audio_reactive_vfx(
        self,
        event_map: AudioEventMap,
        profile: dict,
        duration: float,
    ) -> list[str]:
        """Build FFmpeg filter expressions driven by audio events."""
        vfx = []
        last_event_time = -999.0
        cooldown = profile["cooldown"]

        # Collect snare flash timestamps (with cooldown)
        flash_times = []
        for event in event_map.events:
            if event.event_type == "snare" and profile["snare_flash"] > 0:
                if event.timestamp - last_event_time >= cooldown:
                    flash_times.append(event.timestamp)
                    last_event_time = event.timestamp

        # Build brightness flash enable expression for snare hits
        if flash_times and profile["snare_flash"] > 0:
            flash_exprs = []
            for t in flash_times[:30]:  # Cap at 30 to avoid filter explosion
                flash_exprs.append(f"between(t,{t:.2f},{t + 0.067:.3f})")
            if flash_exprs:
                flash_cond = "+".join(flash_exprs)
                brightness_boost = profile["snare_flash"]
                vfx.append(
                    f"eq=brightness='{brightness_boost}*({flash_cond})'"
                )

        return vfx

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

        # Step 1: Scale + zoompan each section's image with cinematic motion
        for i, sec in enumerate(sections):
            sec_dur = max(1.0, sec.end_sec - sec.start_sec)
            frames = int(sec_dur * fps)
            img_durations.append(sec_dur)

            motion = _zoom_params(sec.zoom.direction, sec.zoom.speed)

            filters.append(
                f"[{i}:v]scale={w}:{h}:force_original_aspect_ratio=increase,"
                f"crop={w}:{h},"
                f"zoompan=z='{motion['z']}':x='{motion['x']}':y='{motion['y']}':"
                f"d={frames}:s={w}x{h}:fps={fps},"
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
                # Subtle cinematic vignette on each section
                f"vignette=PI/4,"
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
                f"fontsize=26:fontcolor=white@0.45:"
                f"borderw=2:bordercolor=black@0.3:"
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

                    # Fade-in/out animation: 0.8s fade in, 0.8s fade out
                    fade_in_dur = 0.8
                    fade_out_dur = 0.8
                    # Alpha expression: ramp up during fade_in, full during middle, ramp down during fade_out
                    alpha_expr = (
                        f"if(lt(t,{appear+fade_in_dur:.1f}),"
                        f"(t-{appear:.1f})/{fade_in_dur:.1f},"
                        f"if(gt(t,{end-fade_out_dur:.1f}),"
                        f"({end:.1f}-t)/{fade_out_dur:.1f},"
                        f"1))"
                    )

                    filters.append(
                        f"[{last_label}]drawtext="
                        f"textfile='{_safe_ffmpeg_path(txt_file)}':"
                        f"fontsize=44:fontcolor=white:alpha='{alpha_expr}':"
                        f"borderw=3:bordercolor=black@0.7:"
                        f"shadowcolor=black@0.5:shadowx=3:shadowy=3:"
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
            # Cycle through different motions for variety
            motion_cycle = ["zoom_in", "drift_diagonal", "breathing", "pan_left", "ken_burns_tl_br", "zoom_out", "drift_up"]
            direction = motion_cycle[i % len(motion_cycle)]
            motion = _zoom_params(direction, "slow")
            filters.append(
                f"[{i}:v]scale={w}:{h}:force_original_aspect_ratio=increase,"
                f"crop={w}:{h},"
                f"zoompan=z='{motion['z']}':x='{motion['x']}':y='{motion['y']}':"
                f"d={nf}:s={w}x{h}:fps={fps},"
                f"vignette=PI/4,"
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
        # Dynamic grain: lo-fi/nostalgic moods get more grain, party/energetic get minimal
        grain = self._mood_grain_intensity(brief)
        filters.append(f"[graded]noise=c0s={grain}:c0f=t+u[grained]")

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

    @staticmethod
    def _mood_grain_intensity(brief: CreativeBrief) -> int:
        """Dynamic film grain based on mood. Lo-fi/nostalgic = heavy grain, party/energetic = clean."""
        grain_map = {
            "melancholic": 4,
            "nostalgic":   5,
            "dark":        3,
            "dreamy":      2,
            "peaceful":    2,
            "anxious":     3,
            "romantic":    2,
            "energetic":   1,  # Party/dance songs should look clean and vibrant
        }
        grain = grain_map.get(brief.mood, 2)
        # Override: high energy songs always get minimal grain
        if brief.energy.lower() == "high":
            grain = min(grain, 1)
        return grain

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

        # Prefer Noto Sans Devanagari for Hindi text support
        noto_hindi = fonts_dir / "NotoSansDevanagari-Regular.ttf"
        roboto_font = fonts_dir / "Roboto-Regular.ttf"

        # Check if we already have a Hindi-compatible font
        if noto_hindi.exists() and noto_hindi.stat().st_size > 50_000:
            return noto_hindi
        if roboto_font.exists() and roboto_font.stat().st_size > 50_000:
            return roboto_font

        # Try downloading Noto Sans Devanagari (supports Hindi + Latin)
        hindi_font_urls = [
            ("NotoSansDevanagari-Regular.ttf",
             "https://github.com/googlefonts/noto-fonts/raw/main/hinted/ttf/NotoSansDevanagari/NotoSansDevanagari-Regular.ttf"),
            ("NotoSansDevanagari-Regular.ttf",
             "https://github.com/google/fonts/raw/main/ofl/notosansdevanagari/NotoSansDevanagari%5Bwdth%2Cwght%5D.ttf"),
        ]

        for fname, url in hindi_font_urls:
            target = fonts_dir / fname
            try:
                logger.info("Downloading Hindi font: %s", url[:80])
                urllib.request.urlretrieve(url, target)
                if target.exists() and target.stat().st_size > 50_000:
                    logger.info("Hindi font downloaded: %s", fname)
                    return target
                target.unlink(missing_ok=True)
            except Exception as e:
                logger.warning("Hindi font download failed: %s", e)
                target.unlink(missing_ok=True)

        # Fallback: try Roboto (Latin only)
        roboto_urls = [
            "https://github.com/googlefonts/roboto-classic/raw/main/fonts/ttf/Roboto-Regular.ttf",
            "https://github.com/google/fonts/raw/main/apache/roboto/Roboto%5Bwdth%2Cwght%5D.ttf",
        ]
        for url in roboto_urls:
            try:
                logger.info("Downloading font: %s", url[:80])
                urllib.request.urlretrieve(url, roboto_font)
                if roboto_font.exists() and roboto_font.stat().st_size > 50_000:
                    return roboto_font
                roboto_font.unlink(missing_ok=True)
            except Exception as e:
                logger.warning("Font download failed: %s", e)
                roboto_font.unlink(missing_ok=True)

        # System font fallbacks — prioritize Hindi-capable fonts
        for sys_font in [
            # Hindi-compatible system fonts (CI installs fonts-noto)
            Path("/usr/share/fonts/truetype/noto/NotoSansDevanagari-Regular.ttf"),
            Path("/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf"),
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
            Path("/usr/share/fonts/TTF/DejaVuSans.ttf"),
            # Windows
            Path("C:/Windows/Fonts/mangal.ttf"),  # Hindi font on Windows
            Path("C:/Windows/Fonts/arial.ttf"),
        ]:
            if sys_font.exists():
                logger.info("Using system font: %s", sys_font)
                return sys_font

        raise RuntimeError("Could not find any font. Install: sudo apt install fonts-noto")

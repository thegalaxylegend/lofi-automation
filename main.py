"""
Main Orchestrator — The Pipeline Controller.

Coordinates all agents in sequence to transform a single MP3 into:
  1. A rendered long-form video (MP4)
  2. A YouTube Short (MP4)
  3. A thumbnail (JPG)
  4. SEO metadata (title, description, tags)

Usage:
  python main.py audio/my_song.mp3
  python main.py audio/           # process all MP3s in directory
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from core.config import Config, OUTPUT_DIR, TEMP_DIR
from core.discord_webhook import DiscordNotifier
from core.memory import initialize_all_memories, pipeline_memory

logger = logging.getLogger(__name__)


@dataclass
class PipelineResult:
    """Result of processing a single MP3 file."""

    audio_file: str = ""
    video_path: str = ""
    short_path: str = ""
    thumbnail_path: str = ""
    metadata_path: str = ""
    title: str = ""
    success: bool = False
    error: str = ""
    render_time_sec: float = 0.0
    youtube_id: str = ""
    short_youtube_id: str = ""


@dataclass
class BatchResult:
    """Result of processing all MP3 files in a batch."""

    results: list[PipelineResult] = field(default_factory=list)
    total_time_sec: float = 0.0

    @property
    def succeeded(self) -> int:
        return sum(1 for r in self.results if r.success)

    @property
    def failed(self) -> int:
        return sum(1 for r in self.results if not r.success)


def process_single(audio_path: Path) -> PipelineResult:
    """
    Process a single MP3 through the full agent pipeline.

    Order of operations:
      Director → BackgroundFetcher → VideoEditor → Marketer →
      ThumbnailCreator → Distributor → QATester → Compliance
    """
    result = PipelineResult(audio_file=str(audio_path))
    start = time.time()

    try:
        # ── Agent 1: Director (Audio Analysis) ──────────────
        logger.info("=" * 60)
        logger.info("PIPELINE START: %s", audio_path.name)
        logger.info("=" * 60)

        from agents.director import Director
        director = Director()
        brief = director.analyze(audio_path)

        logger.info(
            "Director complete: mood=%s, energy=%s, vfx=%s",
            brief.mood, brief.energy, brief.vfx_profile,
        )

        # ── Audio Event Map (Beat/Instrument Detection) ─────
        from agents.audio_event_map import AudioAnalyzer as EventAnalyzer
        event_analyzer = EventAnalyzer()
        event_map = event_analyzer.analyze(audio_path)
        logger.info(
            "Audio events: BPM=%.1f, kicks=%d, snares=%d, drops=%d, breaths=%d",
            event_map.bpm, len(event_map.kick_timestamps),
            len(event_map.snare_timestamps), len(event_map.bass_drops),
            len(event_map.breathing_zones),
        )

        # Legacy audio_math dict (backward compatibility for image pipeline fallback)
        from core.audio_analyzer import AudioAnalyzer
        audio_math = AudioAnalyzer.analyze_audio(audio_path)

        # ── Video Pipeline (Stock Clips + Audio-Reactive VFX) ─
        video_path = None
        image_paths = None  # will be set only if we fall back

        from agents.video_editor import VideoEditor
        editor = VideoEditor()

        try:
            from agents.video_fetcher import VideoFetcher
            vfetcher = VideoFetcher()
            clip_paths = vfetcher.fetch_clips(brief)

            # Check if we got enough clips (at least 50% of sections)
            valid_clips = [p for p in clip_paths if p is not None]
            if len(valid_clips) >= max(1, len(brief.sections) // 2):
                logger.info(
                    "Video pipeline: %d/%d clips fetched. Rendering with video...",
                    len(valid_clips), len(brief.sections),
                )
                video_path = editor.render_with_video(
                    audio_path, clip_paths, brief, event_map,
                )
            else:
                logger.warning(
                    "Only %d/%d clips fetched. Falling back to image pipeline.",
                    len(valid_clips), len(brief.sections),
                )
        except Exception as exc:
            logger.warning(
                "Video pipeline failed: %s. Falling back to image pipeline.", exc
            )

        # ── Fallback: Image Pipeline ────────────────────
        if video_path is None:
            logger.info("Using image pipeline fallback...")
            from agents.image_fetcher import ImageFetcher
            fetcher = ImageFetcher()
            image_paths = fetcher.fetch_images(brief)

            if not image_paths:
                raise RuntimeError("Failed to fetch AI images.")

            logger.info("Images generated: %d", len(image_paths))
            video_path = editor.render(audio_path, image_paths, brief, audio_math)

        result.video_path = str(video_path)
        logger.info("Video rendered: %s", video_path.name)

        # ── Agent 3: Marketer (SEO Metadata) ────────────────
        from agents.marketer import Marketer

        # Load past titles for anti-repetition
        mem = pipeline_memory()
        past_titles = mem.get("generated_titles", [])[-15:]

        marketer = Marketer()
        metadata = marketer.generate_metadata(
            mood=brief.mood,
            energy=brief.energy,
            emotional_tone=brief.emotional_tone,
            visual_style=brief.visual_style,
            title_keywords=brief.title_keywords,
            thumbnail_prompt_hint=brief.thumbnail_prompt,
            song_dna=brief.song_dna.model_dump() if brief.song_dna else None,
            past_titles=past_titles,
        )

        # Save metadata to file
        meta_path = OUTPUT_DIR / f"{audio_path.stem}_metadata.txt"
        metadata.to_file(meta_path)
        result.metadata_path = str(meta_path)
        result.title = metadata.title

        # Record title in memory for future anti-repetition
        mem.append_to_list("generated_titles", metadata.title)

        logger.info("Metadata generated: '%s'", metadata.title)

        # ── Agent 4: Thumbnail Creator ──────────────────────
        from agents.thumbnail_creator import ThumbnailCreator
        thumb_creator = ThumbnailCreator()
        thumb_path = thumb_creator.create(
            title=metadata.title,
            thumbnail_prompt=metadata.thumbnail_prompt,
            output_name=f"{audio_path.stem}_thumb.jpg",
        )

        result.thumbnail_path = str(thumb_path)
        logger.info("Thumbnail created: %s", thumb_path.name)

        # ── Agent 10: Distributor (YouTube Short) ───────────
        config = Config()
        if config.auto_shorts:
            from agents.distributor import Distributor
            distributor = Distributor()

            # Pick the best background for the Short
            short_img = None
            if image_paths:
                short_img = image_paths[0]  # default
                if brief.has_sections and brief.shorts.image_section:
                    for i, sec in enumerate(brief.sections):
                        if sec.name == brief.shorts.image_section and i < len(image_paths):
                            short_img = image_paths[i]
                            break

            # If video pipeline was used (no images), extract a frame from the video
            if short_img is None and video_path:
                import subprocess
                frame_path = TEMP_DIR / f"{audio_path.stem}_short_frame.jpg"
                # Extract frame at the recommended Short start time
                start_sec = brief.shorts.recommended_start_sec if brief.has_sections else 10
                try:
                    subprocess.run(
                        ["ffmpeg", "-y", "-ss", str(start_sec), "-i", str(video_path),
                         "-frames:v", "1", "-q:v", "2", str(frame_path)],
                        capture_output=True, timeout=30,
                    )
                    if frame_path.exists():
                        short_img = frame_path
                except Exception:
                    pass

            if short_img:
                short_path = distributor.create_short(
                    audio_path=audio_path,
                    background_path=short_img,
                    brief=brief,
                    shorts_text=metadata.shorts_text,
                )
                result.short_path = str(short_path)
                logger.info("Short created: %s", short_path.name)
            else:
                logger.warning("No background available for Short. Skipping.")

        # ── Agent 5: QA Tester ──────────────────────────────
        from agents.qa_tester import QATester
        qa = QATester()
        qa_result = qa.validate(
            video_path=video_path,
            thumbnail_path=thumb_path,
            metadata=metadata,
        )

        if not qa_result.passed:
            logger.warning("QA FAILED: %s", qa_result.summary())
            # Don't halt — log the issue but continue
            # The Analyst will review failures later

        # ── Agent 6: Compliance Check ───────────────────────
        from agents.compliance import ComplianceAgent
        compliance = ComplianceAgent()
        comp_result = compliance.check(metadata)

        if not comp_result.is_safe:
            logger.warning("COMPLIANCE FLAGGED: %s", comp_result.summary())
            # Flag for manual review but don't delete the outputs

        result.success = True
        
        # Mark as processed in memory
        mem = pipeline_memory()
        mem.append_to_list("processed_files", audio_path.name)
        
        # ── Agent 11: YouTube Uploader ───────────────────────
        try:
            if config.youtube_refresh_token:
                from core.youtube_uploader import YouTubeUploader
                uploader = YouTubeUploader()
                
                privacy = "private" if config.upload_as_draft else "public"
                
                # Upload main video
                logger.info("Uploading main video to YouTube (%s)...", privacy)
                vid_id = uploader.upload_video(
                    video_path=video_path,
                    title=metadata.title,
                    description=metadata.description,
                    tags=metadata.tags,
                    privacy_status=privacy
                )
                if vid_id:
                    result.youtube_id = vid_id
                    # Set thumbnail
                    uploader.set_thumbnail(vid_id, thumb_path)
                
                # Upload short if created
                if result.short_path:
                    logger.info("Uploading Short to YouTube (%s)...", privacy)
                    short_vid_id = uploader.upload_video(
                        video_path=Path(result.short_path),
                        title=f"{metadata.title} #shorts",
                        description=f"{metadata.shorts_text}\n\n#shorts #lofi",
                        tags=metadata.tags + ["shorts"],
                        privacy_status=privacy
                    )
                    if short_vid_id:
                        result.short_youtube_id = short_vid_id
            else:
                logger.info("YouTube refresh token missing. Skipping upload.")
        except Exception as upload_exc:
            logger.error("YouTube Upload failed (video still saved): %s", upload_exc)
            result.error = f"Upload failed: {upload_exc}"
            # We don't set result.success = False here because the video was rendered successfully.

    except Exception as exc:
        result.error = str(exc)
        result.success = False
        logger.error("PIPELINE FAILED for %s: %s", audio_path.name, exc, exc_info=True)

    result.render_time_sec = time.time() - start
    logger.info(
        "Pipeline %s for %s in %.1fs",
        "COMPLETE" if result.success else "FAILED",
        audio_path.name,
        result.render_time_sec,
    )
    return result


def _detect_triggered_file(audio_dir: Path) -> Path | None:
    """
    Detect which MP3 the Telegram bot pushed for processing.

    Priority:
      1. Read audio/.trigger — the bot writes the exact filename here
      2. Search git log for the most recent 'Auto-ingest' commit message
      3. Check git diff across recent commits for added MP3 files
    """
    import subprocess

    # Method 1 (PRIMARY): Read the .trigger file for the filename
    trigger_file = audio_dir / ".trigger"
    if trigger_file.exists():
        trigger_content = trigger_file.read_text(encoding="utf-8").strip()
        logger.info("Trigger file contents: '%s'", trigger_content)
        # The trigger file contains: line1=filename, line2=timestamp
        # Extract only the first line as the filename
        trigger_filename = trigger_content.split("\n")[0].strip()
        if trigger_filename and not trigger_filename.replace(".", "").replace("-", "").isdigit():
            # It's a filename, not a timestamp
            target = audio_dir / trigger_filename
            if target.exists():
                logger.info("Trigger file detected target: %s", target.name)
                return target
            else:
                logger.warning("Trigger file says '%s' but file not found in %s", trigger_filename, audio_dir)

    # Method 2: Search git log for the most recent Auto-ingest commit
    try:
        result = subprocess.run(
            ["git", "log", "--all", "--format=%s", "-n", "20"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            import re
            for line in result.stdout.splitlines():
                if "Auto-ingest:" in line:
                    match = re.search(r"Auto-ingest:\s*(.+?)\s*from Telegram", line)
                    if match:
                        fname = match.group(1).strip()
                        target = audio_dir / fname
                        if target.exists():
                            logger.info("Git log detected target: %s", target.name)
                            return target
                    break  # Only check the most recent Auto-ingest commit
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        logger.warning("Git log detection failed: %s", exc)

    # Method 3: Check git diff across multiple commits for added MP3s
    for depth in range(1, 6):
        try:
            result = subprocess.run(
                ["git", "diff", "--name-only", "--diff-filter=A",
                 f"HEAD~{depth}", "HEAD", "--", "audio/"],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0 and result.stdout.strip():
                added_files = [
                    line.strip() for line in result.stdout.strip().splitlines()
                    if line.strip().lower().endswith(".mp3")
                ]
                if added_files:
                    target = audio_dir / Path(added_files[-1]).name
                    if target.exists():
                        logger.info(
                            "Git diff (HEAD~%d) detected target: %s",
                            depth, target.name,
                        )
                        return target
        except (subprocess.SubprocessError, FileNotFoundError):
            break

    return None


def process_batch(audio_dir: Path) -> BatchResult:
    """Process the MP3 file that triggered the pipeline.

    Detection priority:
      1. Git diff — which file was added/changed in the latest commit
      2. Commit message — parse the Telegram bot's auto-ingest commit
      3. Fallback — newest unprocessed file by mtime (original behavior)
    """
    batch = BatchResult()
    start = time.time()

    mp3_files = sorted(audio_dir.glob("*.mp3"), key=lambda f: f.stat().st_mtime, reverse=True)
    if not mp3_files:
        logger.warning("No MP3 files found in %s", audio_dir)
        return batch

    # Try to detect the actual triggered file first (primary mechanism)
    target = _detect_triggered_file(audio_dir)

    if target is None:
        # Fallback: use dedup + mtime approach
        mem = pipeline_memory()
        processed = set(mem.get("processed_files", []))
        logger.info("Dedup check: %d files in memory", len(processed))

        unprocessed = [f for f in mp3_files if f.name not in processed]
        if not unprocessed:
            logger.info("All %d MP3 files already processed.", len(mp3_files))
            return batch

        target = unprocessed[0]
        logger.warning(
            "Could not detect triggered file via git. "
            "Falling back to newest unprocessed by mtime: %s",
            target.name,
        )

    logger.info("Found %d MP3 files. Processing: %s", len(mp3_files), target.name)

    result = process_single(target)
    batch.results.append(result)

    # Record processed file in memory for dedup
    if result.success:
        mem = pipeline_memory()
        mem.append_to_list("processed_files", target.name)
        mem.update_key("last_run", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))

    batch.total_time_sec = time.time() - start
    logger.info(
        "BATCH COMPLETE: %d/%d succeeded in %.1fs",
        batch.succeeded, len(batch.results), batch.total_time_sec,
    )
    return batch


def _send_discord_summary(batch: BatchResult) -> None:
    """Send a pipeline completion summary to Discord."""
    try:
        notifier = DiscordNotifier()

        for r in batch.results:
            if r.success:
                notifier.notify_upload(
                    title=r.title or r.audio_file,
                    channel_name=Config().channel.name,
                )
            else:
                notifier.notify_error(
                    error=r.error,
                    agent="Pipeline",
                )

        notifier.notify_pipeline_complete({
            "videos": batch.succeeded,
            "shorts": sum(1 for r in batch.results if r.short_path),
            "render_time": f"{batch.total_time_sec:.0f}s",
            "api_calls": "N/A",
        })
    except Exception as exc:
        logger.warning("Discord notification failed: %s", exc)


def _cleanup_temp() -> None:
    """Clean up temporary files after pipeline completion."""
    if TEMP_DIR.exists():
        for f in TEMP_DIR.iterdir():
            if f.is_file() and f.suffix in (".mp4", ".jpg", ".png", ".webm"):
                try:
                    f.unlink()
                except OSError:
                    pass


def main() -> None:
    """Entry point for the pipeline."""
    # Configure logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    parser = argparse.ArgumentParser(
        description="Lo-fi YouTube Automation Pipeline"
    )
    parser.add_argument(
        "input",
        help="Path to an MP3 file or a directory containing MP3 files.",
    )
    parser.add_argument(
        "--channel",
        default=None,
        help="Channel key to use (e.g., 'lofi' or 'hindi'). Defaults to brand_config.",
    )
    parser.add_argument(
        "--no-shorts",
        action="store_true",
        help="Skip YouTube Short generation.",
    )
    parser.add_argument(
        "--no-discord",
        action="store_true",
        help="Skip Discord notifications.",
    )
    args = parser.parse_args()

    # Initialize
    config = Config()
    if args.channel:
        config.active_channel_key = args.channel
    if args.no_shorts:
        config.auto_shorts = False

    initialize_all_memories()

    input_path = Path(args.input)

    if input_path.is_file() and input_path.suffix.lower() == ".mp3":
        result = process_single(input_path)
        batch = BatchResult(results=[result], total_time_sec=result.render_time_sec)
    elif input_path.is_dir():
        batch = process_batch(input_path)
    else:
        logger.error("Input must be an MP3 file or directory: %s", input_path)
        sys.exit(1)

    # Discord notifications
    if not args.no_discord:
        _send_discord_summary(batch)

    # Cleanup temp files
    _cleanup_temp()

    # Exit with error code if any failures
    if batch.failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()

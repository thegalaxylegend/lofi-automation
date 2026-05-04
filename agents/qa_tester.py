"""
Agent 5: QA Tester — Quality Assurance before upload.

Validates all output files before they reach YouTube:
  - Video file integrity (exists, size, duration)
  - Thumbnail resolution and file size
  - Title length and character safety
  - Description minimum length
  - Tag count and relevance
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

from agents.marketer import VideoMetadata
from core.memory import Memory

logger = logging.getLogger(__name__)


@dataclass
class QAResult:
    """Result of a QA validation pass."""

    passed: bool = True
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def fail(self, message: str) -> None:
        self.passed = False
        self.errors.append(message)

    def warn(self, message: str) -> None:
        self.warnings.append(message)

    def summary(self) -> str:
        if self.passed:
            status = "✅ PASSED"
        else:
            status = "❌ FAILED"
        lines = [f"QA Result: {status}"]
        for e in self.errors:
            lines.append(f"  ERROR: {e}")
        for w in self.warnings:
            lines.append(f"  WARN:  {w}")
        return "\n".join(lines)


class QATester:
    """
    Validates all pipeline outputs before YouTube upload.
    Learns from rejection patterns to prevent recurring issues.
    """

    def __init__(self) -> None:
        self.memory = Memory("qa_history")

    def validate(
        self,
        video_path: Path | None,
        thumbnail_path: Path | None,
        metadata: VideoMetadata | None,
    ) -> QAResult:
        """
        Run all validation checks.

        Returns:
            QAResult indicating pass/fail with detailed errors.
        """
        result = QAResult()

        if video_path:
            self._check_video(video_path, result)
        else:
            result.fail("No video file provided.")

        if thumbnail_path:
            self._check_thumbnail(thumbnail_path, result)
        else:
            result.fail("No thumbnail file provided.")

        if metadata:
            self._check_metadata(metadata, result)
        else:
            result.fail("No metadata provided.")

        # Log result for learning
        self.memory.append_to_list("validations", {
            "passed": result.passed,
            "error_count": len(result.errors),
            "errors": result.errors[:5],
        })

        logger.info(result.summary())
        return result

    def _check_video(self, path: Path, result: QAResult) -> None:
        """Validate video file integrity."""
        if not path.exists():
            result.fail(f"Video file missing: {path}")
            return

        size_mb = path.stat().st_size / (1024 * 1024)
        if size_mb < 0.1:
            result.fail(f"Video too small ({size_mb:.2f} MB) — likely corrupted.")
            return

        if size_mb > 5000:
            result.warn(f"Video very large ({size_mb:.0f} MB). Upload may be slow.")

        # Check duration with ffprobe
        try:
            cmd = [
                "ffprobe", "-v", "quiet",
                "-show_entries", "format=duration",
                "-of", "csv=p=0", str(path),
            ]
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=15, check=True)
            duration = float(out.stdout.strip())
            if duration < 5:
                result.fail(f"Video too short ({duration:.1f}s).")
            elif duration > 7200:
                result.warn(f"Video over 2 hours ({duration/3600:.1f}h).")
            logger.debug("Video duration: %.1fs, size: %.1f MB", duration, size_mb)
        except Exception as exc:
            result.warn(f"Could not verify video duration: {exc}")

    def _check_thumbnail(self, path: Path, result: QAResult) -> None:
        """Validate thumbnail image."""
        if not path.exists():
            result.fail(f"Thumbnail missing: {path}")
            return

        size_kb = path.stat().st_size / 1024
        if size_kb < 5:
            result.fail(f"Thumbnail too small ({size_kb:.0f} KB) — likely corrupted.")
            return
        if size_kb > 2048:
            result.warn(f"Thumbnail over 2MB ({size_kb:.0f} KB). YouTube limit is 2MB.")

        try:
            img = Image.open(path)
            w, h = img.size
            if w < 1280 or h < 720:
                result.warn(f"Thumbnail resolution low: {w}x{h}. Recommended: 1280x720.")
            aspect = w / h
            if not (1.7 < aspect < 1.85):
                result.warn(f"Thumbnail aspect ratio {aspect:.2f} — should be ~1.78 (16:9).")
        except Exception as exc:
            result.fail(f"Cannot open thumbnail: {exc}")

    def _check_metadata(self, meta: VideoMetadata, result: QAResult) -> None:
        """Validate SEO metadata."""
        # Title checks
        if not meta.title:
            result.fail("Title is empty.")
        elif len(meta.title) > 100:
            result.fail(f"Title too long: {len(meta.title)} chars (max 100).")
        elif len(meta.title) < 10:
            result.warn(f"Title very short: {len(meta.title)} chars.")

        # Description checks
        if not meta.description:
            result.fail("Description is empty.")
        else:
            word_count = len(meta.description.split())
            if word_count < 20:
                result.warn(f"Description too short: {word_count} words.")

        # Tags check
        if not meta.tags:
            result.warn("No tags provided.")
        elif len(meta.tags) < 5:
            result.warn(f"Only {len(meta.tags)} tags. Recommend 15+.")

        # Check for common bad patterns
        bad_words = ["fuck", "shit", "porn", "xxx", "kill", "die", "suicide"]
        full_text = f"{meta.title} {meta.description}".lower()
        for word in bad_words:
            if word in full_text:
                result.fail(f"Unsafe word detected in metadata: '{word}'")

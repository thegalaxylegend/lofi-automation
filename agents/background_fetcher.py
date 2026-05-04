"""
Background Fetcher — Downloads aesthetic videos from Pexels and Pixabay.

Uses the Director's pexels_search_queries to find matching backgrounds.
Implements a "used backgrounds" tracker to avoid repeats.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

import requests

from core.config import Config, TEMP_DIR

logger = logging.getLogger(__name__)

# Track used video IDs to prevent repeats across pipeline runs
_USED_CACHE_FILE = TEMP_DIR / "used_backgrounds.txt"


def _load_used_ids() -> set[str]:
    if _USED_CACHE_FILE.exists():
        return set(_USED_CACHE_FILE.read_text().strip().splitlines())
    return set()


def _save_used_id(video_id: str) -> None:
    with open(_USED_CACHE_FILE, "a", encoding="utf-8") as fh:
        fh.write(f"{video_id}\n")


class BackgroundFetcher:
    """Fetch aesthetic background videos from Pexels (primary) and Pixabay (fallback)."""

    def __init__(self) -> None:
        cfg = Config()
        self.pexels_key = cfg.pexels_api_key
        self.pixabay_key = cfg.pixabay_api_key
        self._used_ids = _load_used_ids()

    def fetch(
        self,
        search_queries: list[str],
        *,
        min_duration: int = 10,
        orientation: str = "landscape",
    ) -> Path | None:
        """
        Download a background video matching the search queries.

        Tries each query in order, first on Pexels, then Pixabay.
        Skips videos already used (tracked in used_backgrounds.txt).

        Args:
            search_queries: List of search terms from the Director.
            min_duration: Minimum video duration in seconds.
            orientation: "landscape" or "portrait".

        Returns:
            Path to the downloaded video file, or None if all sources fail.
        """
        for query in search_queries:
            # Try Pexels first
            if self.pexels_key:
                result = self._fetch_pexels(query, min_duration, orientation)
                if result:
                    return result

            # Fallback to Pixabay
            if self.pixabay_key:
                result = self._fetch_pixabay(query, min_duration)
                if result:
                    return result

            time.sleep(0.5)  # Gentle rate limiting between queries

        logger.error("BackgroundFetcher: No suitable video found for any query.")
        return None

    # ── Pexels ───────────────────────────────

    def _fetch_pexels(
        self, query: str, min_duration: int, orientation: str
    ) -> Path | None:
        logger.info("Pexels search: '%s'", query)
        try:
            resp = requests.get(
                "https://api.pexels.com/videos/search",
                headers={"Authorization": self.pexels_key},
                params={
                    "query": query,
                    "per_page": 15,
                    "orientation": orientation,
                    "size": "large",
                },
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as exc:
            logger.error("Pexels API error: %s", exc)
            return None

        videos: list[dict[str, Any]] = data.get("videos", [])
        if not videos:
            logger.info("Pexels: no results for '%s'.", query)
            return None

        # Filter: minimum duration + not already used
        for video in videos:
            vid_id = str(video.get("id", ""))
            duration = video.get("duration", 0)

            if vid_id in self._used_ids:
                continue
            if duration < min_duration:
                continue

            # Find the best quality video file (prefer HD)
            download_url = self._best_pexels_file(video.get("video_files", []))
            if not download_url:
                continue

            # Download
            out_path = TEMP_DIR / f"bg_pexels_{vid_id}.mp4"
            if self._download_file(download_url, out_path):
                self._used_ids.add(vid_id)
                _save_used_id(vid_id)
                logger.info("Pexels: downloaded video %s (%ds)", vid_id, duration)
                return out_path

        logger.info("Pexels: all results for '%s' already used or too short.", query)
        return None

    @staticmethod
    def _best_pexels_file(files: list[dict[str, Any]]) -> str:
        """Pick the highest quality video file, preferring 1080p."""
        # Sort by height descending, prefer HD
        hd_files = [f for f in files if f.get("height", 0) >= 1080]
        if hd_files:
            # Pick the one closest to 1080p (not 4K — too large for free CI)
            hd_files.sort(key=lambda f: abs(f.get("height", 0) - 1080))
            return hd_files[0].get("link", "")

        # Fallback: any file with height >= 720
        ok_files = [f for f in files if f.get("height", 0) >= 720]
        if ok_files:
            ok_files.sort(key=lambda f: f.get("height", 0), reverse=True)
            return ok_files[0].get("link", "")

        # Last resort: just pick the first file
        return files[0].get("link", "") if files else ""

    # ── Pixabay ──────────────────────────────

    def _fetch_pixabay(self, query: str, min_duration: int) -> Path | None:
        logger.info("Pixabay search: '%s'", query)
        try:
            resp = requests.get(
                "https://pixabay.com/api/videos/",
                params={
                    "key": self.pixabay_key,
                    "q": query,
                    "per_page": 10,
                    "video_type": "film",
                },
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as exc:
            logger.error("Pixabay API error: %s", exc)
            return None

        hits: list[dict[str, Any]] = data.get("hits", [])
        if not hits:
            logger.info("Pixabay: no results for '%s'.", query)
            return None

        for hit in hits:
            vid_id = f"px_{hit.get('id', '')}"
            duration = hit.get("duration", 0)

            if vid_id in self._used_ids:
                continue
            if duration < min_duration:
                continue

            # Get the large video URL
            videos_dict = hit.get("videos", {})
            large = videos_dict.get("large", {})
            download_url = large.get("url", "")
            if not download_url:
                medium = videos_dict.get("medium", {})
                download_url = medium.get("url", "")
            if not download_url:
                continue

            out_path = TEMP_DIR / f"bg_pixabay_{vid_id}.mp4"
            if self._download_file(download_url, out_path):
                self._used_ids.add(vid_id)
                _save_used_id(vid_id)
                logger.info("Pixabay: downloaded video %s (%ds)", vid_id, duration)
                return out_path

        logger.info("Pixabay: all results for '%s' already used.", query)
        return None

    # ── Download ─────────────────────────────

    @staticmethod
    def _download_file(url: str, dest: Path) -> bool:
        try:
            resp = requests.get(url, stream=True, timeout=60)
            resp.raise_for_status()
            with open(dest, "wb") as fh:
                for chunk in resp.iter_content(chunk_size=1024 * 256):
                    fh.write(chunk)
            size_mb = dest.stat().st_size / (1024 * 1024)
            logger.debug("Downloaded %.1f MB -> %s", size_mb, dest.name)
            return True
        except (requests.RequestException, OSError) as exc:
            logger.error("Download failed: %s", exc)
            return False

"""
Agent 3B: The Video Fetcher — AI Clip Curator.

Multi-platform stock video sourcing with LLM-driven "perfect clip" selection:
  1. Takes video_search_queries from the Director's CreativeBrief
  2. Searches Pexels + Pixabay for candidate clips
  3. Sends metadata to the LLM to pick the best match per section
  4. Downloads only the chosen clips
  5. Falls back to image_fetcher if stock video is unavailable

This replaces image_fetcher.py as the primary visual source.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

import requests

from agents.director import CreativeBrief, SongSection
from core.api_rotation import APIRotator
from core.config import Config, TEMP_DIR
from core.memory import Memory

logger = logging.getLogger(__name__)

# Persistent memory to avoid reusing clips across videos
def video_fetcher_memory() -> Memory:
    return Memory("video_fetcher_clips")


class VideoFetcher:
    """
    AI Clip Curator: searches stock platforms, uses LLM to select
    the best clip for each song section, and downloads them.
    """

    def __init__(self) -> None:
        self.config = Config()
        self.rotator = APIRotator()
        self.memory = video_fetcher_memory()
        self._used_clip_ids: set[str] = set()  # Within-run deduplication
        self._load_historical_clips()

    def _load_historical_clips(self) -> None:
        """Load previously used clip IDs to prevent cross-video repetition."""
        mem = self.memory.load()
        historical = mem.get("used_clip_ids", [])
        self._historical_clip_ids: set[str] = set(historical[-500:])  # Keep last 500

    def fetch_clips(
        self,
        brief: CreativeBrief,
        output_dir: Path | None = None,
    ) -> list[Path]:
        """
        Fetch one curated stock video clip per song section.

        Returns:
            List of paths to downloaded .mp4 files (one per section).
            Falls back to empty list if all sources fail.
        """
        output_dir = output_dir or TEMP_DIR / "stock_clips"
        output_dir.mkdir(parents=True, exist_ok=True)

        # Build search queries from the brief
        queries = self._extract_queries(brief)
        if not queries:
            logger.warning("No video search queries in brief. Returning empty.")
            return []

        downloaded: list[Path] = []

        for idx, (query, section) in enumerate(queries):
            logger.info(
                "Fetching clip %d/%d: query='%s' section='%s'",
                idx + 1, len(queries), query, section.name,
            )

            clip_path = self._fetch_single_clip(
                query=query,
                section=section,
                brief=brief,
                output_path=output_dir / f"clip_{idx:02d}.mp4",
            )

            if clip_path and clip_path.exists():
                downloaded.append(clip_path)
                logger.info("✅ Clip %d downloaded: %s", idx + 1, clip_path.name)
            else:
                logger.warning("❌ Clip %d failed for query='%s'", idx + 1, query)
                downloaded.append(None)  # Placeholder for fallback

        # Save used clips to persistent memory
        self._save_used_clips()

        # Report results
        success = sum(1 for p in downloaded if p is not None)
        logger.info(
            "VideoFetcher: %d/%d clips downloaded successfully.",
            success, len(downloaded),
        )

        return downloaded

    def _extract_queries(
        self, brief: CreativeBrief
    ) -> list[tuple[str, SongSection]]:
        """Extract search queries paired with their sections."""
        queries = []

        if brief.has_sections:
            for i, section in enumerate(brief.sections):
                # Prefer per-section video_search_query, fall back to brief-level list
                query = section.video_search_query
                if not query and i < len(brief.video_search_queries):
                    query = brief.video_search_queries[i]
                if not query:
                    # Last resort: use the section emotion + visual motif
                    motif = brief.narrative_thread.visual_motif or "nature"
                    query = f"{section.emotion} {motif}"
                queries.append((query.strip(), section))

        return queries

    def _fetch_single_clip(
        self,
        query: str,
        section: SongSection,
        brief: CreativeBrief,
        output_path: Path,
    ) -> Path | None:
        """Fetch a single curated clip for one section."""

        # Step 1: Search both platforms for candidates
        candidates = self._search_candidates(query)

        if len(candidates) < 3:
            # Simplify query and retry
            simplified = self._simplify_query(query)
            logger.info("Few results for '%s', retrying with '%s'", query, simplified)
            candidates.extend(self._search_candidates(simplified))

        if not candidates:
            # Use visual motif as last-resort query
            motif = brief.narrative_thread.visual_motif or "cinematic landscape"
            logger.info("No results. Falling back to motif query: '%s'", motif)
            candidates = self._search_candidates(motif)

        if not candidates:
            logger.error("All search attempts failed for query='%s'", query)
            return None

        # Step 2: Filter out already-used clips
        fresh = [
            c for c in candidates
            if c["id"] not in self._used_clip_ids
            and c["id"] not in self._historical_clip_ids
        ]
        
        # Strict anti-repetition: if we must reuse, NEVER use the last two clips (prevents A-A and A-B-A)
        if not fresh:
            recently_used = list(self._used_clip_ids)[-2:] if len(self._used_clip_ids) >= 2 else list(self._used_clip_ids)
            fresh = [c for c in candidates if c["id"] not in recently_used]
            if not fresh:
                fresh = candidates  # Extreme fallback

        # Step 3: LLM selects the best clip
        chosen = self._llm_select_clip(fresh, section, brief)

        if not chosen:
            # Fallback: pick the first available clip
            chosen = fresh[0] if fresh else candidates[0]
            logger.info("LLM selection failed. Using first candidate: %s", chosen["id"])

        # Step 4: Download the chosen clip
        downloaded = self._download_clip(chosen, output_path)

        if downloaded:
            self._used_clip_ids.add(chosen["id"])

        return downloaded

    # ── Search Methods ────────────────────────

    def _search_candidates(self, query: str) -> list[dict]:
        """Search multiple platforms and merge into a candidate pool."""
        candidates = []

        # 1. Pexels (Primary)
        candidates.extend(self._search_pexels(query))

        # 2. Coverr (Secondary)
        if len(candidates) < 8:
            candidates.extend(self._search_coverr(query))

        # 3. Pixabay (Tertiary)
        if len(candidates) < 8:
            candidates.extend(self._search_pixabay(query))

        # 4. Vecteezy (Quaternary)
        if len(candidates) < 8:
            candidates.extend(self._search_vecteezy(query))

        # Deduplicate by ID
        seen = set()
        unique = []
        for c in candidates:
            if c["id"] not in seen:
                seen.add(c["id"])
                unique.append(c)

        # Sort by resolution width to prefer 4K/1080p
        unique.sort(key=lambda x: x.get("width", 0), reverse=True)

        return unique[:15]  # Cap at 15 candidates

    def _search_pexels(self, query: str) -> list[dict]:
        """Search Pexels Video API."""
        api_key = self.config.pexels_api_key
        if not api_key:
            logger.debug("No Pexels API key configured.")
            return []

        try:
            resp = requests.get(
                "https://api.pexels.com/videos/search",
                headers={"Authorization": api_key},
                params={
                    "query": query,
                    "orientation": "landscape",
                    "size": "large",
                    "per_page": 15,
                },
                timeout=15,
            )

            if resp.status_code == 429:
                logger.warning("Pexels rate limited (429). Skipping.")
                return []
            if resp.status_code != 200:
                logger.warning("Pexels returned %d for '%s'", resp.status_code, query)
                return []

            data = resp.json()
            results = []
            for video in data.get("videos", []):
                # Find the best HD file
                hd_file = self._pick_pexels_file(video.get("video_files", []))
                if not hd_file:
                    continue

                # Extract preview thumbnail from video_pictures
                thumb_url = ""
                pics = video.get("video_pictures", [])
                if pics:
                    thumb_url = pics[0].get("picture", "")
                elif video.get("image"):
                    thumb_url = video["image"]

                results.append({
                    "id": f"pexels_{video['id']}",
                    "platform": "pexels",
                    "url": hd_file["link"],
                    "thumbnail_url": thumb_url,
                    "duration": video.get("duration", 0),
                    "width": hd_file.get("width", 0),
                    "height": hd_file.get("height", 0),
                    "tags": "",
                    "description": video.get("url", ""),
                })

            logger.info("Pexels: %d results for '%s'", len(results), query)
            return results

        except requests.RequestException as exc:
            logger.error("Pexels search failed: %s", exc)
            return []

    def _search_pixabay(self, query: str) -> list[dict]:
        """Search Pixabay Video API."""
        api_key = self.config.pixabay_api_key
        if not api_key:
            logger.debug("No Pixabay API key configured.")
            return []

        try:
            resp = requests.get(
                "https://pixabay.com/api/videos/",
                params={
                    "key": api_key,
                    "q": query,
                    "video_type": "film",
                    "min_width": 1920,
                    "per_page": 15,
                },
                timeout=15,
            )

            if resp.status_code != 200:
                logger.warning("Pixabay returned %d for '%s'", resp.status_code, query)
                return []

            data = resp.json()
            results = []
            for hit in data.get("hits", []):
                videos = hit.get("videos", {})
                # Prefer "large" then "medium"
                vid = videos.get("large", videos.get("medium", {}))
                url = vid.get("url", "")
                if not url:
                    continue

                # Pixabay provides preview image URLs
                thumb_url = hit.get("userImageURL", "") or hit.get("pageURL", "")
                # Pixabay tiny preview from videos dict
                tiny = videos.get("tiny", {})
                if tiny.get("thumbnail"):
                    thumb_url = tiny["thumbnail"]

                results.append({
                    "id": f"pixabay_{hit['id']}",
                    "platform": "pixabay",
                    "url": url,
                    "thumbnail_url": thumb_url,
                    "duration": hit.get("duration", 0),
                    "width": vid.get("width", 0),
                    "height": vid.get("height", 0),
                    "tags": hit.get("tags", ""),
                    "description": hit.get("tags", ""),
                })

            logger.info("Pixabay: %d results for '%s'", len(results), query)
            return results

        except requests.RequestException as exc:
            logger.error("Pixabay search failed: %s", exc)
            return []

    def _search_coverr(self, query: str) -> list[dict]:
        """Search Coverr Video API."""
        api_key = self.config.coverr_api_key
        if not api_key:
            return []

        try:
            resp = requests.get(
                "https://api.coverr.co/videos",
                headers={"Authorization": f"Bearer {api_key}"},
                params={"query": query, "urls": "true"},
                timeout=15,
            )
            if resp.status_code != 200:
                return []

            data = resp.json()
            results = []
            for hit in data.get("hits", []):
                urls = hit.get("urls", {})
                url = urls.get("mp4", "")
                if not url:
                    continue
                results.append({
                    "id": f"coverr_{hit['id']}",
                    "platform": "coverr",
                    "url": url,
                    "thumbnail_url": hit.get("thumbnail", ""),
                    "duration": hit.get("duration", 0),
                    "width": 1920,
                    "height": 1080,
                    "tags": ", ".join(hit.get("tags", [])),
                    "description": hit.get("title", ""),
                })
            logger.info("Coverr: %d results for '%s'", len(results), query)
            return results
        except Exception as exc:
            logger.error("Coverr search failed: %s", exc)
            return []

    def _search_vecteezy(self, query: str) -> list[dict]:
        """Search Vecteezy Video API."""
        # Simple bearer token auth if they provided the secret key
        api_key = self.config.vecteezy_secret_key
        if not api_key:
            return []

        try:
            resp = requests.get(
                "https://api.vecteezy.com/v1/videos/search",
                headers={"Authorization": f"Bearer {api_key}"},
                params={"q": query, "page": 1},
                timeout=15,
            )
            if resp.status_code != 200:
                return []

            data = resp.json()
            results = []
            for hit in data.get("data", []):
                preview = hit.get("attributes", {}).get("preview_url", "")
                if not preview:
                    continue
                results.append({
                    "id": f"vecteezy_{hit['id']}",
                    "platform": "vecteezy",
                    "url": preview,  # Vecteezy provides watermarked previews for free tier, but it's a valid fallback
                    "thumbnail_url": hit.get("attributes", {}).get("thumbnail_url", ""),
                    "duration": 10,
                    "width": 1920,
                    "height": 1080,
                    "tags": query,
                    "description": hit.get("attributes", {}).get("title", ""),
                })
            logger.info("Vecteezy: %d results for '%s'", len(results), query)
            return results
        except Exception as exc:
            logger.error("Vecteezy search failed: %s", exc)
            return []

    # ── LLM Selection ─────────────────────────

    def _llm_select_clip(
        self,
        candidates: list[dict],
        section: SongSection,
        brief: CreativeBrief,
    ) -> dict | None:
        """
        Use the LLM to pick the best clip using:
        1. Scene description storyboard (text-based matching)
        2. Preview thumbnail visual verification (image-based matching)
        """
        if not candidates:
            return None

        # ── Step A: Download top 5 thumbnails for visual verification ──
        top_candidates = candidates[:8]  # Limit to 8 for speed
        thumb_dir = TEMP_DIR / "clip_thumbs"
        thumb_dir.mkdir(parents=True, exist_ok=True)

        for c in top_candidates:
            thumb_path = thumb_dir / f"{c['id']}.jpg"
            c["_thumb_path"] = str(thumb_path)
            if not thumb_path.exists():
                self._download_thumbnail(c.get("thumbnail_url", ""), thumb_path)

        # ── Step B: Try visual verification with thumbnails ──
        # Send thumbnails to Gemini to visually match against scene_description
        scene_desc = section.scene_description or section.image_prompt
        chosen = self._visual_verify_clips(top_candidates, section, brief, scene_desc)
        if chosen:
            return chosen

        # ── Step C: Fallback to text-only LLM selection ──
        logger.info("Visual verification didn't select. Trying text-only selection...")
        return self._text_select_clip(candidates, section, brief, scene_desc)

    def _download_thumbnail(self, url: str, output: Path) -> bool:
        """Download a preview thumbnail image from the stock platform."""
        if not url:
            return False
        try:
            resp = requests.get(url, timeout=10)
            if resp.status_code == 200 and len(resp.content) > 1000:
                with open(output, "wb") as f:
                    f.write(resp.content)
                return True
        except Exception:
            pass
        return False

    def _visual_verify_clips(
        self,
        candidates: list[dict],
        section: SongSection,
        brief: CreativeBrief,
        scene_desc: str,
    ) -> dict | None:
        """
        Send preview thumbnails to Gemini for visual scene matching.
        Uses Gemini's image understanding to verify clip content.
        """
        # Find candidates with valid thumbnails
        with_thumbs = [
            c for c in candidates
            if Path(c.get("_thumb_path", "")).exists()
        ]
        if not with_thumbs:
            logger.info("No thumbnails available for visual verification.")
            return None

        # Send up to 3 thumbnails at a time (Gemini limit)
        best_match = None
        best_score = -1

        for batch_start in range(0, min(len(with_thumbs), 6), 3):
            batch = with_thumbs[batch_start:batch_start + 3]
            if not batch:
                break

            # Build the visual verification prompt
            clip_info = "\n".join([
                f"  Clip {i+1} (ID: {c['id']}): {c.get('tags', '')} "
                f"| {c['duration']}s | {c['width']}x{c['height']}"
                for i, c in enumerate(batch)
            ])

            prompt = f"""You are a professional music video editor. I need you to look at these preview thumbnail images from stock video clips and tell me which one BEST matches this scene description.

SCENE DESCRIPTION (this is what the video should show at timestamp {section.start_sec:.0f}s - {section.end_sec:.0f}s):
\"{scene_desc}\"

Song context: {brief.song_comprehension.story_summary or brief.narrative_thread.story_summary}

CLIP METADATA:
{clip_info}

Look at the images carefully. Which clip's visual content best matches the scene description?
Rate each clip 0-10 on how well it matches.

Return ONLY valid JSON:
{{"scores": [{{"id": "<clip_id>", "score": <0-10>, "reason": "<why>"}}], "best_id": "<best clip id>"}}"""

            try:
                # Upload first thumbnail as primary context
                thumb_path = batch[0].get("_thumb_path", "")
                if not thumb_path or not Path(thumb_path).exists():
                    continue

                raw = self.rotator.generate_text_with_media(
                    media_path=thumb_path,
                    prompt=prompt,
                    mime_type="image/jpeg",
                    model="gemini-2.5-flash",
                    temperature=0.2,
                )

                cleaned = raw.strip()
                if cleaned.startswith("```"):
                    lines = cleaned.split("\n")
                    lines = [l for l in lines if not l.strip().startswith("```")]
                    cleaned = "\n".join(lines)

                result = json.loads(cleaned)
                best_id = result.get("best_id", "")

                # Find score of best
                for score_entry in result.get("scores", []):
                    if score_entry.get("id") == best_id:
                        score_val = score_entry.get("score", 0)
                        if score_val > best_score:
                            best_score = score_val
                            # Find the candidate
                            for c in batch:
                                if c["id"] == best_id:
                                    best_match = c
                                    break
                        logger.info(
                            "Visual verify: clip '%s' scored %d/10 — %s",
                            best_id, score_val,
                            score_entry.get("reason", "")[:80],
                        )

            except Exception as exc:
                logger.warning("Visual verification batch failed: %s", exc)
                continue

        if best_match and best_score >= 5:
            logger.info(
                "✅ Visual verification chose '%s' (score: %d/10)",
                best_match["id"], best_score,
            )
            return best_match

        logger.info("Visual verification: no clip scored >= 5/10.")
        return None

    def _text_select_clip(
        self,
        candidates: list[dict],
        section: SongSection,
        brief: CreativeBrief,
        scene_desc: str,
    ) -> dict | None:
        """Fallback: text-only LLM selection using scene_description."""
        clip_summaries = []
        for c in candidates[:10]:
            clip_summaries.append({
                "id": c["id"],
                "platform": c["platform"],
                "duration_sec": c["duration"],
                "resolution": f"{c['width']}x{c['height']}",
                "tags": c.get("tags", ""),
            })

        story = brief.song_comprehension.story_summary or brief.narrative_thread.story_summary

        prompt = f"""You are a professional music video editor selecting stock footage.

SCENE STORYBOARD ({section.start_sec:.0f}s - {section.end_sec:.0f}s):
\"{scene_desc}\"

Song story: {story}
Section: \"{section.name}\" | Emotion: {section.emotion} | Energy: {section.energy}
Must show: {section.primary_subject or 'anything matching the scene'}

AVAILABLE CLIPS:
{json.dumps(clip_summaries, indent=2)}

Pick the ONE clip whose TAGS and CONTENT best match the storyboard above.
- Match the SCENE (objects, people, setting, action), not just mood.
- Prefer clips >= 10 seconds and >= 1080p.

Return ONLY JSON: {{"first_choice_id": "<id>", "second_choice_id": "<id>", "reasoning": "<why>"}}"""

        try:
            raw = self.rotator.generate_text(prompt, temperature=0.3, max_retries=3)
            cleaned = raw.strip()
            if cleaned.startswith("```"):
                lines = cleaned.split("\n")
                lines = [l for l in lines if not l.strip().startswith("```")]
                cleaned = "\n".join(lines)

            result = json.loads(cleaned)
            for key in ["first_choice_id", "second_choice_id"]:
                cid = result.get(key, "")
                for c in candidates:
                    if c["id"] == cid:
                        logger.info("Text selection chose '%s': %s", cid, result.get('reasoning', '')[:80])
                        return c
        except Exception as exc:
            logger.warning("Text-only clip selection failed: %s", exc)

        return None

    # ── Download ──────────────────────────────

    def _download_clip(self, clip: dict, output_path: Path) -> Path | None:
        """Download a clip after verifying the URL is accessible."""
        url = clip.get("url", "")
        if not url:
            return None

        # HEAD check first
        try:
            head = requests.head(url, timeout=10, allow_redirects=True)
            if head.status_code not in (200, 206, 302):
                logger.warning(
                    "Clip %s URL returned %d on HEAD check. Skipping.",
                    clip["id"], head.status_code,
                )
                return None
        except requests.RequestException:
            logger.warning("HEAD check failed for %s. Trying download anyway.", clip["id"])

        # Stream download
        try:
            resp = requests.get(url, stream=True, timeout=60)
            resp.raise_for_status()

            with open(output_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    f.write(chunk)

            # Verify file is valid
            if output_path.stat().st_size < 100_000:  # < 100KB is suspect
                logger.warning("Downloaded clip too small (%d bytes). Removing.", output_path.stat().st_size)
                output_path.unlink(missing_ok=True)
                return None

            size_mb = output_path.stat().st_size / (1024 * 1024)
            logger.info("Downloaded %s: %.1f MB", clip["id"], size_mb)
            return output_path

        except Exception as exc:
            logger.error("Download failed for %s: %s", clip["id"], exc)
            output_path.unlink(missing_ok=True)
            return None

    # ── Helpers ────────────────────────────────

    @staticmethod
    def _pick_pexels_file(video_files: list[dict]) -> dict | None:
        """Pick the best HD file from Pexels video_files array."""
        # Prefer HD (1920x1080), then any >= 1280 wide
        ranked = sorted(
            video_files,
            key=lambda f: (
                1 if f.get("quality") == "hd" else 0,
                f.get("width", 0),
            ),
            reverse=True,
        )
        for f in ranked:
            if f.get("width", 0) >= 1280 and f.get("link"):
                return f
        # Fallback to any available
        return ranked[0] if ranked else None

    @staticmethod
    def _simplify_query(query: str) -> str:
        """Strip adjectives, keep nouns for broader search."""
        words = query.split()
        # Keep only words > 3 chars (likely nouns)
        nouns = [w for w in words if len(w) > 3]
        if len(nouns) >= 2:
            return " ".join(nouns[:2])
        return words[-1] if words else "landscape"

    def _save_used_clips(self) -> None:
        """Save used clip IDs to persistent memory."""
        all_ids = list(self._historical_clip_ids | self._used_clip_ids)
        self.memory.update_key("used_clip_ids", all_ids[-500:])

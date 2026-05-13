"""
Agent 9: The Scout — Competitor Intelligence Agent.

Monitors top music channels weekly to detect:
  - Trending keywords in titles
  - Upload frequency patterns
  - Video length trends
  - Thumbnail color/style shifts

Feeds intelligence to the Marketer and Director for adaptation.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

import requests

from core.api_rotation import APIRotator
from core.config import Config
from core.memory import scout_memory

logger = logging.getLogger(__name__)

# Default competitors to monitor (channel IDs)
DEFAULT_COMPETITORS = [
    {"name": "Lofi Girl", "channel_id": "UCSJ4gkVC6NrvII8umztf0A"},
    {"name": "Chillhop Music", "channel_id": "UCOxqgCwgOqC2lMqC5PYbDgw"},
    {"name": "The Bootleg Boy", "channel_id": "UCjYO25ZVJT523TD1iiYiQ0Q"},
    {"name": "Dreamy", "channel_id": "UCgnVJOaYr-MBq5BPeoRNJDg"},
    {"name": "College Music", "channel_id": "UCWzZ5TIGoZ6o-KtbGCyhnhg"},
]

SCOUT_ANALYSIS_PROMPT = """You are a competitive intelligence analyst for a Hindi music YouTube channel.

Here is data about our top competitors' recent uploads:

{competitor_data}

Analyze this data and return ONLY valid JSON:

{{
  "trending_keywords": ["<top 10 keywords appearing in successful titles>"],
  "trending_moods": ["<top 3 mood/vibe trends>"],
  "optimal_video_length": "<recommended duration based on what's working>",
  "title_patterns": ["<3 title format patterns that are performing well>"],
  "thumbnail_observations": "<what thumbnail styles seem to be trending>",
  "opportunities": ["<3 gaps or opportunities we can exploit>"],
  "threats": ["<2 potential threats or saturated areas to avoid>"],
  "recommendation": "<1-2 sentence strategic recommendation for this week>"
}}
"""


class Scout:
    """
    Monitors competitor channels via YouTube Data API and
    provides strategic intelligence to other agents.
    """

    def __init__(self) -> None:
        self.rotator = APIRotator()
        self.memory = scout_memory()
        self.config = Config()

    def scan(self, youtube_api_key: str = "") -> dict:
        """
        Scan competitor channels and generate an intelligence report.

        Args:
            youtube_api_key: YouTube Data API key for fetching public data.

        Returns:
            Intelligence report dict with trends and recommendations.
        """
        logger.info("Scout: starting competitor scan...")

        mem = self.memory.load()
        competitors = mem.get("competitors", []) or DEFAULT_COMPETITORS

        # Fetch recent videos from each competitor
        all_videos: list[dict] = []
        for comp in competitors:
            if youtube_api_key:
                videos = self._fetch_channel_videos(
                    comp["channel_id"], youtube_api_key, max_results=5
                )
                for v in videos:
                    v["channel_name"] = comp["name"]
                all_videos.extend(videos)
            else:
                logger.info(
                    "No YouTube API key — skipping live scan for %s.", comp["name"]
                )

        if not all_videos:
            logger.warning("Scout: no competitor data available. Using memory.")
            return mem.get("last_report", {})

        # Format data for AI analysis
        competitor_data = self._format_data(all_videos)

        # Generate intelligence report via AI
        prompt = SCOUT_ANALYSIS_PROMPT.format(competitor_data=competitor_data)

        try:
            raw = self.rotator.generate_text(prompt, temperature=0.5, max_retries=2)
            report = self._parse_report(raw)
        except Exception as exc:
            logger.error("Scout AI analysis failed: %s", exc)
            report = {"error": str(exc)}

        # Save to memory
        report["scan_date"] = datetime.now(timezone.utc).isoformat()
        report["videos_analyzed"] = len(all_videos)
        self.memory.update_key("last_report", report)
        self.memory.update_key("last_scan", report["scan_date"])

        # Update trending keywords in memory
        if "trending_keywords" in report:
            self.memory.update_key("trending_keywords", report["trending_keywords"])

        logger.info(
            "✅ Scout report complete: %d videos analyzed from %d channels.",
            len(all_videos), len(competitors),
        )
        return report

    def get_trending_keywords(self) -> list[str]:
        """Quick access to latest trending keywords for other agents."""
        return self.memory.get("trending_keywords", [])

    def _fetch_channel_videos(
        self, channel_id: str, api_key: str, max_results: int = 5
    ) -> list[dict]:
        """Fetch recent videos from a YouTube channel."""
        try:
            resp = requests.get(
                "https://www.googleapis.com/youtube/v3/search",
                params={
                    "key": api_key,
                    "channelId": channel_id,
                    "part": "snippet",
                    "order": "date",
                    "maxResults": max_results,
                    "type": "video",
                },
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()

            videos = []
            for item in data.get("items", []):
                snippet = item.get("snippet", {})
                videos.append({
                    "title": snippet.get("title", ""),
                    "published": snippet.get("publishedAt", ""),
                    "description": snippet.get("description", "")[:200],
                })
            return videos

        except requests.RequestException as exc:
            logger.warning("Failed to fetch videos for channel %s: %s", channel_id, exc)
            return []

    @staticmethod
    def _format_data(videos: list[dict]) -> str:
        """Format video data into a readable string for the AI prompt."""
        lines = []
        for v in videos:
            lines.append(
                f"- [{v.get('channel_name', '?')}] \"{v['title']}\" "
                f"(published: {v.get('published', 'unknown')[:10]})"
            )
        return "\n".join(lines)

    @staticmethod
    def _parse_report(raw: str) -> dict:
        """Parse the AI-generated intelligence report."""
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            lines = cleaned.split("\n")
            lines = [l for l in lines if not l.strip().startswith("```")]
            cleaned = "\n".join(lines)

        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            logger.error("Scout failed to parse AI report.")
            return {"raw_report": raw[:500], "parse_error": True}

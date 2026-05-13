"""
Agent 3: The Marketer — SEO, Titles, and Lore-Fi Descriptions.

Generates YouTube-optimized titles, rich "Lore-Fi" story descriptions,
and hashtags based on the Director's CreativeBrief and learned keyword
performance data from memory.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from core.api_rotation import APIRotator
from core.config import Config
from core.memory import marketer_memory

logger = logging.getLogger(__name__)


@dataclass
class VideoMetadata:
    """Final SEO package ready for YouTube upload."""

    title: str = ""
    description: str = ""
    tags: list[str] = field(default_factory=list)
    category_id: str = "10"  # YouTube category: Music
    thumbnail_prompt: str = ""
    shorts_text: str = ""

    def to_file(self, path: Path) -> None:
        """Save metadata to a text file for manual review."""
        content = (
            f"TITLE: {self.title}\n\n"
            f"DESCRIPTION:\n{self.description}\n\n"
            f"TAGS: {', '.join(self.tags)}\n\n"
            f"CATEGORY: {self.category_id}\n\n"
            f"THUMBNAIL_PROMPT: {self.thumbnail_prompt}\n\n"
            f"SHORTS_TEXT: {self.shorts_text}\n"
        )
        path.write_text(content, encoding="utf-8")
        logger.info("Metadata saved to %s", path.name)

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "description": self.description,
            "tags": self.tags,
            "category_id": self.category_id,
            "thumbnail_prompt": self.thumbnail_prompt,
            "shorts_text": self.shorts_text,
        }


MARKETER_PROMPT = """You are the Marketing Director for a premium Hindi music YouTube channel called "{channel_name}".
Tagline: "{tagline}"
Target Audience: {audience}

You've been given a creative brief from the audio Director:
- Mood: {mood}
- Energy: {energy}
- Emotional Tone: {emotional_tone}
- Visual Style: {visual_style}
- Suggested Keywords: {keywords}

{learned_rules}

Generate the following as ONLY valid JSON (no markdown, no explanation):

{{
  "title": "<YouTube title, max {title_length} chars. Must be catchy, emotional, SEO-optimized. Use power words. Include relevant emoji.>",
  "description": "<Full YouTube description with these sections:
    1. Opening hook (1 emotional sentence)
    2. 'Lore-Fi' story (2 paragraphs — a mini journal entry or scene that matches the mood. Write as if the listener is living the moment. Make it deeply relatable for students.)
    3. Call to action (subscribe, like, share)
    4. Credits line
    5. Hashtags (10-15 relevant hashtags on separate line)>",
  "tags": ["<15-20 SEO tags>"],
  "thumbnail_prompt": "<Detailed AI image generation prompt for the thumbnail. Describe a vivid scene matching the mood. No text in the image. Cinematic, atmospheric, 4K quality.>",
  "shorts_text": "<A short, punchy overlay text for the YouTube Short version. Max 10 words. POV style or relatable student moment.>"
}}

Rules:
- Title MUST be under {title_length} characters including emoji.
- Description must be at least {desc_min_words} words.
- Tags should include both broad and niche keywords.
- The Lore-Fi story should feel like a diary entry — deeply personal and atmospheric.
- Shorts text should be the kind of text that makes someone stop scrolling.
"""


class Marketer:
    """
    Generates YouTube-optimized metadata using AI, enhanced
    by learned keyword performance data.
    """

    def __init__(self) -> None:
        self.rotator = APIRotator()
        self.memory = marketer_memory()
        self.config = Config()

    def generate_metadata(
        self,
        mood: str,
        energy: str,
        emotional_tone: str,
        visual_style: str,
        title_keywords: list[str],
        thumbnail_prompt_hint: str = "",
    ) -> VideoMetadata:
        """
        Generate complete YouTube metadata from the Director's brief.

        Returns:
            VideoMetadata with title, description, tags, and prompts.
        """
        channel = self.config.channel
        learned_rules = self._get_learned_rules()

        prompt = MARKETER_PROMPT.format(
            channel_name=channel.name,
            tagline=channel.tagline,
            audience=channel.audience,
            mood=mood,
            energy=energy,
            emotional_tone=emotional_tone,
            visual_style=visual_style,
            keywords=", ".join(title_keywords),
            learned_rules=learned_rules,
            title_length=channel.seo.target_title_length,
            desc_min_words=channel.seo.description_min_words,
        )

        logger.info("Marketer generating metadata for mood=%s, energy=%s...", mood, energy)

        # Store mood/energy for fallback use
        self._last_mood = mood
        self._last_energy = energy

        raw = self.rotator.generate_text(
            prompt,
            temperature=0.8,
            max_retries=3,
        )

        metadata = self._parse_response(raw)

        # Merge default tags from brand config
        all_tags = list(set(metadata.tags + channel.seo.default_tags))
        metadata.tags = all_tags[:25]  # YouTube max is ~500 chars total

        # Use Director's thumbnail prompt if Marketer's is weak
        if thumbnail_prompt_hint and len(metadata.thumbnail_prompt) < 30:
            metadata.thumbnail_prompt = thumbnail_prompt_hint

        logger.info("Marketer: title='%s' (%d chars)", metadata.title, len(metadata.title))
        return metadata

    def _get_learned_rules(self) -> str:
        """Pull learned keyword/title rules from memory."""
        mem = self.memory.load()
        rules_parts: list[str] = []

        # Power words that work
        power_words = mem.get("power_words", [])
        if power_words:
            rules_parts.append(
                f"PROVEN power words to use in titles: {', '.join(power_words[-10:])}"
            )

        # Words to avoid
        avoid = mem.get("avoid_words", [])
        if avoid:
            rules_parts.append(
                f"Words to AVOID (low CTR): {', '.join(avoid[-10:])}"
            )

        # Thumbnail style preferences
        thumb_perf = mem.get("thumbnail_performance", [])
        if thumb_perf:
            recent = thumb_perf[-3:]
            best = max(recent, key=lambda x: x.get("ctr", 0), default=None)
            if best:
                rules_parts.append(
                    f"Best performing thumbnail style: {best.get('style', 'N/A')} "
                    f"(CTR: {best.get('ctr', 'N/A')}%)"
                )

        if rules_parts:
            return "LEARNED RULES FROM PAST PERFORMANCE:\n" + "\n".join(f"- {r}" for r in rules_parts)
        return ""

    def _parse_response(self, raw: str) -> VideoMetadata:
        """Parse the LLM JSON response into VideoMetadata."""
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            lines = cleaned.split("\n")
            lines = [l for l in lines if not l.strip().startswith("```")]
            cleaned = "\n".join(lines)

        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError:
            logger.error("Marketer failed to parse LLM response. Using defaults.")
            logger.debug("Raw: %s", raw[:500])
            # Use mood-aware fallback instead of hardcoded lo-fi
            mood = getattr(self, '_last_mood', 'chill')
            energy = getattr(self, '_last_energy', 'low')
            fallback_title = f"{mood.capitalize()} Vibes 🎶 | {self.config.channel.name}"
            fallback_tags = [mood, energy, "hindi music", "mood wire", "music"]
            return VideoMetadata(
                title=fallback_title,
                description=f"{mood.capitalize()} music to match your mood.",
                tags=fallback_tags,
            )

        return VideoMetadata(
            title=data.get("title", f"Mood Wire 🎶")[:100],
            description=data.get("description", ""),
            tags=data.get("tags", []),
            thumbnail_prompt=data.get("thumbnail_prompt", ""),
            shorts_text=data.get("shorts_text", ""),
        )

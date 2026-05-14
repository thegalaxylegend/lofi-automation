"""
Agent 3: The Marketer — SEO, Titles, and Lore-Fi Descriptions.

Generates YouTube-optimized titles, rich "Lore-Fi" story descriptions,
and hashtags based on the Director's CreativeBrief and learned keyword
performance data from memory.

UPGRADED: Now includes anti-repetition system that tracks past titles
and enforces structural variety across videos for YouTube growth.
"""

from __future__ import annotations

import json
import logging
import random
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


# ──────────────────────────────────────────────
#  Title Format Rotation System
# ──────────────────────────────────────────────
TITLE_FORMATS = [
    'Lyric Quote: Use an actual Hindi lyric/phrase from the song in quotes, e.g. "Tujhe Kitna Chahne Lage" 💔 Midnight Feels',
    'POV Hook: Start with "POV:" and a relatable scenario, e.g. POV: You finally let go 🌧️ Hindi Sad Mix',
    'Question Style: Ask a question that triggers curiosity, e.g. Why does 2 AM feel like a whole different world? 🌙',
    'One-Word Punch: Lead with ONE bold emotional word, e.g. HEARTBREAK. | The Playlist You Need Tonight',
    'Story Hook: Paint a micro-scene, e.g. Empty hostel room, one song on repeat 🎧 Lo-Fi Hindi',
    'Mood Label: Direct mood labeling with emoji, e.g. 🌧️ Monsoon Melancholy | Hindi Rain Beats',
    'Song Identity: Feature the song name prominently, e.g. Aaj Ki Raat 🎶 | Party Anthem Hindi Dance Mix',
    'Dare/Challenge: Create urgency, e.g. Play this at 3 AM and try not to feel something 🥀',
    'Sensation Style: Describe a feeling, e.g. That bittersweet ache when the last note fades 💫',
    'List/Number: Use a number hook, e.g. 5 Minutes of Pure Calm 🕊️ Hindi Lo-Fi for Exam Nights',
]


MARKETER_PROMPT = """You are the Marketing Director for a premium Hindi music YouTube channel called "{channel_name}".
Tagline: "{tagline}"
Target Audience: {audience}

You've been given a creative brief from the audio Director:
- Mood: {mood}
- Energy: {energy}
- Emotional Tone: {emotional_tone}
- Visual Style: {visual_style}
- Suggested Keywords: {keywords}

{song_identity}

{learned_rules}

{anti_repetition}

TITLE FORMAT INSTRUCTION:
You MUST use this specific title format for THIS video:
→ {title_format}

Generate the following as ONLY valid JSON (no markdown, no explanation):

{{
  "title": "<YouTube title, max {title_length} chars. MUST follow the title format instruction above. Must be catchy, emotional, SEO-optimized. Use power words. Include relevant emoji. If song name is available, incorporate it naturally.>",
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
- Title MUST be UNIQUE and DIFFERENT from all past titles listed above.
- Do NOT start with "3 AM" — find fresh, creative angles.
- If song_dna provides a detected_title, use the actual song name in the title.
"""


class Marketer:
    """
    Generates YouTube-optimized metadata using AI, enhanced
    by learned keyword performance data and anti-repetition system.
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
        song_dna: dict | None = None,
        past_titles: list[str] | None = None,
    ) -> VideoMetadata:
        """
        Generate complete YouTube metadata from the Director's brief.

        Args:
            song_dna: Song identity dict (detected_title, genre, language, feeling)
            past_titles: List of previously generated titles for anti-repetition

        Returns:
            VideoMetadata with title, description, tags, and prompts.
        """
        channel = self.config.channel
        learned_rules = self._get_learned_rules()

        # Build song identity section
        song_identity = self._build_song_identity(song_dna)

        # Build anti-repetition section
        anti_repetition = self._build_anti_repetition(past_titles)

        # Pick a random title format (avoid recent ones)
        title_format = self._pick_title_format()

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
            song_identity=song_identity,
            anti_repetition=anti_repetition,
            title_format=title_format,
        )

        logger.info("Marketer generating metadata for mood=%s, energy=%s...", mood, energy)

        # Store mood/energy for fallback use
        self._last_mood = mood
        self._last_energy = energy

        raw = self.rotator.generate_text(
            prompt,
            temperature=0.85,  # Slightly higher temp for more creative titles
            max_retries=3,
        )

        metadata = self._parse_response(raw)

        # Merge default tags from brand config
        all_tags = list(set(metadata.tags + channel.seo.default_tags))
        metadata.tags = all_tags[:25]  # YouTube max is ~500 chars total

        # Add song-specific tags if available
        if song_dna:
            song_tags = self._extract_song_tags(song_dna)
            metadata.tags = list(set(metadata.tags + song_tags))[:25]

        # Use Director's thumbnail prompt if Marketer's is weak
        if thumbnail_prompt_hint and len(metadata.thumbnail_prompt) < 30:
            metadata.thumbnail_prompt = thumbnail_prompt_hint

        # Record which title format was used
        self.memory.update_key("last_title_format", title_format)

        logger.info("Marketer: title='%s' (%d chars)", metadata.title, len(metadata.title))
        return metadata

    def _build_song_identity(self, song_dna: dict | None) -> str:
        """Build a song identity section for the prompt."""
        if not song_dna:
            return ""

        parts = ["SONG IDENTITY (use this to make the title SPECIFIC to this song):"]
        if song_dna.get("detected_title"):
            parts.append(f"  - Song Name: {song_dna['detected_title']}")
        if song_dna.get("genre"):
            parts.append(f"  - Genre: {song_dna['genre']}")
        if song_dna.get("language"):
            parts.append(f"  - Language: {song_dna['language']}")
        if song_dna.get("overall_feeling"):
            parts.append(f"  - Feeling: {song_dna['overall_feeling']}")

        return "\n".join(parts)

    def _build_anti_repetition(self, past_titles: list[str] | None) -> str:
        """Build anti-repetition instructions from past title history."""
        if not past_titles:
            return ""

        titles_list = "\n".join(f"  - \"{t}\"" for t in past_titles[-10:])
        return (
            f"⚠️ TITLE DIVERSITY — CRITICAL:\n"
            f"Here are our RECENT titles. You MUST NOT repeat these patterns:\n"
            f"{titles_list}\n\n"
            f"Rules:\n"
            f"- Do NOT start with the same word as any recent title.\n"
            f"- Do NOT use the same emoji combination as any recent title.\n"
            f"- Each title must feel like a COMPLETELY DIFFERENT video.\n"
            f"- Use the actual song name/lyrics when available.\n"
            f"- Vary emotional hooks: sometimes mysterious, sometimes direct, sometimes poetic."
        )

    def _pick_title_format(self) -> str:
        """Pick a title format, avoiding the most recently used one."""
        mem = self.memory.load()
        last_format = mem.get("last_title_format", "")

        # Filter out the last used format
        available = [f for f in TITLE_FORMATS if f != last_format]
        if not available:
            available = TITLE_FORMATS

        return random.choice(available)

    @staticmethod
    def _extract_song_tags(song_dna: dict) -> list[str]:
        """Extract SEO tags from song DNA."""
        tags = []
        if song_dna.get("detected_title"):
            # Split song name into tag-friendly parts
            name = song_dna["detected_title"]
            tags.append(name)
            tags.extend(name.lower().split())
        if song_dna.get("genre"):
            tags.append(song_dna["genre"])
        if song_dna.get("language"):
            tags.append(f"{song_dna['language']} music")
        return [t.strip() for t in tags if t.strip()]

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

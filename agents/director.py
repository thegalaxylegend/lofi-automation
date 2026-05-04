"""
Agent 1: The Director — Audio Analysis Agent.

Listens to an MP3 file via Gemini's native audio understanding.
Extracts mood, BPM, energy, instruments, and generates a creative
brief that tells every downstream agent exactly what to produce.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from core.api_rotation import APIRotator
from core.memory import director_memory

logger = logging.getLogger(__name__)

ANALYSIS_PROMPT = """You are the Creative Director for a premium lo-fi YouTube channel.

Listen to this audio track carefully. Analyze every aspect of it.

Return ONLY valid JSON with this exact structure (no markdown, no explanation):

{
  "mood": "<primary mood: one of melancholic, energetic, peaceful, nostalgic, dark, dreamy, romantic, anxious>",
  "secondary_mood": "<secondary mood if mixed, or null>",
  "bpm_estimate": <estimated BPM as integer>,
  "energy": "<low, medium, high>",
  "instruments": ["<list of detected instruments/elements>"],
  "emotional_tone": "<1-2 sentence description of the emotional feeling>",
  "visual_style": "<recommended visual aesthetic: e.g. rainy tokyo street, cozy study room, starry rooftop>",
  "color_palette": ["<hex color 1>", "<hex color 2>", "<hex color 3>"],
  "visualizer_intensity": "<subtle, moderate, intense — how reactive should the audio visualizer be>",
  "text_overlay_suggestion": "<a short poetic/relatable line for the video, e.g. 'for when it's 3am and the exam is tomorrow'>",
  "pexels_search_queries": ["<search query 1>", "<search query 2>", "<search query 3>"],
  "thumbnail_prompt": "<detailed prompt for AI image generation for the thumbnail>",
  "title_keywords": ["<keyword1>", "<keyword2>", "<keyword3>"]
}

Rules:
- BPM: estimate based on the tempo you hear.
- Color palette: choose colors that match the mood for video color grading.
- Pexels queries: suggest 3 different search terms for finding background videos.
- Thumbnail prompt: describe a scene that captures the mood (no text in the image).
- Be specific and creative. This brief drives the entire video production.
"""


@dataclass
class CreativeBrief:
    """Structured output from the Director's audio analysis."""

    mood: str = "peaceful"
    secondary_mood: str | None = None
    bpm_estimate: int = 90
    energy: str = "low"
    instruments: list[str] = field(default_factory=list)
    emotional_tone: str = ""
    visual_style: str = "cozy study room"
    color_palette: list[str] = field(default_factory=lambda: ["#1a1a2e", "#4a3d8f", "#6c3ce1"])
    visualizer_intensity: str = "subtle"
    text_overlay_suggestion: str = ""
    pexels_search_queries: list[str] = field(default_factory=lambda: ["lo-fi aesthetic", "rainy window", "cozy room"])
    thumbnail_prompt: str = ""
    title_keywords: list[str] = field(default_factory=list)
    source_file: str = ""

    def to_dict(self) -> dict:
        return {
            "mood": self.mood,
            "secondary_mood": self.secondary_mood,
            "bpm_estimate": self.bpm_estimate,
            "energy": self.energy,
            "instruments": self.instruments,
            "emotional_tone": self.emotional_tone,
            "visual_style": self.visual_style,
            "color_palette": self.color_palette,
            "visualizer_intensity": self.visualizer_intensity,
            "text_overlay_suggestion": self.text_overlay_suggestion,
            "pexels_search_queries": self.pexels_search_queries,
            "thumbnail_prompt": self.thumbnail_prompt,
            "title_keywords": self.title_keywords,
            "source_file": self.source_file,
        }


class Director:
    """
    Analyzes an audio file and produces a CreativeBrief that drives
    all downstream agents (Video Editor, Marketer, Thumbnail Creator).
    """

    def __init__(self) -> None:
        self.rotator = APIRotator()
        self.memory = director_memory()

    def analyze(self, audio_path: str | Path) -> CreativeBrief:
        """
        Listen to an MP3 and return a structured CreativeBrief.

        Args:
            audio_path: Path to the MP3 file.

        Returns:
            CreativeBrief with mood, colors, visual style, etc.
        """
        audio_path = Path(audio_path)
        if not audio_path.exists():
            raise FileNotFoundError(f"Audio file not found: {audio_path}")

        logger.info("Director analyzing: %s", audio_path.name)

        # Enhance prompt with learned preferences from memory
        prompt = self._build_prompt()

        # Send audio to Gemini for analysis
        raw_response = self.rotator.generate_text_with_media(
            media_path=str(audio_path),
            prompt=prompt,
            mime_type="audio/mpeg",
            model="gemini-2.0-flash",
            temperature=0.6,
        )

        # Parse the JSON response
        brief = self._parse_response(raw_response, str(audio_path))

        logger.info(
            "Director brief: mood=%s, energy=%s, style=%s",
            brief.mood, brief.energy, brief.visual_style,
        )

        return brief

    def _build_prompt(self) -> str:
        """Enhance the base prompt with learned rules from memory."""
        base = ANALYSIS_PROMPT
        mem_data = self.memory.load()

        # If the Analyst has logged performance rules, inject them
        perf_log = mem_data.get("performance_log", [])
        if perf_log:
            # Get the last 5 performance entries
            recent = perf_log[-5:]
            rules_text = "\n".join(
                f"- {entry.get('rule', '')}" for entry in recent if entry.get("rule")
            )
            if rules_text:
                base += (
                    f"\n\nIMPORTANT — Learned rules from past performance data:\n"
                    f"{rules_text}\n"
                    f"Apply these rules when making your creative decisions."
                )

        return base

    def _parse_response(self, raw: str, source_file: str) -> CreativeBrief:
        """Parse the LLM JSON response into a CreativeBrief."""
        # Strip markdown code fences if present
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            # Remove ```json and closing ```
            lines = cleaned.split("\n")
            lines = [l for l in lines if not l.strip().startswith("```")]
            cleaned = "\n".join(lines)

        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError:
            logger.error("Director failed to parse LLM response. Using defaults.")
            logger.debug("Raw response: %s", raw[:500])
            return CreativeBrief(source_file=source_file)

        return CreativeBrief(
            mood=data.get("mood", "peaceful"),
            secondary_mood=data.get("secondary_mood"),
            bpm_estimate=int(data.get("bpm_estimate", 90)),
            energy=data.get("energy", "low"),
            instruments=data.get("instruments", []),
            emotional_tone=data.get("emotional_tone", ""),
            visual_style=data.get("visual_style", "cozy study room"),
            color_palette=data.get("color_palette", ["#1a1a2e", "#4a3d8f", "#6c3ce1"]),
            visualizer_intensity=data.get("visualizer_intensity", "subtle"),
            text_overlay_suggestion=data.get("text_overlay_suggestion", ""),
            pexels_search_queries=data.get("pexels_search_queries", ["lo-fi aesthetic"]),
            thumbnail_prompt=data.get("thumbnail_prompt", ""),
            title_keywords=data.get("title_keywords", []),
            source_file=source_file,
        )

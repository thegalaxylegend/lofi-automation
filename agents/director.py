"""
Agent 1: The Director — AI Creative Engine.

Listens to an MP3 via Gemini's native audio understanding and produces
a COMPLETE CREATIVE BRIEF — a 10-dimension analysis that drives every
downstream agent with song-specific, section-by-section instructions.

This is the BRAIN of the entire system. Every other agent reads this brief.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from pydantic import BaseModel, Field, field_validator, ValidationError
from typing import Any

from core.api_rotation import APIRotator
from core.memory import director_memory

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────
#  The Complete Creative Director Prompt
# ──────────────────────────────────────────────
ANALYSIS_PROMPT = """You are the Creative Director for "Mood Wire" — a premium Hindi music YouTube channel. Your audience is 16-24 year old Indians who connect deeply with Hindi music across ALL genres — lo-fi study beats, emotional ballads, party anthems, devotional, romantic, festive (Holi/Diwali), hip-hop, and more.

Listen to this ENTIRE audio track deeply. Feel its emotional journey — not just one mood, but how the emotion EVOLVES from start to finish.

You must produce a COMPLETE CREATIVE BRIEF. Every editor, thumbnail artist, and marketer on the team will use YOUR brief as their only guide.

Current date: {current_date}
Season context: {season_context}

Return ONLY valid JSON with this EXACT structure:

{{
  "song_dna": {{
    "detected_title": "<title if vocals contain it, or descriptive name>",
    "language": "<hindi/english/mixed/instrumental>",
    "genre": "<e.g., emotional ballad, lo-fi hip hop, motivational, romantic>",
    "overall_feeling": "<one evocative sentence capturing the song's soul>",
    "duration_seconds": <estimated total duration as integer>
  }},

  "mood": "<primary mood: melancholic, energetic, peaceful, nostalgic, dark, dreamy, romantic, anxious>",
  "secondary_mood": "<secondary mood if mixed, or null>",
  "emotional_tone": "<evocative 3-5 word description of the specific emotional character>",
  "bpm_estimate": <estimated BPM as integer>,
  "energy": "<low/medium/high>",
  "instruments": ["<detected instruments/elements>"],

  "emotional_journey": {{
    "arc_type": "<despair_to_hope/building_intensity/calm_throughout/emotional_rollercoaster/slow_burn>",
    "arc_description": "<2-3 sentences describing how the emotion transforms>"
  }},

  "narrative_thread": {{
    "story_summary": "<The visual story connecting ALL images, e.g. 'A student journey through a lonely exam night, from isolation to finding hope in sunrise'>",
    "visual_motif": "<One recurring element in every image: rain, city lights, empty spaces, windows>"
  }},

  "sections": [
    {{
      "name": "<intro/verse_1/pre_chorus/chorus/verse_2/bridge/outro>",
      "start_sec": <float>,
      "end_sec": <float>,
      "energy": "<very_low/low/medium/high/very_high/fading>",
      "emotion": "<specific emotion for THIS section>",
      "musical_elements": "<what instruments/sounds are active here>",

      "image_prompt": "<VERY detailed AI image prompt, minimum 40 words. MUST START WITH the exact visual_style declared above (e.g. 'anime lo-fi illustration style, ...' or 'cinematic photo-realistic style, ...'). Include: culturally appropriate Indian elements matching THIS song's genre and mood (e.g. hostel room for study songs, club/stage for party songs, temple for devotional, colors/gulal for Holi, diyas for Diwali, rain/chai for monsoon moods). Include: lighting, color palette, atmosphere, subject/character, camera angle. The visual_motif MUST appear in this prompt. ALL section prompts MUST use the SAME art style.>",

      "color_grade": {{
        "brightness": <-0.1 to 0.1>,
        "contrast": <0.8 to 1.3>,
        "saturation": <0.5 to 1.4>,
        "red_shift": <-0.1 to 0.1>,
        "green_shift": <-0.1 to 0.1>,
        "blue_shift": <-0.1 to 0.1>
      }},

      "zoom": {{
        "direction": "<zoom_in/zoom_out/pan_left/pan_right/drift_diagonal/breathing/ken_burns_tl_br/ken_burns_br_tl/drift_up/static — VARY this across sections, never use the same direction for adjacent sections>",
        "speed": "<very_slow/slow/medium/fast>"
      }},

      "grain_intensity": <0 to 6>,

      "text_overlay": {{
        "text": "<evocative Hindi or English text, or empty string if none>",
        "appear_at_sec": <float or 0>,
        "duration_sec": <float or 0>,
        "position": "<center/center_bottom/top_center>"
      }}
    }}
  ],

  "transitions": [
    {{
      "from_section": "<name>",
      "to_section": "<name>",
      "type": "<slow_dissolve/fast_dissolve/fade_through_black/direct_cut>",
      "duration_sec": <0.5 to 4.0>
    }}
  ],

  "shorts": {{
    "recommended_start_sec": <float>,
    "recommended_end_sec": <float>,
    "duration_sec": <int, 15-58>,
    "reasoning": "<why this segment is the best hook>",
    "hook_text": "<POV text for first 1.5 seconds, relatable to Indian students>",
    "mood_text": "<Hindi emotional text for middle of Short>",
    "image_section": "<which section's image to use>"
  }},

  "thumbnail": {{
    "best_section": "<which section's image makes the best thumbnail>",
    "thumbnail_prompt": "<separate detailed prompt for thumbnail image — more vibrant, more contrast, eye-catching at small size, no text in the image>",
    "suggested_title_text": "<bold Hindi text for the thumbnail overlay>",
    "text_color": "<hex color for title text>",
    "text_glow_color": "<hex color for glow>"
  }},

  "visual_style": "<ONE specific art style that ALL images MUST use. Choose ONE: 'anime lo-fi illustration' OR 'cinematic photo-realistic' OR 'digital painting' OR 'watercolor illustration' OR 'moody film photography'. NEVER mix styles — every image in the video must look like it was drawn by the SAME artist>",
  "color_palette": ["<hex1>", "<hex2>", "<hex3>"],
  "text_overlay_suggestion": "<best single poetic line from the song>",
  "image_prompts": ["<prompt1>", "<prompt2>", "<prompt3>", "<prompt4>", "<prompt5>"],
  "thumbnail_prompt": "<detailed thumbnail generation prompt>",
  "title_keywords": ["<keyword1>", "<keyword2>", "<keyword3>"],
  "visualizer_intensity": "subtle"
}}

CRITICAL RULES:
- Return ONLY valid JSON. No markdown. No explanation.
- The "sections" array must cover the ENTIRE song duration with no gaps.
- Each section's color_grade must be DIFFERENT — no two sections look the same.
- Each image_prompt MUST include the visual_motif element.
- Include Indian cultural elements in image prompts when appropriate.
- The shorts segment should be the ACTUAL best hook, not always the middle.
- The "image_prompts" array must have exactly 5 prompts matching the sections (one per section, pick the best 5 if more sections exist).
- Be SPECIFIC to THIS song. Do not give generic responses.
- STYLE CONSISTENCY IS CRITICAL: ALL image_prompts across ALL sections MUST use the EXACT SAME art style declared in visual_style. If visual_style is 'anime lo-fi illustration', EVERY section prompt must start with that style. NEVER generate one section as photo-realistic and another as anime — this creates a jarring, amateur slideshow effect.
- Each section's image_prompt must show a DIFFERENT scene/composition but in the SAME art style. Vary the subject, angle, and lighting — NOT the rendering style.
- The thumbnail_prompt should match the visual_style used in the video for brand consistency.
- COMPOSITION VARIETY IS CRITICAL: Each section's image_prompt MUST show a DIFFERENT camera shot type. Cycle through: wide establishing shot → medium shot → close-up detail → bird's eye view → profile silhouette. NEVER repeat the same framing/composition in adjacent sections.
- NEVER let adjacent sections show the same subject in the same pose. If section 1 has 'person sitting at desk', section 2 MUST show something different (e.g., 'window with rain', 'empty hallway', 'hands on book').
- EMOTIONAL PROGRESSION: The visual intensity must BUILD across the song. Start with wider, calmer compositions. Build to tighter, more emotionally intense shots at the chorus/climax. End with a wide shot that provides closure.
- SECTION DURATION: No section should exceed 20 seconds. Break long musical sections into 2-3 visual sub-sections with distinct compositions to prevent visual stagnation.
- TEXT OVERLAYS: Add text_overlay to only 2-3 key emotional moments, not every section. Make each text evocative and impactful — a poetic Hindi line that viewers will screenshot.
- ZOOM VARIETY: Use DIFFERENT zoom directions across sections. Never use 'zoom_in' for more than 2 consecutive sections. Use 'breathing' for emotional sections, 'drift_diagonal' or 'ken_burns' for establishing shots, 'pan_left'/'pan_right' for narrative movement.
- The STRONGEST visual shot should appear near the song's emotional climax (usually chorus), not at the beginning.
"""


# ──────────────────────────────────────────────
#  Data Models
# ──────────────────────────────────────────────

class SectionColorGrade(BaseModel):
    brightness: float = 0.0
    contrast: float = 1.0
    saturation: float = 0.9
    red_shift: float = 0.0
    green_shift: float = 0.0
    blue_shift: float = 0.0

class SectionZoom(BaseModel):
    direction: str = "zoom_in"
    speed: str = "slow"

class SectionTextOverlay(BaseModel):
    text: str = ""
    appear_at_sec: float = 0
    duration_sec: float = 0
    position: str = "center"

class SongSection(BaseModel):
    name: str = "intro"
    start_sec: float = 0
    end_sec: float = 30
    energy: str = "low"
    emotion: str = "peaceful"
    musical_elements: str = ""
    image_prompt: str = ""
    color_grade: SectionColorGrade = Field(default_factory=SectionColorGrade)
    zoom: SectionZoom = Field(default_factory=SectionZoom)
    grain_intensity: int = 3
    text_overlay: SectionTextOverlay = Field(default_factory=SectionTextOverlay)

class SongTransition(BaseModel):
    from_section: str = ""
    to_section: str = ""
    type: str = "slow_dissolve"
    duration_sec: float = 1.5

class ShortsDirective(BaseModel):
    recommended_start_sec: float = 0
    recommended_end_sec: float = 40
    duration_sec: int = 40
    reasoning: str = ""
    hook_text: str = ""
    mood_text: str = ""
    image_section: str = "chorus"

class ThumbnailDirective(BaseModel):
    best_section: str = "chorus"
    thumbnail_prompt: str = ""
    suggested_title_text: str = ""
    text_color: str = "#FFFFFF"
    text_glow_color: str = "#FF6B9D"

class EmotionalJourney(BaseModel):
    arc_type: str = "slow_burn"
    arc_description: str = ""

class NarrativeThread(BaseModel):
    story_summary: str = ""
    visual_motif: str = "rain"

class SongDNA(BaseModel):
    detected_title: str = ""
    language: str = "hindi"
    genre: str = "emotional ballad"
    overall_feeling: str = ""
    duration_seconds: int = 120


class CreativeBrief(BaseModel):
    """The complete 10-dimension creative brief from the Director."""

    # Legacy fields (backward compatible with old code)
    mood: str = "peaceful"
    secondary_mood: str | None = None
    emotional_tone: str = ""
    bpm_estimate: int = 90
    energy: str = "low"
    instruments: list[str] = Field(default_factory=list)
    visual_style: str = "cinematic atmospheric"
    color_palette: list[str] = Field(default_factory=lambda: ["#1a1a2e", "#4a3d8f", "#6c3ce1"])
    visualizer_intensity: str = "subtle"
    text_overlay_suggestion: str = ""
    image_prompts: list[str] = Field(default_factory=lambda: ["cinematic atmospheric scene, detailed, 4k"] * 5)
    thumbnail_prompt: str = ""
    title_keywords: list[str] = Field(default_factory=list)
    source_file: str = ""

    # New Creative Engine fields
    song_dna: SongDNA = Field(default_factory=SongDNA)
    emotional_journey: EmotionalJourney = Field(default_factory=EmotionalJourney)
    narrative_thread: NarrativeThread = Field(default_factory=NarrativeThread)
    sections: list[SongSection] = Field(default_factory=list)
    transitions: list[SongTransition] = Field(default_factory=list)
    shorts: ShortsDirective = Field(default_factory=ShortsDirective)
    thumbnail: ThumbnailDirective = Field(default_factory=ThumbnailDirective)

    @field_validator("color_palette", mode="before")
    @classmethod
    def ensure_hex_prefix(cls, v):
        if not isinstance(v, list):
            return ["#1a1a2e", "#4a3d8f", "#6c3ce1"]
        return [c if str(c).startswith("#") else f"#{c}" for c in v]

    @field_validator("text_overlay_suggestion", mode="before")
    @classmethod
    def sanitize_null_strings(cls, v):
        if not v or str(v).strip().lower() in ["none", "null", ""]:
            return ""
        return str(v)

    @property
    def has_sections(self) -> bool:
        """Whether the Director produced section-level analysis."""
        return len(self.sections) >= 2

    def to_dict(self) -> dict:
        return self.model_dump()


# ──────────────────────────────────────────────
#  Season Context
# ──────────────────────────────────────────────
def _get_season_context() -> str:
    month = datetime.now(timezone.utc).month
    seasons = {
        1: "Board exam preparation season, pre-JEE stress, winter cold nights",
        2: "Board exams approaching, Valentine's Day, intense study pressure",
        3: "Board exams happening, farewell season, last days of school",
        4: "Results anxiety, farewell season, new beginnings ahead",
        5: "JEE Mains/Advanced season, summer heat, exam results",
        6: "Results declared, college admissions, new chapter beginning",
        7: "Monsoon begins, new college life, hostel life starts, homesickness",
        8: "Monsoon, settling into college, independence, missing home",
        9: "Mid-semester season, Navratri, campus life settling in",
        10: "Diwali approaching, homesickness peaks, festival celebrations",
        11: "Winter exams, year-end reflection, cold campus nights",
        12: "Winter break, going home, New Year hopes, reunion with family",
    }
    return seasons.get(month, "General study and campus life")


# ──────────────────────────────────────────────
#  Director Agent
# ──────────────────────────────────────────────
class Director:
    """
    Analyzes an audio file and produces a CreativeBrief that drives
    all downstream agents with song-specific creative decisions.
    """

    def __init__(self) -> None:
        self.rotator = APIRotator()
        self.memory = director_memory()

    def analyze(self, audio_path: str | Path) -> CreativeBrief:
        audio_path = Path(audio_path)
        if not audio_path.exists():
            raise FileNotFoundError(f"Audio file not found: {audio_path}")

        logger.info("Director analyzing: %s", audio_path.name)

        prompt = self._build_prompt()

        raw_response = self.rotator.generate_text_with_media(
            media_path=str(audio_path),
            prompt=prompt,
            mime_type="audio/mpeg",
            model="gemini-2.5-flash",
            temperature=0.7,
        )

        brief = self._parse_response(raw_response, str(audio_path))

        logger.info(
            "Director brief: mood=%s, energy=%s, sections=%d, arc=%s",
            brief.mood, brief.energy, len(brief.sections),
            brief.emotional_journey.arc_type,
        )

        return brief

    def _build_prompt(self) -> str:
        base = ANALYSIS_PROMPT.format(
            current_date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            season_context=_get_season_context(),
        )

        mem_data = self.memory.load()
        perf_log = mem_data.get("performance_log", [])
        if perf_log:
            recent = perf_log[-5:]
            rules_text = "\n".join(
                f"- {entry.get('rule', '')}" for entry in recent if entry.get("rule")
            )
            if rules_text:
                base += (
                    f"\n\nLearned rules from past performance:\n{rules_text}\n"
                    f"Apply these rules when making creative decisions."
                )

        return base

    def _parse_response(self, raw: str, source_file: str) -> CreativeBrief:
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            lines = cleaned.split("\n")
            lines = [l for l in lines if not l.strip().startswith("```")]
            cleaned = "\n".join(lines)

        try:
            data = json.loads(cleaned)
            data["source_file"] = source_file

            # Parse nested objects that Pydantic needs help with
            if "sections" in data and isinstance(data["sections"], list):
                for i, s in enumerate(data["sections"]):
                    if isinstance(s, dict):
                        if "color_grade" in s and isinstance(s["color_grade"], dict):
                            s["color_grade"] = SectionColorGrade(**s["color_grade"])
                        if "zoom" in s and isinstance(s["zoom"], dict):
                            s["zoom"] = SectionZoom(**s["zoom"])
                        if "text_overlay" in s and isinstance(s["text_overlay"], dict):
                            s["text_overlay"] = SectionTextOverlay(**s["text_overlay"])
                        data["sections"][i] = SongSection(**s)

            if "transitions" in data and isinstance(data["transitions"], list):
                data["transitions"] = [
                    SongTransition(**t) if isinstance(t, dict) else t
                    for t in data["transitions"]
                ]

            for key in ["shorts", "thumbnail", "song_dna", "emotional_journey", "narrative_thread"]:
                if key in data and isinstance(data[key], dict):
                    model_map = {
                        "shorts": ShortsDirective,
                        "thumbnail": ThumbnailDirective,
                        "song_dna": SongDNA,
                        "emotional_journey": EmotionalJourney,
                        "narrative_thread": NarrativeThread,
                    }
                    try:
                        data[key] = model_map[key](**data[key])
                    except Exception:
                        pass

            return CreativeBrief.model_validate(data)

        except (json.JSONDecodeError, ValidationError) as e:
            logger.error("Director failed to parse LLM response: %s. Using defaults.", e)
            logger.debug("Raw response: %s", raw[:500])
            return CreativeBrief(source_file=source_file)

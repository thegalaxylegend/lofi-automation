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

      "image_prompt": "<VERY detailed AI image prompt, minimum 40 words. MUST START WITH the exact visual_style declared above. Include: culturally appropriate Indian elements matching THIS song's genre and mood. Include: lighting, color palette, atmosphere, subject/character, camera angle. The visual_motif MUST appear in this prompt. ALL section prompts MUST use the SAME art style.>",

      "scene_description": "<CRITICAL — DETAILED STORYBOARD. Minimum 30 words. Describe EXACTLY what the viewer should SEE during this timestamp range. Include: WHO is in the scene (a student, an empty desk, a hand), WHAT they are doing (writing, staring, walking), WHERE they are (classroom, corridor, bus stop, rooftop), WHAT THE ENVIRONMENT looks like (rain outside, dim lights, sunset, crowded), CAMERA ANGLE (close-up on face, wide shot of room, overhead view). Think like a MOVIE DIRECTOR describing each shot. Example for 0:15-0:27 of an exam song: 'Close-up of a student sitting alone in a dim classroom. Rain is pouring against the windows. Papers scattered on the desk. The student stares blankly at an empty answer sheet. Camera slowly pulls back to reveal the empty room.' Example for 0:27-0:38: 'A school boy walks alone through heavy rain carrying a worn school bag. His head is down. The street is empty. Puddles reflect streetlights. Wide tracking shot from behind.' NEVER give generic descriptions. NEVER say just 'sad scene'. Be SPECIFIC to what THIS song is about at THIS exact moment.>",
      "video_search_query": "<SHORT stock video search query (3-5 words) for Pexels/Pixabay. Derived from scene_description. Example: 'student alone classroom rain' or 'boy walking rain street'. MUST be different from adjacent sections.>",
      "primary_subject": "<The main visual subject of this section, e.g. 'student at desk', 'empty corridor', 'rain on window'. MUST be different from adjacent sections. NEVER default to 'girl' or 'boy' unless the lyrics explicitly describe a specific person.>",

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
  "visualizer_intensity": "subtle",

  "vfx_profile": "<ONE of: aggressive | vintage_analog | ethereal | cinematic_drama | raw_minimal. Choose based on genre+energy: EDM/rap/phonk/party=aggressive, lofi/chillhop/nostalgic=vintage_analog, acoustic/devotional/ambient=ethereal, romantic/emotional_ballad=cinematic_drama, rock/indie/punk=raw_minimal. If genre unclear, fall back to energy: high=aggressive, medium=cinematic_drama, low=ethereal.>",
  "video_search_queries": ["<query1 max 5 words>", "<query2>", "<query3>", "<query4>", "<query5>"]
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
- SECTION DURATION IS TEMPO-DEPENDENT:
  * For HIGH energy / fast BPM (party, dance, EDM, hip-hop): sections should be SHORT (5-10 seconds each). Fast songs need rapid visual changes to match the energy. More sections = more visual variety = more engaging.
  * For MEDIUM energy (pop, romantic, motivational): sections should be 10-15 seconds each.
  * For LOW energy (sad, lo-fi, ambient, devotional): sections should be 12-20 seconds each. Slow songs need time to breathe — rushing cuts kills the mood.
  * NEVER exceed 20 seconds for any section regardless of energy.
- TRANSITION SPEED MUST MATCH ENERGY:
  * HIGH energy songs: use "fast_dissolve" (0.5s) or "direct_cut" for most transitions. Quick cuts create excitement.
  * LOW energy songs: use "slow_dissolve" (1.5-2.0s) or "fade_through_black". Gentle transitions preserve the emotional flow.
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
    video_search_query: str = ""  # Stock video search query for this section
    primary_subject: str = ""  # Primary visual subject (for diversity enforcement)
    scene_description: str = ""  # DETAILED storyboard: exactly what visual should play in this section
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

class SongComprehension(BaseModel):
    """Phase 1 output: What the song is literally about (before any visual direction)."""
    literal_subjects: list[str] = Field(default_factory=list)  # e.g. ["exam hall", "clock", "answer sheet"]
    story_summary: str = ""  # The literal narrative of the song
    key_moments: list[dict] = Field(default_factory=list)  # [{"timestamp": 45.0, "lyric": "...", "meaning": "..."}]
    is_instrumental: bool = False
    detected_language: str = "hindi"


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

    # Video Pipeline fields (Two-Phase Director + VFX Engine)
    song_comprehension: SongComprehension = Field(default_factory=SongComprehension)
    vfx_profile: str = "cinematic_drama"  # aggressive | vintage_analog | ethereal | cinematic_drama | raw_minimal
    video_search_queries: list[str] = Field(default_factory=list)  # Per-section stock video queries

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
#  Comprehension Prompt (Phase 1 — Understanding)
# ──────────────────────────────────────────────
COMPREHENSION_PROMPT = """You are a music analyst. Listen to this ENTIRE audio track deeply.

Your ONLY job is to understand what this song is LITERALLY about. Do NOT generate any visual directions, image prompts, or aesthetic descriptions.

Extract the following:
1. Every specific OBJECT, PLACE, or SITUATION mentioned or implied in the lyrics (e.g. "exam hall", "ticking clock", "empty road", "temple bells").
2. The literal STORY being told — who is the subject, what are they doing, what happens to them?
3. Key emotional moments with approximate timestamps.
4. Whether this is an instrumental track (no vocals/lyrics).
5. The detected language of the vocals.

Return ONLY valid JSON with this structure:
{{
  "literal_subjects": ["<every concrete noun/object/place mentioned or strongly implied>"],
  "story_summary": "<2-3 sentences describing the literal narrative of the song>",
  "key_moments": [
    {{"timestamp_sec": <float>, "lyric_snippet": "<actual lyric or description>", "meaning": "<what this moment conveys>"}}
  ],
  "is_instrumental": <true if no vocals/lyrics, false otherwise>,
  "detected_language": "<hindi/english/mixed/instrumental>"
}}

CRITICAL RULES:
- Return ONLY valid JSON. No markdown. No explanation.
- Extract REAL objects from the lyrics. If the song says "kitaabon ke pahaad" (mountains of books), list "books" and "study desk", NOT "a beautiful girl".
- If the song is instrumental, set is_instrumental to true and infer subjects from the MOOD and INSTRUMENTS (e.g. piano + rain sounds = "rainy evening", "piano keys", "window").
- Be SPECIFIC. "hostel corridor at 2 AM" is better than "night scene".
- List at least 5 literal_subjects.
"""


# ──────────────────────────────────────────────
#  Director Agent
# ──────────────────────────────────────────────
class Director:
    """
    Two-Phase Creative Director:
      Phase 1 (Comprehension): Understands what the song is literally about.
      Phase 2 (Direction): Generates visual direction grounded in the comprehension.
    """

    def __init__(self) -> None:
        self.rotator = APIRotator()
        self.memory = director_memory()

    def analyze(self, audio_path: str | Path) -> CreativeBrief:
        audio_path = Path(audio_path)
        if not audio_path.exists():
            raise FileNotFoundError(f"Audio file not found: {audio_path}")

        logger.info("Director analyzing: %s", audio_path.name)

        # ── Phase 1: Comprehension ──────────────
        logger.info("Phase 1: Song comprehension...")
        comprehension = self._run_comprehension(audio_path)
        logger.info(
            "Comprehension: instrumental=%s, subjects=%s, story=%s",
            comprehension.is_instrumental,
            comprehension.literal_subjects[:5],
            comprehension.story_summary[:100],
        )

        # ── Phase 2: Creative Direction ─────────
        logger.info("Phase 2: Creative direction (grounded in comprehension)...")
        prompt = self._build_prompt(comprehension)

        raw_response = self.rotator.generate_text_with_media(
            media_path=str(audio_path),
            prompt=prompt,
            mime_type="audio/mpeg",
            model="gemini-2.5-flash",
            temperature=0.7,
        )

        brief = self._parse_response(raw_response, str(audio_path))

        # Attach comprehension data to the brief
        brief.song_comprehension = comprehension

        # Validate subject diversity
        self._enforce_subject_diversity(brief)

        logger.info(
            "Director brief: mood=%s, energy=%s, vfx=%s, sections=%d, arc=%s",
            brief.mood, brief.energy, brief.vfx_profile,
            len(brief.sections), brief.emotional_journey.arc_type,
        )

        return brief

    def _run_comprehension(self, audio_path: Path) -> SongComprehension:
        """Phase 1: Extract what the song is literally about."""
        try:
            raw = self.rotator.generate_text_with_media(
                media_path=str(audio_path),
                prompt=COMPREHENSION_PROMPT,
                mime_type="audio/mpeg",
                model="gemini-2.5-flash",
                temperature=0.3,  # Low temp for factual extraction
            )
            cleaned = raw.strip()
            if cleaned.startswith("```"):
                lines = cleaned.split("\n")
                lines = [l for l in lines if not l.strip().startswith("```")]
                cleaned = "\n".join(lines)

            data = json.loads(cleaned)
            return SongComprehension(**data)
        except Exception as exc:
            logger.warning("Comprehension phase failed: %s. Using empty comprehension.", exc)
            return SongComprehension()

    def _enforce_subject_diversity(self, brief: CreativeBrief) -> None:
        """Post-check: ensure no primary_subject is repeated in adjacent sections."""
        if not brief.has_sections:
            return
        subjects = [s.primary_subject.lower().strip() for s in brief.sections]
        for i in range(1, len(subjects)):
            if subjects[i] and subjects[i] == subjects[i - 1]:
                logger.warning(
                    "Adjacent sections %d and %d have same subject '%s'. "
                    "Downstream agents should vary the visual.",
                    i - 1, i, subjects[i],
                )

    def _build_prompt(self, comprehension: SongComprehension | None = None) -> str:
        base = ANALYSIS_PROMPT.format(
            current_date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            season_context=_get_season_context(),
        )

        # Inject Phase 1 comprehension as grounding context
        if comprehension and comprehension.literal_subjects:
            subjects_str = ", ".join(comprehension.literal_subjects[:15])
            base += (
                f"\n\n=== SONG COMPREHENSION REPORT (Phase 1) ===\n"
                f"The song is about: {comprehension.story_summary}\n"
                f"Objects/places/situations in the lyrics: [{subjects_str}]\n"
                f"Language: {comprehension.detected_language}\n"
                f"Instrumental: {comprehension.is_instrumental}\n\n"
                f"GROUNDING RULES (CRITICAL):\n"
                f"- Your video_search_query for each section MUST reference objects from "
                f"the list above. If the song mentions 'exam hall', search for 'exam hall', "
                f"NOT 'beautiful girl studying'.\n"
                f"- Your primary_subject for each section MUST come from the comprehension "
                f"report's literal_subjects list.\n"
                f"- NEVER default to aesthetic clichés: 'girl sitting alone', 'boy with "
                f"headphones', 'couple on beach', 'neon city'. Use the ACTUAL content.\n"
                f"- If the comprehension says the song is about exam pressure, EVERY visual "
                f"must relate to exams, studying, pressure, clocks, answer sheets — not "
                f"random aesthetic imagery.\n"
                f"===========================================\n"
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

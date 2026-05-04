"""
Agent 6: Compliance Agent — Protects the channel from bans.

Scans all generated text against YouTube Community Guidelines:
  - Title, description, and tags safety check
  - Detects misleading claims
  - Ensures AI disclosure compliance
  - Maintains a growing blacklist from past flags
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from agents.marketer import VideoMetadata
from core.api_rotation import APIRotator
from core.memory import compliance_memory

logger = logging.getLogger(__name__)

# Static blacklist of terms that should never appear
STATIC_BLACKLIST = {
    "guaranteed results", "100% pass", "free money",
    "get rich", "hack", "leaked", "pirated",
    "copyright free",  # misleading — say "royalty free" instead
}

COMPLIANCE_PROMPT = """You are a YouTube Community Guidelines compliance checker.

Analyze the following YouTube video metadata for any policy violations:

TITLE: {title}
DESCRIPTION: {description}
TAGS: {tags}

Check for:
1. Misleading claims or clickbait that could be flagged
2. Words or phrases that violate YouTube's Community Guidelines
3. Copyright-related issues in the text
4. Age-restricted or sensitive content references
5. Spam patterns (excessive hashtags, keyword stuffing)

Return ONLY valid JSON:
{{
  "is_safe": true/false,
  "issues": ["<list of specific issues found, or empty if safe>"],
  "suggestions": ["<list of improvement suggestions>"],
  "risk_level": "<low/medium/high>"
}}
"""


@dataclass
class ComplianceResult:
    """Result of compliance check."""
    is_safe: bool = True
    issues: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)
    risk_level: str = "low"

    def summary(self) -> str:
        status = "✅ SAFE" if self.is_safe else "⚠️ FLAGGED"
        lines = [f"Compliance: {status} (risk: {self.risk_level})"]
        for issue in self.issues:
            lines.append(f"  ISSUE: {issue}")
        for sug in self.suggestions:
            lines.append(f"  TIP:   {sug}")
        return "\n".join(lines)


class ComplianceAgent:
    """
    Scans metadata for YouTube policy violations using both
    static rules and AI-powered analysis.
    """

    def __init__(self) -> None:
        self.rotator = APIRotator()
        self.memory = compliance_memory()

    def check(self, metadata: VideoMetadata) -> ComplianceResult:
        """
        Run compliance checks on video metadata.

        Returns:
            ComplianceResult with safety status and issues.
        """
        result = ComplianceResult()

        # Step 1: Static blacklist check
        self._static_check(metadata, result)

        # Step 2: Pattern-based checks
        self._pattern_check(metadata, result)

        # Step 3: Memory-based blacklist
        self._memory_check(metadata, result)

        # Step 4: AI-powered deep check (only if static checks pass)
        if result.is_safe:
            self._ai_check(metadata, result)

        logger.info(result.summary())
        return result

    def _static_check(self, meta: VideoMetadata, result: ComplianceResult) -> None:
        """Check against hardcoded blacklist."""
        full_text = f"{meta.title} {meta.description}".lower()
        for term in STATIC_BLACKLIST:
            if term in full_text:
                result.is_safe = False
                result.issues.append(f"Blacklisted term found: '{term}'")

    def _pattern_check(self, meta: VideoMetadata, result: ComplianceResult) -> None:
        """Check for spam patterns and policy red flags."""
        # Excessive caps in title
        if meta.title and sum(1 for c in meta.title if c.isupper()) > len(meta.title) * 0.6:
            result.suggestions.append("Title has excessive caps — may be flagged as clickbait.")

        # Too many hashtags
        hashtag_count = len(re.findall(r"#\w+", meta.description))
        if hashtag_count > 20:
            result.suggestions.append(f"Too many hashtags ({hashtag_count}). Keep under 15.")

        # Keyword stuffing in tags
        if len(meta.tags) > 30:
            result.suggestions.append(f"Too many tags ({len(meta.tags)}). YouTube may flag as spam.")

        # Check for phone numbers or personal info patterns
        if re.search(r"\b\d{10,}\b", meta.description):
            result.issues.append("Possible phone number detected in description.")
            result.risk_level = "medium"

    def _memory_check(self, meta: VideoMetadata, result: ComplianceResult) -> None:
        """Check against learned blacklist from past violations."""
        mem = self.memory.load()
        banned = mem.get("banned_words", [])
        flagged = mem.get("flagged_phrases", [])

        full_text = f"{meta.title} {meta.description}".lower()
        for word in banned:
            if word.lower() in full_text:
                result.is_safe = False
                result.issues.append(f"Previously banned word found: '{word}'")

        for phrase in flagged:
            if phrase.lower() in full_text:
                result.risk_level = "medium"
                result.suggestions.append(f"Previously flagged phrase: '{phrase}'")

    def _ai_check(self, meta: VideoMetadata, result: ComplianceResult) -> None:
        """Use AI for deeper content analysis."""
        try:
            prompt = COMPLIANCE_PROMPT.format(
                title=meta.title,
                description=meta.description[:1000],
                tags=", ".join(meta.tags[:20]),
            )

            raw = self.rotator.generate_text(prompt, temperature=0.3, max_retries=2)

            import json
            cleaned = raw.strip()
            if cleaned.startswith("```"):
                lines = cleaned.split("\n")
                lines = [l for l in lines if not l.strip().startswith("```")]
                cleaned = "\n".join(lines)

            data = json.loads(cleaned)

            if not data.get("is_safe", True):
                result.is_safe = False
                result.issues.extend(data.get("issues", []))

            result.suggestions.extend(data.get("suggestions", []))

            ai_risk = data.get("risk_level", "low")
            if ai_risk in ("medium", "high"):
                result.risk_level = ai_risk

        except Exception as exc:
            logger.warning("AI compliance check failed (non-fatal): %s", exc)
            result.suggestions.append("AI compliance check skipped due to error.")

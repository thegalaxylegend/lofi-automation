"""
Persistent memory system for agent self-improvement.

Each agent stores learned rules as JSON files in the memory/ directory.
These commit back to GitHub for free, persistent, version-controlled memory.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.config import MEMORY_DIR

logger = logging.getLogger(__name__)


class Memory:
    """Read/write JSON-based memory files for agent self-improvement."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.path = MEMORY_DIR / f"{name}.json"

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            logger.info("Memory '%s' not found. Initializing empty.", self.name)
            return {"_meta": {"last_updated": datetime.now(timezone.utc).isoformat()}}
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except (json.JSONDecodeError, OSError) as exc:
            logger.error("Failed to load memory '%s': %s", self.name, exc)
            return {"_meta": {"last_updated": datetime.now(timezone.utc).isoformat()}}

    def save(self, data: dict[str, Any]) -> None:
        data["_meta"] = {
            "last_updated": datetime.now(timezone.utc).isoformat(),
            "memory_name": self.name,
        }
        try:
            with open(self.path, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2, ensure_ascii=False)
            logger.info("Memory '%s' saved.", self.name)
        except OSError as exc:
            logger.error("Failed to save memory '%s': %s", self.name, exc)

    def get(self, key: str, default: Any = None) -> Any:
        return self.load().get(key, default)

    def update_key(self, key: str, value: Any) -> None:
        data = self.load()
        data[key] = value
        self.save(data)

    def append_to_list(self, key: str, item: Any, max_items: int = 500) -> None:
        data = self.load()
        if key not in data or not isinstance(data[key], list):
            data[key] = []
        data[key].append(item)
        if len(data[key]) > max_items:
            data[key] = data[key][-max_items:]
        self.save(data)

    def increment(self, key: str, amount: int = 1) -> int:
        data = self.load()
        current = data.get(key, 0)
        if not isinstance(current, (int, float)):
            current = 0
        data[key] = current + amount
        self.save(data)
        return data[key]


# Pre-defined memory factories for each agent
def director_memory() -> Memory:
    return Memory("director_mood_map")

def marketer_memory() -> Memory:
    return Memory("marketer_keywords")

def editor_memory() -> Memory:
    return Memory("editor_limits")

def analyst_memory() -> Memory:
    return Memory("analyst_rules")

def compliance_memory() -> Memory:
    return Memory("compliance_blacklist")

def scout_memory() -> Memory:
    return Memory("scout_trends")


def pipeline_memory() -> Memory:
    """Memory for tracking processed audio files to prevent duplicates."""
    return Memory("pipeline_history")


def initialize_all_memories() -> None:
    """Create default memory files if they don't exist."""
    defaults = {
        "director_mood_map": {
            "mood_visual_mappings": {
                "melancholic": {"colors": ["#1a1a2e", "#4a3d8f"], "styles": ["rainy window", "starry night"]},
                "energetic": {"colors": ["#ff6b35", "#ffd700"], "styles": ["city lights", "neon street"]},
                "peaceful": {"colors": ["#2d5a27", "#87ceeb"], "styles": ["forest stream", "cloudy sky"]},
                "nostalgic": {"colors": ["#d4a373", "#cdb4db"], "styles": ["sunset road", "old room"]},
                "dark": {"colors": ["#0d0d0d", "#6c3ce1"], "styles": ["cyberpunk", "space"]},
            },
            "performance_log": [],
        },
        "marketer_keywords": {
            "power_words": ["3 AM", "late night", "exam season", "when you need to focus"],
            "avoid_words": [],
            "title_performance": [],
            "thumbnail_performance": [],
        },
        "editor_limits": {
            "max_resolution": "1920x1080",
            "safe_effects": ["showcqt", "color_grading", "film_grain"],
            "risky_effects": [],
            "crash_log": [],
        },
        "analyst_rules": {
            "rules": [],
            "daily_reports": [],
            "weekly_reports": [],
        },
        "compliance_blacklist": {
            "banned_words": [],
            "flagged_phrases": [],
            "safe_patterns": [],
        },
        "scout_trends": {
            "competitors": [],
            "trending_keywords": [],
            "color_trends": [],
            "last_scan": None,
        },
        "pipeline_history": {
            "processed_files": [],
            "last_run": None,
        },
    }
    for name, default_data in defaults.items():
        mem = Memory(name)
        if not mem.path.exists():
            mem.save(default_data)
            logger.info("Initialized memory: %s", name)

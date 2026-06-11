"""
Configuration loader for the lo-fi automation pipeline.

Reads brand_config.json and .env variables, providing a single
typed interface for the entire application.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────
#  Resolve project root (works from any CWD)
# ──────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = PROJECT_ROOT / ".env"
BRAND_CONFIG_PATH = PROJECT_ROOT / "brand_config.json"
MEMORY_DIR = PROJECT_ROOT / "memory"
OUTPUT_DIR = PROJECT_ROOT / "output"
TEMP_DIR = PROJECT_ROOT / "temp"
TEMPLATES_DIR = PROJECT_ROOT / "templates"

# Ensure directories exist
for _dir in (MEMORY_DIR, OUTPUT_DIR, TEMP_DIR, TEMPLATES_DIR):
    _dir.mkdir(parents=True, exist_ok=True)


# ──────────────────────────────────────────────
#  Data classes
# ──────────────────────────────────────────────
@dataclass(frozen=True)
class VideoSettings:
    resolution: str = "1920x1080"
    fps: int = 30
    crf: int = 18
    codec: str = "libx264"
    preset: str = "medium"
    pixel_format: str = "yuv420p"
    audio_bitrate: str = "320k"

    @property
    def width(self) -> int:
        return int(self.resolution.split("x")[0])

    @property
    def height(self) -> int:
        return int(self.resolution.split("x")[1])


@dataclass(frozen=True)
class ShortsSettings:
    resolution: str = "1080x1920"
    max_duration_sec: int = 59
    text_style: str = "floating_pov"

    @property
    def width(self) -> int:
        return int(self.resolution.split("x")[0])

    @property
    def height(self) -> int:
        return int(self.resolution.split("x")[1])


@dataclass(frozen=True)
class ThumbnailSettings:
    width: int = 1280
    height: int = 720
    font_title: str = "Outfit-Bold"
    font_subtitle: str = "Inter-Regular"
    title_max_chars: int = 35
    style: str = "dark_gradient_with_glow"
    logo_position: str = "bottom_right"


@dataclass(frozen=True)
class SEOSettings:
    default_tags: list[str] = field(default_factory=list)
    target_title_length: int = 60
    description_min_words: int = 100


@dataclass(frozen=True)
class BrandColors:
    primary: str = "#6C3CE1"
    secondary: str = "#1A0A3E"
    accent: str = "#FF6B9D"
    text_light: str = "#F0E6FF"
    text_dark: str = "#0D0520"


@dataclass(frozen=True)
class ChannelConfig:
    name: str
    handle: str
    tagline: str
    audience: str
    language: str
    brand_colors: BrandColors
    thumbnail: ThumbnailSettings
    video: VideoSettings
    shorts: ShortsSettings
    seo: SEOSettings


# ──────────────────────────────────────────────
#  Configuration singleton
# ──────────────────────────────────────────────
class Config:
    """Central configuration for the entire pipeline."""

    _instance: Config | None = None

    def __new__(cls) -> Config:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self) -> None:
        if self._initialized:
            return
        self._initialized = True
        self._load_env()
        self._load_brand_config()
        logger.info("Configuration loaded successfully.")

    # ── Environment ──────────────────────────
    def _load_env(self) -> None:
        # Only load from .env if it actually exists (local dev)
        if ENV_PATH.exists():
            load_dotenv(ENV_PATH)
            logger.debug("Loaded variables from .env file: %s", ENV_PATH)
        else:
            logger.debug(".env file not found at %s. Relying on system environment.", ENV_PATH)

        # Debug: List all available environment variables (names only) for troubleshooting
        all_env_keys = list(os.environ.keys())
        llm_keys_found = [k for k in all_env_keys if k.startswith(("GEMINI_API_KEY_", "GROQ_API_KEY_"))]
        logger.info("Environment probe found keys: %s", ", ".join(llm_keys_found) if llm_keys_found else "NONE")

        self.gemini_keys: list[str] = self._collect_keys("GEMINI_API_KEY_", 6)
        self.groq_keys: list[str] = self._collect_keys("GROQ_API_KEY_", 8)

        self.pexels_api_key: str = os.getenv("PEXELS_API_KEY", "")
        self.pixabay_api_key: str = os.getenv("PIXABAY_API_KEY", "")
        self.coverr_api_key: str = os.getenv("COVERR_API_KEY", "")
        self.vecteezy_account_id: str = os.getenv("VECTEEZY_ACCOUNT_ID", "")
        self.vecteezy_secret_key: str = os.getenv("VECTEEZY_SECRET_KEY", "")

        self.youtube_client_id: str = os.getenv("YOUTUBE_CLIENT_ID", "")
        self.youtube_client_secret: str = os.getenv("YOUTUBE_CLIENT_SECRET", "")
        self.youtube_refresh_token: str = os.getenv("YOUTUBE_REFRESH_TOKEN", "")

        self.discord_webhook_uploads: str = os.getenv("DISCORD_WEBHOOK_UPLOADS", "")
        self.discord_webhook_daily: str = os.getenv("DISCORD_WEBHOOK_DAILY", "")
        self.discord_webhook_alerts: str = os.getenv("DISCORD_WEBHOOK_ALERTS", "")

        self.telegram_bot_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
        self.telegram_chat_id: str = os.getenv("TELEGRAM_CHAT_ID", "")

        self.cloudflare_account_ids: list[str] = self._collect_keys("CLOUDFLARE_ACCOUNT_ID_", 10)
        self.cloudflare_api_tokens: list[str] = self._collect_keys("CLOUDFLARE_API_TOKEN_", 10)

        logger.info(
            "API keys loaded: %d Gemini, %d Groq",
            len(self.gemini_keys),
            len(self.groq_keys),
        )

    @staticmethod
    def _collect_keys(prefix: str, max_count: int) -> list[str]:
        keys: list[str] = []
        for i in range(1, max_count + 1):
            val = os.getenv(f"{prefix}{i}", "")
            if val:
                keys.append(val)
        return keys

    # ── Brand Config ─────────────────────────
    def _load_brand_config(self) -> None:
        if not BRAND_CONFIG_PATH.exists():
            raise FileNotFoundError(
                f"Brand config not found at {BRAND_CONFIG_PATH}. "
                "Copy brand_config.json to the project root."
            )

        with open(BRAND_CONFIG_PATH, "r", encoding="utf-8") as fh:
            raw: dict[str, Any] = json.load(fh)

        defaults = raw.get("defaults", {})
        self.active_channel_key: str = defaults.get("active_channel", "lofi")
        self.upload_as_draft: bool = defaults.get("upload_as_draft", True)
        self.auto_shorts: bool = defaults.get("auto_shorts", True)
        self.weekly_compilation: bool = defaults.get("weekly_compilation", True)
        self.discord_reports: bool = defaults.get("discord_reports", True)

        self._channels: dict[str, ChannelConfig] = {}
        for key, ch in raw.get("channels", {}).items():
            self._channels[key] = ChannelConfig(
                name=ch["name"],
                handle=ch["handle"],
                tagline=ch["tagline"],
                audience=ch["audience"],
                language=ch["language"],
                brand_colors=BrandColors(**ch["brand_colors"]),
                thumbnail=ThumbnailSettings(**ch["thumbnail"]),
                video=VideoSettings(**ch["video"]),
                shorts=ShortsSettings(**ch["shorts"]),
                seo=SEOSettings(**ch["seo"]),
            )

    @property
    def channel(self) -> ChannelConfig:
        """Return the currently active channel configuration."""
        return self._channels[self.active_channel_key]

    def get_channel(self, key: str) -> ChannelConfig:
        """Return a specific channel configuration by key."""
        if key not in self._channels:
            raise KeyError(
                f"Channel '{key}' not found. Available: {list(self._channels)}"
            )
        return self._channels[key]

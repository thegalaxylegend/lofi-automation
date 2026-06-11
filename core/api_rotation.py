"""
API Key Rotation with per-key rate-limit tracking.

Supports Gemini and Groq key pools with:
  - Round-robin selection
  - Automatic cooldown on 429 / rate-limit errors
  - Seamless fallback between providers
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import google.genai as genai
from groq import Groq

from core.config import Config

logger = logging.getLogger(__name__)


class Provider(Enum):
    GEMINI = "gemini"
    GROQ = "groq"


@dataclass
class KeySlot:
    """Tracks the state of a single API key."""

    key: str
    provider: Provider
    index: int
    total_calls: int = 0
    total_failures: int = 0
    cooldown_until: float = 0.0
    last_used: float = 0.0

    @property
    def is_available(self) -> bool:
        return time.time() >= self.cooldown_until

    def mark_used(self) -> None:
        self.total_calls += 1
        self.last_used = time.time()

    def mark_rate_limited(self, cooldown_sec: float = 60.0) -> None:
        self.total_failures += 1
        self.cooldown_until = time.time() + cooldown_sec
        logger.warning(
            "%s key #%d rate-limited. Cooling down for %.0fs.",
            self.provider.value,
            self.index,
            cooldown_sec,
        )


@dataclass
class KeyPool:
    """Manages a pool of API keys for a single provider."""

    provider: Provider
    slots: list[KeySlot] = field(default_factory=list)
    _cursor: int = 0

    def add(self, key: str) -> None:
        idx = len(self.slots)
        self.slots.append(KeySlot(key=key, provider=self.provider, index=idx))

    def next_available(self) -> KeySlot | None:
        """Return the next available key using round-robin."""
        if not self.slots:
            return None

        n = len(self.slots)
        for _ in range(n):
            slot = self.slots[self._cursor % n]
            self._cursor += 1
            if slot.is_available:
                return slot

        # All keys are cooling down — return the one that recovers soonest
        soonest = min(self.slots, key=lambda s: s.cooldown_until)
        wait = soonest.cooldown_until - time.time()
        if wait > 0:
            logger.info(
                "All %s keys cooling. Waiting %.1fs for key #%d...",
                self.provider.value,
                wait,
                soonest.index,
            )
            time.sleep(wait)
        return soonest


class APIRotator:
    """
    Unified interface for making LLM calls with automatic
    key rotation and provider fallback.

    Usage:
        rotator = APIRotator()
        result = rotator.generate_text("Describe this mood...")
        result = rotator.generate_text_with_audio(audio_path, "Analyze this song")
    """

    _instance: APIRotator | None = None

    def __new__(cls) -> APIRotator:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self) -> None:
        if self._initialized:
            return
        self._initialized = True

        cfg = Config()

        self._gemini_pool = KeyPool(provider=Provider.GEMINI)
        for key in cfg.gemini_keys:
            self._gemini_pool.add(key)

        self._groq_pool = KeyPool(provider=Provider.GROQ)
        for key in cfg.groq_keys:
            self._groq_pool.add(key)

        logger.info(
            "APIRotator initialized: %d Gemini keys, %d Groq keys",
            len(self._gemini_pool.slots),
            len(self._groq_pool.slots),
        )

    # ── Public Methods ───────────────────────

    def generate_text(
        self,
        prompt: str,
        *,
        model: str = "gemini-2.5-flash",
        temperature: float = 0.7,
        max_retries: int = 100,
    ) -> str:
        """Generate text using Gemini with Groq fallback."""

        # Try Gemini first
        result = self._try_gemini(prompt, model=model, temperature=temperature, retries=max_retries)
        if result is not None:
            return result

        # Fallback to Groq
        logger.info("Gemini exhausted. Falling back to Groq.")
        result = self._try_groq(prompt, temperature=temperature, retries=max_retries)
        if result is not None:
            return result

        raise RuntimeError("All API keys exhausted across all providers.")

    def generate_text_with_media(
        self,
        media_path: str,
        prompt: str,
        *,
        mime_type: str = "audio/mpeg",
        model: str = "gemini-2.5-flash",
        temperature: float = 0.7,
        max_retries: int = 100,
    ) -> str:
        """
        Generate text with a media file attachment (audio/image).
        Only available via Gemini (Groq doesn't support media input).
        """
        result = self._try_gemini_media(
            media_path,
            prompt,
            mime_type=mime_type,
            model=model,
            temperature=temperature,
            retries=max_retries,
        )
        if result is not None:
            return result

        raise RuntimeError(
            "All Gemini keys exhausted. Media analysis requires Gemini — "
            "Groq does not support audio/image input."
        )

    # ── Gemini ───────────────────────────────

    def _try_gemini(
        self,
        prompt: str,
        *,
        model: str,
        temperature: float,
        retries: int,
    ) -> str | None:
        for attempt in range(retries):
            slot = self._gemini_pool.next_available()
            if slot is None:
                return None

            try:
                client = genai.Client(api_key=slot.key)
                response = client.models.generate_content(
                    model=model,
                    contents=prompt,
                    config=genai.types.GenerateContentConfig(
                        temperature=temperature,
                    ),
                )
                slot.mark_used()

                if response.text:
                    return response.text.strip()

                logger.warning("Gemini key #%d returned empty response.", slot.index)

            except Exception as exc:
                exc_str = str(exc).lower()
                if "model" in exc_str and ("404" in exc_str or "not found" in exc_str):
                    logger.error(
                        "Gemini key #%d: MODEL NOT FOUND (404). "
                        "Check model name is valid. Error: %s",
                        slot.index, exc,
                    )
                    # Don't cooldown — every key will fail the same way
                    return None
                elif "429" in exc_str or "rate" in exc_str or "quota" in exc_str:
                    slot.mark_rate_limited(cooldown_sec=60.0)
                else:
                    logger.error("Gemini key #%d error: %s", slot.index, exc)
                    slot.mark_rate_limited(cooldown_sec=10.0)

        return None

    def _try_gemini_media(
        self,
        media_path: str,
        prompt: str,
        *,
        mime_type: str,
        model: str,
        temperature: float,
        retries: int,
    ) -> str | None:
        for attempt in range(retries):
            slot = self._gemini_pool.next_available()
            if slot is None:
                return None

            try:
                client = genai.Client(api_key=slot.key)

                # Upload the media file
                uploaded_file = client.files.upload(
                    file=media_path,
                    config=genai.types.UploadFileConfig(mime_type=mime_type),
                )

                response = client.models.generate_content(
                    model=model,
                    contents=[uploaded_file, prompt],
                    config=genai.types.GenerateContentConfig(
                        temperature=temperature,
                    ),
                )
                slot.mark_used()

                if response.text:
                    return response.text.strip()

                logger.warning(
                    "Gemini key #%d returned empty response for media.", slot.index
                )

            except Exception as exc:
                exc_str = str(exc).lower()
                if "model" in exc_str and ("404" in exc_str or "not found" in exc_str):
                    logger.error(
                        "Gemini key #%d: MODEL NOT FOUND (404). "
                        "Check model name is valid. Error: %s",
                        slot.index, exc,
                    )
                    return None
                elif "429" in exc_str or "rate" in exc_str or "quota" in exc_str:
                    slot.mark_rate_limited(cooldown_sec=60.0)
                else:
                    logger.error(
                        "Gemini key #%d media error: %s", slot.index, exc
                    )
                    slot.mark_rate_limited(cooldown_sec=10.0)

        return None

    # ── Groq ─────────────────────────────────

    def _try_groq(
        self,
        prompt: str,
        *,
        temperature: float,
        retries: int,
    ) -> str | None:
        for attempt in range(retries):
            slot = self._groq_pool.next_available()
            if slot is None:
                return None

            try:
                client = Groq(api_key=slot.key)
                response = client.chat.completions.create(
                    model="llama-3.1-8b-instant",
                    messages=[{"role": "user", "content": prompt}],
                    temperature=temperature,
                    max_tokens=2048,
                )
                slot.mark_used()

                text = response.choices[0].message.content
                if text:
                    return text.strip()

                logger.warning("Groq key #%d returned empty response.", slot.index)

            except Exception as exc:
                exc_str = str(exc).lower()
                if "429" in exc_str or "rate" in exc_str:
                    slot.mark_rate_limited(cooldown_sec=30.0)
                else:
                    logger.error("Groq key #%d error: %s", slot.index, exc)
                    slot.mark_rate_limited(cooldown_sec=10.0)

        return None

    # ── Diagnostics ──────────────────────────

    def get_stats(self) -> dict[str, Any]:
        """Return usage statistics for all key pools."""
        stats: dict[str, Any] = {}
        for pool_name, pool in [("gemini", self._gemini_pool), ("groq", self._groq_pool)]:
            stats[pool_name] = {
                "total_keys": len(pool.slots),
                "available_keys": sum(1 for s in pool.slots if s.is_available),
                "total_calls": sum(s.total_calls for s in pool.slots),
                "total_failures": sum(s.total_failures for s in pool.slots),
            }
        return stats

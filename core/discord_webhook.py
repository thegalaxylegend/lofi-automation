"""
Discord webhook integration for pipeline notifications.

Sends structured embeds to three Discord channels:
  #uploads   — new video published
  #daily     — daily analytics standup
  #alerts    — strategy alerts and error reports
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import requests

from core.config import Config

logger = logging.getLogger(__name__)


class DiscordNotifier:
    """Send rich embed messages to Discord via webhooks."""

    COLORS = {
        "success": 0x00D26A,   # green
        "info": 0x6C3CE1,     # purple (brand)
        "warning": 0xFFB800,  # amber
        "error": 0xFF4444,    # red
        "analytics": 0x00B4D8, # cyan
    }

    def __init__(self) -> None:
        cfg = Config()
        self._webhooks = {
            "uploads": cfg.discord_webhook_uploads,
            "daily": cfg.discord_webhook_daily,
            "alerts": cfg.discord_webhook_alerts,
        }

    def _send(self, channel: str, embed: dict[str, Any]) -> bool:
        url = self._webhooks.get(channel, "")
        if not url:
            logger.warning("Discord webhook for '%s' not configured.", channel)
            return False
        try:
            resp = requests.post(url, json={"embeds": [embed]}, timeout=10)
            resp.raise_for_status()
            logger.info("Discord message sent to #%s.", channel)
            return True
        except requests.RequestException as exc:
            logger.error("Discord send failed for #%s: %s", channel, exc)
            return False

    def notify_upload(
        self,
        title: str,
        video_url: str = "",
        thumbnail_url: str = "",
        channel_name: str = "",
    ) -> bool:
        embed = {
            "title": "🎬 New Video Uploaded",
            "description": f"**{title}**",
            "color": self.COLORS["success"],
            "fields": [
                {"name": "Channel", "value": channel_name or "Unknown", "inline": True},
                {"name": "Status", "value": "Draft (review needed)", "inline": True},
            ],
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        if video_url:
            embed["url"] = video_url
        if thumbnail_url:
            embed["thumbnail"] = {"url": thumbnail_url}
        return self._send("uploads", embed)

    def notify_daily_report(self, report: dict[str, Any]) -> bool:
        embed = {
            "title": "📊 Daily Standup",
            "color": self.COLORS["analytics"],
            "fields": [
                {"name": "Views (24h)", "value": str(report.get("views", "N/A")), "inline": True},
                {"name": "New Subs", "value": str(report.get("new_subs", "N/A")), "inline": True},
                {"name": "Best Video", "value": report.get("best_video", "N/A"), "inline": False},
                {"name": "Worst Video", "value": report.get("worst_video", "N/A"), "inline": False},
            ],
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        if "insight" in report:
            embed["footer"] = {"text": f"💡 {report['insight']}"}
        return self._send("daily", embed)

    def notify_alert(self, title: str, message: str, level: str = "warning") -> bool:
        color_key = level if level in self.COLORS else "warning"
        embed = {
            "title": f"⚠️ {title}" if level == "warning" else f"🔴 {title}",
            "description": message,
            "color": self.COLORS[color_key],
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        return self._send("alerts", embed)

    def notify_pipeline_complete(self, stats: dict[str, Any]) -> bool:
        embed = {
            "title": "✅ Pipeline Complete",
            "color": self.COLORS["success"],
            "fields": [
                {"name": "Videos Rendered", "value": str(stats.get("videos", 0)), "inline": True},
                {"name": "Shorts Created", "value": str(stats.get("shorts", 0)), "inline": True},
                {"name": "Render Time", "value": stats.get("render_time", "N/A"), "inline": True},
                {"name": "API Calls Used", "value": str(stats.get("api_calls", 0)), "inline": True},
            ],
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        return self._send("uploads", embed)

    def notify_error(self, error: str, agent: str = "unknown") -> bool:
        embed = {
            "title": f"🔴 Pipeline Error — {agent}",
            "description": f"```\n{error[:1500]}\n```",
            "color": self.COLORS["error"],
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        return self._send("alerts", embed)

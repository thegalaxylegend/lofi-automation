"""
Agent 7: The Analyst — The Self-Improvement Engine.

This is the brain of the entire organization. It:
  1. Pulls YouTube Analytics data (views, CTR, AVD, subs)
  2. Correlates performance with video attributes (mood, title, thumbnail)
  3. Generates actionable rules for other agents
  4. Sends daily/weekly/monthly reports to Discord
  5. Updates all agent memory files with learned insights
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from core.api_rotation import APIRotator
from core.config import Config
from core.discord_webhook import DiscordNotifier
from core.memory import (
    Memory,
    analyst_memory,
    director_memory,
    marketer_memory,
)

logger = logging.getLogger(__name__)

ANALYSIS_PROMPT = """You are a YouTube Analytics expert specializing in lo-fi/music channels.

Here is the recent performance data for our channel:

{analytics_data}

And here are the attributes of each video:
{video_attributes}

Previous rules we've established:
{existing_rules}

Analyze this data and return ONLY valid JSON:

{{
  "daily_summary": {{
    "total_views_24h": <int>,
    "new_subscribers": <int>,
    "best_video": "<title of best performing video>",
    "worst_video": "<title of worst performing video>",
    "avg_ctr": <float percentage>,
    "avg_avd_seconds": <float>
  }},
  "new_rules": [
    {{
      "rule": "<a specific, actionable rule for the team>",
      "target_agent": "<director|marketer|editor|distributor>",
      "confidence": "<high|medium|low>",
      "evidence": "<brief data point supporting this rule>"
    }}
  ],
  "alerts": [
    "<any urgent issues that need immediate attention>"
  ],
  "insight": "<1-2 sentence strategic insight for the channel owner>"
}}
"""


class Analyst:
    """
    The Manager of the AI Organization.
    Reads analytics, generates rules, and trains other agents.
    """

    def __init__(self) -> None:
        self.rotator = APIRotator()
        self.config = Config()
        self.discord = DiscordNotifier()
        self.memory = analyst_memory()

    def run_daily_report(
        self,
        analytics_data: dict[str, Any] | None = None,
        video_attributes: list[dict] | None = None,
    ) -> dict[str, Any]:
        """
        Generate and distribute the daily analytics report.

        Args:
            analytics_data: Raw YouTube Analytics data (views, CTR, AVD per video).
            video_attributes: List of video metadata (mood, title, thumbnail style).

        Returns:
            The complete analysis report dict.
        """
        logger.info("Analyst: generating daily report...")

        # Load existing rules
        mem = self.memory.load()
        existing_rules = mem.get("rules", [])
        rules_text = "\n".join(
            f"- [{r.get('target_agent', '?')}] {r.get('rule', '')}"
            for r in existing_rules[-10:]
        ) or "No rules established yet."

        # Format data
        analytics_str = json.dumps(analytics_data, indent=2) if analytics_data else "No analytics data available yet."
        attributes_str = json.dumps(video_attributes, indent=2) if video_attributes else "No video attributes logged yet."

        prompt = ANALYSIS_PROMPT.format(
            analytics_data=analytics_str,
            video_attributes=attributes_str,
            existing_rules=rules_text,
        )

        try:
            raw = self.rotator.generate_text(prompt, temperature=0.4, max_retries=2)
            report = self._parse_report(raw)
        except Exception as exc:
            logger.error("Analyst report generation failed: %s", exc)
            report = {"error": str(exc)}

        # Timestamp the report
        report["generated_at"] = datetime.now(timezone.utc).isoformat()

        # Save new rules to analyst memory
        new_rules = report.get("new_rules", [])
        if new_rules:
            all_rules = existing_rules + new_rules
            # Keep last 50 rules
            self.memory.update_key("rules", all_rules[-50:])
            logger.info("Analyst: %d new rules generated.", len(new_rules))

        # Propagate rules to other agents' memories
        self._propagate_rules(new_rules)

        # Save daily report
        self.memory.append_to_list("daily_reports", report, max_items=90)

        # Send to Discord
        self._send_discord_report(report)

        logger.info("✅ Analyst daily report complete.")
        return report

    def run_weekly_summary(self) -> dict[str, Any]:
        """
        Generate a weekly summary from the last 7 daily reports.
        """
        mem = self.memory.load()
        daily_reports = mem.get("daily_reports", [])[-7:]

        if not daily_reports:
            logger.warning("No daily reports available for weekly summary.")
            return {}

        prompt = (
            "You are a YouTube channel strategist. "
            "Here are the last 7 daily analytics reports:\n\n"
            f"{json.dumps(daily_reports, indent=2)}\n\n"
            "Generate a concise weekly summary with:\n"
            "1. Total views and subscriber growth for the week\n"
            "2. Top 3 performing videos and why\n"
            "3. Bottom 3 performing videos and why\n"
            "4. Key trends (rising/falling)\n"
            "5. Strategy adjustments for next week\n\n"
            "Return ONLY valid JSON with these fields."
        )

        try:
            raw = self.rotator.generate_text(prompt, temperature=0.4, max_retries=2)
            summary = self._parse_report(raw)
        except Exception as exc:
            logger.error("Weekly summary failed: %s", exc)
            summary = {"error": str(exc)}

        summary["generated_at"] = datetime.now(timezone.utc).isoformat()
        self.memory.append_to_list("weekly_reports", summary, max_items=12)

        # Send to Discord
        self.discord.notify_alert(
            title="📊 Weekly Performance Summary",
            message=json.dumps(summary.get("strategy_adjustments", summary), indent=2)[:1500],
            level="info",
        )

        return summary

    def _propagate_rules(self, rules: list[dict]) -> None:
        """Push new rules to the relevant agent memories."""
        for rule in rules:
            target = rule.get("target_agent", "")
            rule_text = rule.get("rule", "")
            confidence = rule.get("confidence", "low")

            if confidence == "low":
                continue  # Only propagate medium/high confidence rules

            if target == "director":
                dmem = director_memory()
                dmem.append_to_list("performance_log", {
                    "rule": rule_text,
                    "source": "analyst",
                    "date": datetime.now(timezone.utc).isoformat(),
                })
                logger.info("Rule propagated to Director: %s", rule_text[:60])

            elif target == "marketer":
                mmem = marketer_memory()
                # If the rule mentions avoiding certain words
                if "avoid" in rule_text.lower():
                    mmem.append_to_list("avoid_words", rule_text)
                else:
                    mmem.append_to_list("title_performance", {
                        "rule": rule_text,
                        "source": "analyst",
                    })
                logger.info("Rule propagated to Marketer: %s", rule_text[:60])

            elif target == "editor":
                emem = Memory("editor_limits")
                emem.append_to_list("analyst_feedback", {
                    "rule": rule_text,
                    "date": datetime.now(timezone.utc).isoformat(),
                })
                logger.info("Rule propagated to Editor: %s", rule_text[:60])

    def _send_discord_report(self, report: dict[str, Any]) -> None:
        """Send the daily report to Discord."""
        summary = report.get("daily_summary", {})
        insight = report.get("insight", "No insight available.")
        alerts = report.get("alerts", [])

        self.discord.notify_daily_report({
            "views": summary.get("total_views_24h", "N/A"),
            "new_subs": summary.get("new_subscribers", "N/A"),
            "best_video": summary.get("best_video", "N/A"),
            "worst_video": summary.get("worst_video", "N/A"),
            "insight": insight,
        })

        for alert in alerts:
            self.discord.notify_alert(
                title="📉 Performance Alert",
                message=alert,
                level="warning",
            )

    @staticmethod
    def _parse_report(raw: str) -> dict[str, Any]:
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            lines = cleaned.split("\n")
            lines = [l for l in lines if not l.strip().startswith("```")]
            cleaned = "\n".join(lines)
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            return {"raw": raw[:500], "parse_error": True}

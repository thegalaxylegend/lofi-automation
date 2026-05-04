"""
Agent 8: The Ghostwriter — YouTube Comment Engagement Bot.

Reads new comments on your videos via the YouTube API and generates
thoughtful, in-character replies to boost engagement.

Features:
  - Sentiment-aware responses (happy, sad, question, etc.)
  - In-character voice matching the channel brand
  - Auto-reply or queue for manual approval via Discord
  - Rate-limited to avoid spam flags
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from core.api_rotation import APIRotator
from core.config import Config
from core.discord_webhook import DiscordNotifier
from core.memory import Memory

logger = logging.getLogger(__name__)

REPLY_PROMPT = """You are the social media manager for "{channel_name}", a lo-fi YouTube channel.
Tagline: "{tagline}"

A viewer left this comment on your video "{video_title}":

"{comment_text}"

Write a warm, genuine reply that:
1. Acknowledges their comment specifically
2. Stays in character (chill, supportive, warm lo-fi vibes)
3. Is 1-3 sentences max
4. Feels human, NOT corporate or AI-generated
5. Occasionally uses a relevant emoji (don't overdo it)

If they're asking a question, answer it helpfully.
If they're expressing sadness, be supportive.
If they're praising the channel, thank them genuinely.

Reply ONLY with the response text. No quotes, no labels.
"""


@dataclass
class CommentReply:
    """A generated reply to a YouTube comment."""
    comment_id: str
    comment_text: str
    reply_text: str
    video_title: str
    auto_post: bool = False
    approved: bool = False


class Ghostwriter:
    """
    Generates authentic, in-character replies to YouTube comments.
    Can auto-post or queue for approval via Discord.
    """

    def __init__(self, auto_post: bool = False) -> None:
        self.rotator = APIRotator()
        self.config = Config()
        self.memory = Memory("ghostwriter_log")
        self.discord = DiscordNotifier()
        self.auto_post = auto_post

    def generate_reply(
        self,
        comment_id: str,
        comment_text: str,
        video_title: str,
    ) -> CommentReply:
        """
        Generate a reply to a single comment.

        Args:
            comment_id: YouTube comment ID.
            comment_text: The viewer's comment text.
            video_title: Title of the video the comment is on.

        Returns:
            CommentReply with the generated response.
        """
        channel = self.config.channel

        prompt = REPLY_PROMPT.format(
            channel_name=channel.name,
            tagline=channel.tagline,
            video_title=video_title,
            comment_text=comment_text,
        )

        try:
            reply_text = self.rotator.generate_text(
                prompt, temperature=0.8, max_retries=2
            )
            # Clean up any quotes the LLM might add
            reply_text = reply_text.strip().strip('"').strip("'")
        except Exception as exc:
            logger.error("Ghostwriter failed: %s", exc)
            reply_text = ""

        reply = CommentReply(
            comment_id=comment_id,
            comment_text=comment_text,
            reply_text=reply_text,
            video_title=video_title,
            auto_post=self.auto_post,
        )

        # Log to memory
        self.memory.append_to_list("replies", {
            "date": datetime.now(timezone.utc).isoformat(),
            "comment": comment_text[:100],
            "reply": reply_text[:100],
            "video": video_title,
        })

        logger.info("Ghostwriter reply: '%s' → '%s'", comment_text[:40], reply_text[:40])
        return reply

    def process_comments(
        self,
        comments: list[dict],
        video_title: str,
    ) -> list[CommentReply]:
        """
        Process a batch of comments and generate replies.

        Args:
            comments: List of dicts with 'id' and 'text' keys.
            video_title: Title of the video.

        Returns:
            List of CommentReply objects.
        """
        replies: list[CommentReply] = []

        for comment in comments:
            cid = comment.get("id", "")
            text = comment.get("text", "")

            if not text or len(text.strip()) < 3:
                continue

            # Skip comments that are just emojis or single words
            words = text.split()
            if len(words) < 2:
                continue

            reply = self.generate_reply(cid, text, video_title)

            if reply.reply_text:
                replies.append(reply)

                # If not auto-posting, send to Discord for approval
                if not self.auto_post:
                    self._send_to_discord_for_approval(reply)

        logger.info(
            "Ghostwriter: generated %d replies for '%s'.",
            len(replies), video_title,
        )
        return replies

    def _send_to_discord_for_approval(self, reply: CommentReply) -> None:
        """Send the reply to Discord for manual review."""
        message = (
            f"**Comment on:** {reply.video_title}\n"
            f"**Viewer said:** {reply.comment_text[:200]}\n"
            f"**Suggested reply:** {reply.reply_text}\n\n"
            f"React ✅ to approve or ❌ to reject."
        )
        self.discord.notify_alert(
            title="💬 Comment Reply Pending",
            message=message,
            level="info",
        )

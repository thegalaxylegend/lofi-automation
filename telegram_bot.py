"""
Telegram Ingestion Bot — Your Command Center.

Runs locally or on a small VPS.
Listens for MP3 files you send via Telegram, saves them to the audio/ folder,
and pushes them to GitHub to automatically trigger the Action Pipeline.
"""

import asyncio
import logging
import os
import subprocess
import re
from pathlib import Path

from core.config import PROJECT_ROOT

from dotenv import load_dotenv
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# Load config
load_dotenv()
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ALLOWED_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

AUDIO_DIR = PROJECT_ROOT / "audio"
AUDIO_DIR.mkdir(exist_ok=True)

# Telegram file download timeout (seconds) — large MP3s can take a while
FILE_DOWNLOAD_TIMEOUT = 300
# Git operation timeout (seconds)
GIT_TIMEOUT = 120


def sanitize_filename(name: str) -> str:
    """Remove special characters and spaces from filename."""
    # Split extension
    base, ext = os.path.splitext(name)
    # Keep alphanumeric, dots, and underscores in base
    base = re.sub(r"[^a-zA-Z0-9._-]", "_", base)
    # Prevent multiple underscores
    base = re.sub(r"_+", "_", base)
    return f"{base.strip('_')}{ext}"


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a welcome message."""
    user = update.effective_user
    chat_id = str(update.message.chat_id)

    if ALLOWED_CHAT_ID and chat_id != ALLOWED_CHAT_ID:
        logger.warning(f"Unauthorized access attempt from {user.first_name} (Chat ID: {chat_id})")
        return

    # Create buttons
    keyboard = [
        ["🎬 Latest Uploads", "📊 Get Report"],
        ["🛡️ Safety Status", "❓ Help"]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

    await update.message.reply_html(
        rf"Hi {user.mention_html()}! 🎧" + "\n\n"
        "I am the <b>Moodwire Ingestion Bot</b>.\n\n"
        "📍 <b>HOW TO USE:</b>\n"
        "Simply send or forward me any <b>MP3 file</b>, and I will automatically "
        "push it to the cloud pipeline to generate your video.\n\n"
        "Use the buttons below for quick status checks!",
        reply_markup=reply_markup
    )


async def handle_audio(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Download audio file and trigger GitHub push."""
    chat_id = str(update.message.chat_id)

    # Security check
    if ALLOWED_CHAT_ID and chat_id != ALLOWED_CHAT_ID:
        return

    # Check for audio file
    audio_msg = update.message.audio or update.message.voice or update.message.document

    if not audio_msg:
        await update.message.reply_text("Please send an MP3 audio file.")
        return

    # Ensure it's an audio file
    mime_type = getattr(audio_msg, "mime_type", "")
    file_name = getattr(audio_msg, "file_name", "voice_note.mp3")

    if "audio" not in mime_type and not file_name.endswith((".mp3", ".wav", ".m4a")):
        await update.message.reply_text("That doesn't look like an audio file.")
        return

    # Sanitize the filename to prevent Git/CLI issues
    file_name = sanitize_filename(file_name)

    status_msg = await update.message.reply_text(f"📥 Downloading `{file_name}`...")

    try:
        # Download the file with explicit timeout
        file = await asyncio.wait_for(
            context.bot.get_file(audio_msg.file_id),
            timeout=60,
        )
        file_path = AUDIO_DIR / file_name
        await asyncio.wait_for(
            file.download_to_drive(file_path),
            timeout=FILE_DOWNLOAD_TIMEOUT,
        )

        # Verify file was actually downloaded
        if not file_path.exists() or file_path.stat().st_size < 100:
            await status_msg.edit_text("❌ Download failed: file is empty or missing.")
            return

        file_size_mb = file_path.stat().st_size / (1024 * 1024)
        await status_msg.edit_text(
            f"✅ Downloaded `{file_name}` ({file_size_mb:.1f} MB).\n"
            f"🚀 Pushing to GitHub to trigger pipeline..."
        )

        # Write the filename + timestamp to .trigger so the pipeline knows which song to process
        # The timestamp ensures git always detects a change, even for re-sent songs
        import datetime
        trigger_file = AUDIO_DIR / ".trigger"
        trigger_content = f"{file_name}\n{datetime.datetime.now(datetime.timezone.utc).isoformat()}"
        trigger_file.write_text(trigger_content)

        # Git operations to trigger Actions
        logger.info(f"Using PROJECT_ROOT for git: {PROJECT_ROOT}")

        # Step 1: Pull latest to avoid conflicts (rebase to keep linear history)
        pull_result = subprocess.run(
            ["git", "pull", "--rebase", "--autostash"],
            capture_output=True, text=True, timeout=GIT_TIMEOUT,
            cwd=str(PROJECT_ROOT),
        )
        if pull_result.returncode != 0:
            logger.warning(f"Git pull warning: {pull_result.stderr}")
            # Don't fail on pull issues — try to push anyway

        git_cmds = [
            ["git", "add", f"audio/{file_name}", "audio/.trigger"],
            ["git", "commit", "-m", f"🎵 Auto-ingest: {file_name} from Telegram"],
            ["git", "push"],
        ]

        for cmd in git_cmds:
            try:
                result = subprocess.run(
                    cmd, capture_output=True, text=True, timeout=GIT_TIMEOUT,
                    cwd=str(PROJECT_ROOT),
                )
                if result.returncode != 0:
                    error = result.stderr or result.stdout

                    # If push fails, try pulling and pushing again
                    if cmd[1] == "push":
                        logger.warning(f"Push failed, trying pull --rebase + push: {error}")
                        subprocess.run(
                            ["git", "pull", "--rebase", "--autostash"],
                            capture_output=True, text=True, timeout=GIT_TIMEOUT,
                            cwd=PROJECT_ROOT,
                        )
                        retry = subprocess.run(
                            ["git", "push"],
                            capture_output=True, text=True, timeout=GIT_TIMEOUT,
                            cwd=PROJECT_ROOT,
                        )
                        if retry.returncode == 0:
                            continue  # Push succeeded on retry
                        error = retry.stderr or retry.stdout

                    logger.error(f"Git command failed: {' '.join(cmd)}\n{error}")
                    await status_msg.edit_text(
                        f"❌ GitHub push failed on `{' '.join(cmd)}`.\n"
                        f"Error: {error[:200]}\n"
                        f"The file was saved locally. Try running `git push` manually."
                    )
                    return
            except subprocess.TimeoutExpired:
                logger.error(f"Git command timed out: {' '.join(cmd)}")
                await status_msg.edit_text(
                    f"❌ Git command timed out: `{' '.join(cmd)}`.\n"
                    f"The file was saved locally. Check your internet connection."
                )
                return

        await status_msg.edit_text(
            f"🎉 <b>Pipeline Triggered!</b>\n\n"
            f"File: <code>{file_name}</code> is now processing in the cloud.\n"
            f"I'll ping Discord when the final video is ready.",
            parse_mode="HTML",
        )

    except asyncio.TimeoutError:
        logger.error(f"Timeout downloading file: {file_name}")
        await status_msg.edit_text(
            f"❌ Timed out downloading `{file_name}`.\n"
            f"The file may be too large. Try sending a smaller file or check your connection."
        )
    except Exception as e:
        logger.error(f"Error handling audio: {e}", exc_info=True)
        await status_msg.edit_text(f"❌ Error processing file: {e}")


def main() -> None:
    """Start the bot."""
    if not TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN not found in .env file!")
        return

    # Create the Application with extended timeouts for large file handling
    application = (
        Application.builder()
        .token(TOKEN)
        .connect_timeout(30)
        .read_timeout(60)
        .write_timeout(60)
        .build()
    )

    # Commands
    application.add_handler(CommandHandler("start", start))

    # Messages (audio files and documents)
    application.add_handler(MessageHandler(
        filters.AUDIO | filters.VOICE | filters.Document.AUDIO | filters.Document.ALL,
        handle_audio,
    ))

    # Run the bot until the user presses Ctrl-C
    logger.info(f"Starting Telegram Bot with PROJECT_ROOT: {PROJECT_ROOT}")
    logger.info("Send an MP3 to trigger the pipeline.")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()

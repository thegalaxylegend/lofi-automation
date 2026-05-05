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
from pathlib import Path

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

AUDIO_DIR = Path("audio")
AUDIO_DIR.mkdir(exist_ok=True)


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
        rf"Hi {user.mention_html()}! 🎧\n\n"
        "I am the **Moodwire Ingestion Bot**.\n\n"
        "📍 **HOW TO USE:**\n"
        "Simply send or forward me any **MP3 file**, and I will automatically "
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

    status_msg = await update.message.reply_text(f"📥 Downloading `{file_name}`...")

    try:
        # Download the file
        file = await context.bot.get_file(audio_msg.file_id)
        file_path = AUDIO_DIR / file_name
        await file.download_to_drive(file_path)
        
        await status_msg.edit_text(f"✅ Downloaded to `audio/`.\n🚀 Pushing to GitHub to trigger pipeline...")

        # Git operations to trigger Actions
        git_cmds = [
            ["git", "add", f"audio/{file_name}"],
            ["git", "commit", "--allow-empty", "-m", f"🎵 Auto-ingest: {file_name} from Telegram"],
            ["git", "push"]
        ]

        for cmd in git_cmds:
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                error = result.stderr or result.stdout
                logger.error(f"Git command failed: {' '.join(cmd)}\n{error}")
                await status_msg.edit_text(f"❌ GitHub push failed on `{' '.join(cmd)}`.\nCheck logs.")
                return

        await status_msg.edit_text(
            f"🎉 **Pipeline Triggered!**\n\n"
            f"File: `{file_name}` is now processing in the cloud.\n"
            f"I'll ping Discord when the final video is ready."
        )

    except Exception as e:
        logger.error(f"Error handling audio: {e}")
        await status_msg.edit_text(f"❌ Error processing file: {e}")


def main() -> None:
    """Start the bot."""
    if not TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN not found in .env file!")
        return

    # Create the Application
    application = Application.builder().token(TOKEN).build()

    # Commands
    application.add_handler(CommandHandler("start", start))

    # Messages (audio files and documents)
    application.add_handler(MessageHandler(filters.AUDIO | filters.VOICE | filters.Document.AUDIO, handle_audio))

    # Run the bot until the user presses Ctrl-C
    logger.info("Starting Telegram Bot... Send an MP3 to trigger the pipeline.")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()

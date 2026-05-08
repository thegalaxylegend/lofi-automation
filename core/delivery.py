import os
import requests
from pathlib import Path
import logging

logger = logging.getLogger(__name__)

def send_to_telegram():
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    
    if not token or not chat_id:
        print("Telegram credentials missing in environment.")
        return

    output_dir = Path("output")
    if not output_dir.exists():
        print("No output directory found.")
        return

    # Find the video and thumbnail
    videos = list(output_dir.glob("*.mp4"))
    thumbs = list(output_dir.glob("*.jpg"))
    texts = list(output_dir.glob("*_metadata.txt"))

    if not videos:
        print("No videos found to send.")
        return

    video_path = videos[0]
    thumb_path = thumbs[0] if thumbs else None
    
    # 1. Send Notification
    caption = f"✅ *Video Render Complete!*\n\n"
    if texts:
        with open(texts[0], "r", encoding="utf-8") as f:
            meta = f.read()
            # Extract title if possible
            for line in meta.split("\n"):
                if line.upper().startswith("TITLE:"):
                    caption += f"🎬 *{line}*\n"
    
    caption += f"\n📁 File: `{video_path.name}`"

    # 2. Send Video (if < 50MB)
    file_size_mb = video_path.stat().st_size / (1024 * 1024)
    
    if file_size_mb < 50:
        print(f"Sending video ({file_size_mb:.1f}MB) to Telegram...")
        url = f"https://api.telegram.org/bot{token}/sendVideo"
        with open(video_path, "rb") as v_file:
            files = {"video": v_file}
            data = {"chat_id": chat_id, "caption": caption, "parse_mode": "Markdown"}
            if thumb_path:
                with open(thumb_path, "rb") as t_file:
                    files["thumb"] = t_file
                    response = requests.post(url, data=data, files=files)
            else:
                response = requests.post(url, data=data, files=files)
        
        if response.status_code == 200:
            print("Video sent successfully!")
        else:
            print(f"Failed to send video: {response.text}")
    else:
        # Send just the thumbnail and metadata if too large
        print(f"Video too large ({file_size_mb:.1f}MB). Sending notification only.")
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        msg = caption + f"\n\n⚠️ *File size ({file_size_mb:.1f}MB) exceeds Telegram limit.*\nDownload it from GitHub Actions Artifacts."
        requests.post(url, data={"chat_id": chat_id, "text": msg, "parse_mode": "Markdown"})

if __name__ == "__main__":
    send_to_telegram()

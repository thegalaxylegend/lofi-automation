import os
import logging
from pathlib import Path
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

from core.config import Config

logger = logging.getLogger(__name__)

class YouTubeUploader:
    """Handles uploading videos to YouTube using OAuth2."""

    def __init__(self):
        self.config = Config()
        self.youtube = self._get_service()

    def _get_service(self):
        client_id = self.config.youtube_client_id
        client_secret = self.config.youtube_client_secret
        refresh_token = self.config.youtube_refresh_token

        if not all([client_id, client_secret, refresh_token]):
            logger.error("YouTube credentials missing in environment.")
            return None

        creds = Credentials(
            token=None,
            refresh_token=refresh_token,
            client_id=client_id,
            client_secret=client_secret,
            token_uri="https://oauth2.googleapis.com/token",
        )

        # Refresh token if needed
        if not creds.valid:
            creds.refresh(Request())

        return build("youtube", "v3", credentials=creds)

    def upload_video(self, video_path: Path, title: str, description: str, tags: list = None, category_id: str = "10", privacy_status: str = "private"):
        """
        Uploads a video to YouTube.
        
        Args:
            video_path: Path to the video file.
            title: Video title.
            description: Video description.
            tags: List of tags.
            category_id: YouTube category ID (default "10" for Music).
            privacy_status: "public", "private", or "unlisted".
        """
        if not self.youtube:
            logger.error("YouTube service not initialized.")
            return None

        if not video_path.exists():
            logger.error(f"Video file not found: {video_path}")
            return None

        logger.info(f"Uploading video: {video_path}")

        body = {
            "snippet": {
                "title": title,
                "description": description,
                "tags": tags or [],
                "categoryId": category_id
            },
            "status": {
                "privacyStatus": privacy_status,
                "selfDeclaredMadeForKids": False
            }
        }

        media = MediaFileUpload(
            str(video_path),
            mimetype="video/mp4",
            resumable=True
        )

        request = self.youtube.videos().insert(
            part="snippet,status",
            body=body,
            media_body=media
        )

        response = None
        while response is None:
            status, response = request.next_chunk()
            if status:
                logger.info(f"Uploaded {int(status.progress() * 100)}%")

        logger.info(f"✅ Video uploaded! ID: {response['id']}")
        return response["id"]

    def set_thumbnail(self, video_id: str, thumbnail_path: Path):
        """Sets the thumbnail for a video."""
        if not self.youtube:
            return

        if not thumbnail_path.exists():
            logger.error(f"Thumbnail file not found: {thumbnail_path}")
            return

        logger.info(f"Setting thumbnail for video {video_id}...")
        self.youtube.thumbnails().set(
            videoId=video_id,
            media_body=MediaFileUpload(str(thumbnail_path))
        ).execute()
        logger.info("✅ Thumbnail set successfully.")

if __name__ == "__main__":
    # Test upload if run directly
    logging.basicConfig(level=logging.INFO)
    # uploader = YouTubeUploader()
    # uploader.upload_video(Path("output/test.mp4"), "Test Title", "Test Description")

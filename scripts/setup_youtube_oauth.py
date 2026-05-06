import os
import sys
from pathlib import Path
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from dotenv import load_dotenv, set_key

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(PROJECT_ROOT))

ENV_PATH = PROJECT_ROOT / ".env"

# Load existing .env
load_dotenv(ENV_PATH)

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]

def setup_oauth():
    client_id = os.getenv("YOUTUBE_CLIENT_ID")
    client_secret = os.getenv("YOUTUBE_CLIENT_SECRET")

    if not client_id or not client_secret:
        print("❌ Error: YOUTUBE_CLIENT_ID or YOUTUBE_CLIENT_SECRET missing in .env")
        print("Please follow these steps:")
        print("1. Go to Google Cloud Console (https://console.cloud.google.com/)")
        print("2. Create a new project or select an existing one.")
        print("3. Enable 'YouTube Data API v3'.")
        print("4. Go to 'Credentials' -> 'Create Credentials' -> 'OAuth client ID'.")
        print("5. Select 'Desktop App'.")
        print("6. Copy the Client ID and Client Secret to your .env file.")
        return

    # Flow for desktop app
    flow = InstalledAppFlow.from_client_config(
        {
            "installed": {
                "client_id": client_id,
                "client_secret": client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        },
        SCOPES
    )

    print("\nStarting OAuth flow. Your browser will open...")
    # Using local server flow
    creds = flow.run_local_server(port=0)

    if creds and creds.refresh_token:
        print("\nSuccess! Got Refresh Token.")
        print(f"Refresh Token: {creds.refresh_token}")
        
        # Save to .env
        set_key(str(ENV_PATH), "YOUTUBE_REFRESH_TOKEN", creds.refresh_token)
        print(f"Saved YOUTUBE_REFRESH_TOKEN to {ENV_PATH}")
    else:
        print("\nFailed to get refresh token. Make sure you use a Google account that is added as a 'Test User' in your Google Cloud Project's OAuth consent screen.")

if __name__ == "__main__":
    setup_oauth()

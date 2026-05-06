# YouTube Upload Setup Guide

This guide will help you set up the YouTube OAuth2 credentials required to upload your generated lo-fi videos and shorts directly to your YouTube channel as drafts.

## 1. Create Google Cloud Project Credentials

1.  **Go to the Google Cloud Console**: [console.cloud.google.com](https://console.cloud.google.com/)
2.  **Create a New Project** (or select an existing one).
3.  **Enable YouTube Data API v3**:
    - Search for "YouTube Data API v3" in the search bar.
    - Click **Enable**.
4.  **Configure OAuth Consent Screen**:
    - Go to **APIs & Services** > **OAuth consent screen**.
    - Select **External**.
    - Fill in the required app information (App name, support email, developer contact).
    - **Scopes**: Add `https://www.googleapis.com/auth/youtube.upload`.
    - **Test Users**: Add your own YouTube account email as a test user.
5.  **Create Credentials**:
    - Go to **APIs & Services** > **Credentials**.
    - Click **+ CREATE CREDENTIALS** > **OAuth client ID**.
    - Application type: **Desktop app**.
    - Name: `Lo-fi Automation`.
    - Click **Create**.
6.  **Copy Credentials**:
    - Copy the **Client ID** and **Client Secret**.

## 2. Update .env File

Open your `.env` file and paste the values:

```env
YOUTUBE_CLIENT_ID=your_client_id_here
YOUTUBE_CLIENT_SECRET=your_client_secret_here
```

## 3. Generate Refresh Token

Run the setup script I created for you:

```powershell
python scripts/setup_youtube_oauth.py
```

- This will open a browser window.
- Log in with your YouTube account.
- You might see a "Google hasn't verified this app" warning (since it's your own private app). Click **Advanced** > **Go to Lo-fi Automation (unsafe)**.
- Grant the permissions.
- The script will automatically catch the code and save the `YOUTUBE_REFRESH_TOKEN` to your `.env` file.

## 4. Automatic Uploading

The pipeline is now configured to automatically upload:
1.  **Main Video**: Uploaded as a draft (private) with the generated title, description, and tags.
2.  **Thumbnail**: Automatically set for the main video.
3.  **Short**: Uploaded as a draft with `#shorts` in the title.

You can verify the upload in your [YouTube Studio](https://studio.youtube.com/) under "Content".

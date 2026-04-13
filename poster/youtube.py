import os
import logging
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
import pickle

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
TOKEN_FILE = "youtube_token.pickle"
CLIENT_SECRET_FILE = os.getenv("YOUTUBE_CLIENT_SECRET_FILE", "client_secret.json")

# Template yang otomatis ditambahkan di bawah setiap deskripsi
YT_DESCRIPTION_TEMPLATE = """
---
🔥 Get TitanChess Engine: https://titanchess.online
💬 Join our Telegram: https://t.me/TitanChessss
♟️ Play on Chess.com: https://chess.com

#chess #titanchess #shorts #chesscom #bullet #blitz
"""


def get_youtube_service():
    """Get authenticated YouTube service. Handles token refresh automatically."""
    creds = None

    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE, "rb") as f:
            creds = pickle.load(f)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRET_FILE, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(TOKEN_FILE, "wb") as f:
            pickle.dump(creds, f)

    return build("youtube", "v3", credentials=creds)


def upload_video(video_path: str, title: str, description: str = "", tags: list = None, privacy: str = "public") -> dict:
    """
    Upload video ke YouTube.

    Args:
        video_path: Path ke file video
        title: Judul video
        description: Deskripsi video
        tags: List of tags
        privacy: "public", "private", atau "unlisted"

    Returns:
        {"success": bool, "video_id": str, "url": str, "error": str}
    """
    try:
        youtube = get_youtube_service()

        # Append template ke description
        full_description = description.strip() + "\n" + YT_DESCRIPTION_TEMPLATE.strip()

        body = {
            "snippet": {
                "title": title,
                "description": full_description,
                "tags": tags or ["chess", "titanchess", "chesscom"],
                "categoryId": "20",  # Gaming
            },
            "status": {
                "privacyStatus": privacy,
                "selfDeclaredMadeForKids": False,
            },
        }

        media = MediaFileUpload(video_path, mimetype="video/mp4", resumable=True, chunksize=1024 * 1024 * 5)

        logger.info(f"📤 Uploading to YouTube: {title}")
        request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)

        response = None
        while response is None:
            status, response = request.next_chunk()
            if status:
                logger.info(f"Upload progress: {int(status.progress() * 100)}%")

        video_id = response["id"]
        url = f"https://youtu.be/{video_id}"
        logger.info(f"✅ YouTube upload success: {url}")
        return {"success": True, "video_id": video_id, "url": url, "error": None}

    except Exception as e:
        logger.error(f"❌ YouTube upload failed: {e}")
        return {"success": False, "video_id": None, "url": None, "error": str(e)}

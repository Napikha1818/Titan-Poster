import os
import logging
from instagrapi import Client

logger = logging.getLogger(__name__)

IG_USERNAME = os.getenv("IG_USERNAME", "")
IG_PASSWORD = os.getenv("IG_PASSWORD", "")
IG_SESSION_FILE = os.path.join(os.path.dirname(__file__), "..", "ig_session.json")


def _get_client() -> Client:
    """Login ke Instagram. Pakai session file kalau ada."""
    cl = Client()
    if os.path.exists(IG_SESSION_FILE):
        try:
            cl.load_settings(IG_SESSION_FILE)
            cl.login(IG_USERNAME, IG_PASSWORD)
            logger.info("🔑 IG: logged in via session file")
            return cl
        except Exception as e:
            logger.warning(f"⚠️ IG session expired: {e}")

    cl.login(IG_USERNAME, IG_PASSWORD)
    cl.dump_settings(IG_SESSION_FILE)
    logger.info("🔑 IG: fresh login, session saved")
    return cl


def upload_reel(video_path: str, caption: str) -> dict:
    """Upload video sebagai Instagram Reel."""
    try:
        if not IG_USERNAME or not IG_PASSWORD:
            return {
                "success": False,
                "error": "IG_USERNAME dan IG_PASSWORD belum diset di .env"
            }

        logger.info(f"📤 Uploading to Instagram Reels: {caption[:50]}...")
        cl = _get_client()
        media = cl.clip_upload(video_path, caption)
        url = f"https://www.instagram.com/reel/{media.code}/"
        logger.info(f"✅ Instagram upload success: {url}")
        return {"success": True, "url": url, "error": None}

    except Exception as e:
        error_str = str(e)
        logger.error(f"❌ Instagram upload failed: {error_str}")

        if "login_required" in error_str.lower() or "challenge" in error_str.lower():
            return {
                "success": False,
                "error": "🔑 IG SESSION EXPIRED!\n\nLogin ulang diperlukan.\nHapus ig_session.json di VPS lalu restart bot."
            }

        return {"success": False, "error": error_str}

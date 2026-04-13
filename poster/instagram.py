import os
import logging
from instagrapi import Client

logger = logging.getLogger(__name__)

IG_SESSION_FILE = os.path.join(os.path.dirname(__file__), "..", "ig_session.json")


def _get_client() -> Client:
    """Login ke Instagram. Pakai session file kalau ada."""
    cl = Client()
    username = os.getenv("IG_USERNAME", "")
    password = os.getenv("IG_PASSWORD", "")
    if os.path.exists(IG_SESSION_FILE):
        try:
            cl.load_settings(IG_SESSION_FILE)
            cl.login(username, password)
            logger.info("🔑 IG: logged in via session file")
            return cl
        except Exception as e:
            logger.warning(f"⚠️ IG session expired: {e}")

    cl.login(username, password)
    cl.dump_settings(IG_SESSION_FILE)
    logger.info("🔑 IG: fresh login, session saved")
    return cl


def upload_reel(video_path: str, caption: str) -> dict:
    """Upload video sebagai Instagram Reel."""
    try:
        if not os.getenv("IG_USERNAME") or not os.getenv("IG_PASSWORD"):
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

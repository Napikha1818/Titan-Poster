import os
import logging
from datetime import datetime
from tiktok_uploader.upload import TikTokUploader

logger = logging.getLogger(__name__)

COOKIES_FILE = os.getenv("TIKTOK_COOKIES_FILE", "tiktok_cookies.txt")

# Auto-dismiss joyride overlay setiap 300ms
DISMISS_OVERLAY_JS = """
setInterval(() => {
    const portal = document.getElementById('react-joyride-portal');
    if (portal) portal.remove();
    document.querySelectorAll('[data-test-id="overlay"]').forEach(el => el.remove());
    document.querySelectorAll('.react-joyride__overlay').forEach(el => el.remove());
}, 300);
"""


class PatchedUploader(TikTokUploader):
    """TikTokUploader with auto-dismiss joyride overlay."""

    @property
    def page(self):
        page = super().page
        # Inject dismiss script ke setiap page baru
        if not getattr(self, '_patched', False):
            page.context.add_init_script(DISMISS_OVERLAY_JS)
            try:
                page.evaluate(DISMISS_OVERLAY_JS.replace("setInterval", "setTimeout"))
            except Exception:
                pass
            self._patched = True
        return page


def upload_video(video_path: str, caption: str, schedule_time: datetime = None) -> dict:
    """Upload video ke TikTok dengan auto-dismiss overlay."""
    try:
        if not os.path.exists(COOKIES_FILE):
            return {"success": False, "error": "TikTok cookies file tidak ditemukan."}

        logger.info(f"📤 Uploading to TikTok: {caption[:50]}...")

        uploader = PatchedUploader(cookies=COOKIES_FILE, headless=True)

        kwargs = {}
        if schedule_time:
            kwargs["schedule"] = schedule_time

        success = uploader.upload_video(video_path, description=caption, **kwargs)

        if success:
            logger.info("✅ TikTok upload success")
            return {"success": True, "error": None}
        else:
            return {"success": False, "error": "Upload gagal (video mungkin tidak ter-post)"}

    except Exception as e:
        logger.error(f"❌ TikTok upload failed: {e}")
        return {"success": False, "error": str(e)}

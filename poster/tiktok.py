import os
import logging
from datetime import datetime
from tiktok_uploader.upload import TikTokUploader
from tiktok_uploader.browsers import get_browser
from playwright.sync_api import sync_playwright

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
    """TikTokUploader with overlay dismiss + proper headless args for VPS."""

    @property
    def page(self):
        if self._page is None:
            logger.debug("Creating patched browser instance...")
            pw = sync_playwright().start()
            browser = pw.chromium.launch(
                headless=True,
                args=[
                    '--no-sandbox',
                    '--disable-setuid-sandbox',
                    '--disable-dev-shm-usage',
                    '--disable-gpu',
                    '--single-process',
                ]
            )
            context = browser.new_context(
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                viewport={'width': 1280, 'height': 720},
            )
            context.add_init_script(DISMISS_OVERLAY_JS)
            self._page = context.new_page()
            self._page = self.auth.authenticate_agent(self._page)
        return self._page


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
        error_str = str(e)
        logger.error(f"❌ TikTok upload failed: {error_str}")

        # Detect cookie expired
        if "login" in error_str.lower() or "logged out" in error_str.lower() or "redirect" in error_str.lower():
            return {
                "success": False,
                "error": "🔑 COOKIE EXPIRED!\n\nCookies TikTok sudah kadaluarsa.\n\nCara update:\n1. Buka tiktok.com di Chrome, pastikan login\n2. F12 → Console → jalankan script export cookies\n3. Update file tiktok_cookies.txt di VPS"
            }

        return {"success": False, "error": error_str}

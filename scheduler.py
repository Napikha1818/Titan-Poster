import os
import json
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)
WIB = ZoneInfo("Asia/Jakarta")
SCHEDULE_FILE = os.path.join(os.path.dirname(__file__), "scheduled_posts.json")


def _load() -> list:
    if not os.path.exists(SCHEDULE_FILE):
        return []
    with open(SCHEDULE_FILE, "r") as f:
        return json.load(f)


def _save(posts: list):
    with open(SCHEDULE_FILE, "w") as f:
        json.dump(posts, f, indent=2, default=str)


def add_post(chat_id: int, video_path: str, caption: str, scheduled_time: datetime) -> int:
    """Tambah jadwal. Returns post ID."""
    posts = _load()
    post_id = max([p["id"] for p in posts], default=0) + 1
    posts.append({
        "id": post_id,
        "chat_id": chat_id,
        "video_path": video_path,
        "caption": caption,
        "scheduled_time": scheduled_time.isoformat(),
        "status": "pending",
    })
    _save(posts)
    logger.info(f"📅 Scheduled post #{post_id} at {scheduled_time}")
    return post_id


def get_pending() -> list:
    """Ambil semua post pending."""
    return [p for p in _load() if p["status"] == "pending"]


def mark_done(post_id: int, result: str = "success"):
    posts = _load()
    for p in posts:
        if p["id"] == post_id:
            p["status"] = "done"
            p["result"] = result
    _save(posts)


def mark_failed(post_id: int, error: str):
    posts = _load()
    for p in posts:
        if p["id"] == post_id:
            p["status"] = "failed"
            p["error"] = error
    _save(posts)


def remove_post(post_id: int) -> bool:
    posts = _load()
    new_posts = [p for p in posts if p["id"] != post_id]
    if len(new_posts) == len(posts):
        return False
    _save(new_posts)
    return True


def parse_wib_datetime(dt_str: str) -> datetime | None:
    """Parse 'YYYY-MM-DD HH:MM' ke datetime WIB."""
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            dt = datetime.strptime(dt_str, fmt)
            return dt.replace(tzinfo=WIB)
        except ValueError:
            continue
    return None

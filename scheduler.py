import os
import json
import logging
from datetime import datetime
from zoneinfo import ZoneInfo
from google.cloud import firestore

logger = logging.getLogger(__name__)
WIB = ZoneInfo("Asia/Jakarta")
db = None


def get_db():
    global db
    if db is None:
        db = firestore.Client()
    return db


def save_scheduled_post(chat_id: int, platform: str, video_path: str, metadata: dict, scheduled_time: datetime) -> str:
    """
    Simpan jadwal post ke Firestore.

    Returns:
        doc_id: ID dokumen Firestore
    """
    doc = {
        "chat_id": chat_id,
        "platform": platform,          # "youtube" | "tiktok"
        "video_path": video_path,
        "metadata": metadata,          # title, description, caption, tags, privacy
        "scheduled_time": scheduled_time,
        "status": "pending",
        "created_at": datetime.now(WIB),
    }
    ref = get_db().collection("scheduled_posts").add(doc)
    doc_id = ref[1].id
    logger.info(f"📅 Scheduled post saved: {doc_id} at {scheduled_time}")
    return doc_id


def get_pending_posts(now: datetime = None) -> list:
    """Ambil semua post yang sudah waktunya dieksekusi."""
    if now is None:
        now = datetime.now(WIB)

    docs = (
        get_db()
        .collection("scheduled_posts")
        .where("status", "==", "pending")
        .where("scheduled_time", "<=", now)
        .stream()
    )
    return [{"id": d.id, **d.to_dict()} for d in docs]


def mark_done(doc_id: str, result: dict):
    """Update status post setelah dieksekusi."""
    get_db().collection("scheduled_posts").document(doc_id).update({
        "status": "done",
        "result": result,
        "executed_at": datetime.now(WIB),
    })


def mark_failed(doc_id: str, error: str):
    """Update status post jika gagal."""
    get_db().collection("scheduled_posts").document(doc_id).update({
        "status": "failed",
        "error": error,
        "executed_at": datetime.now(WIB),
    })


def parse_wib_datetime(dt_str: str) -> datetime | None:
    """
    Parse string datetime WIB.
    Format yang diterima: "2026-04-15 20:00" atau "2026-04-15 20:00:00"

    Returns:
        datetime dengan timezone WIB, atau None jika format salah
    """
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            dt = datetime.strptime(dt_str, fmt)
            return dt.replace(tzinfo=WIB)
        except ValueError:
            continue
    return None

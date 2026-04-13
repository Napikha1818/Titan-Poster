import os
import re
import logging
import asyncio
import requests
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    ConversationHandler, filters, ContextTypes,
)

from poster.tiktok import upload_video as tt_upload

load_dotenv()
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OWNER_ID = int(os.getenv("TELEGRAM_OWNER_ID", "0"))

WAIT_VIDEO, WAIT_CAPTION = range(2)

GDRIVE_REGEX = r"(?:https?:\/\/)?(?:drive\.google\.com\/(?:file\/d\/|open\?id=)|docs\.google\.com\/file\/d\/)([a-zA-Z0-9_-]{33,})"


def is_owner(update: Update) -> bool:
    return update.effective_user.id == OWNER_ID


def download_gdrive(file_id: str, dest: str) -> bool:
    """Download file publik dari Google Drive."""
    try:
        session = requests.Session()
        url = f"https://drive.google.com/uc?export=download&id={file_id}"
        resp = session.get(url, stream=True, allow_redirects=True)

        # Handle confirm token untuk file besar
        confirm = None
        for k, v in resp.cookies.items():
            if k.startswith("download_warning"):
                confirm = v
                break

        if confirm is None and resp.headers.get("content-type", "").startswith("text/html"):
            content = resp.content.decode("utf-8", errors="ignore")
            match = re.search(r"confirm=([0-9A-Za-z_-]+)", content)
            if match:
                confirm = match.group(1)

        if confirm:
            resp = session.get(url, params={"confirm": confirm, "id": file_id}, stream=True)

        # Fallback URL
        if resp.headers.get("content-type", "").startswith("text/html"):
            alt = f"https://drive.usercontent.google.com/download?id={file_id}&export=download&confirm=t"
            resp = session.get(alt, stream=True)

        with open(dest, "wb") as f:
            for chunk in resp.iter_content(8192):
                if chunk:
                    f.write(chunk)

        size = os.path.getsize(dest) if os.path.exists(dest) else 0
        return size > 10240  # minimal 10KB
    except Exception as e:
        logger.error(f"GDrive download error: {e}")
        return False


# ── /start ───────────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        await update.message.reply_text("❌ Unauthorized.")
        return
    await update.message.reply_text(
        "👋 *TitanChess Auto-Poster*\n\n"
        "Commands:\n"
        "/post — upload video ke TikTok\n"
        "/cancel — batalkan proses\n\n"
        "Support: video langsung atau Google Drive link",
        parse_mode="Markdown",
    )


# ── /post flow ───────────────────────────────────────────────────────────

async def post_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        return ConversationHandler.END
    context.user_data.clear()
    await update.message.reply_text(
        "📹 Kirim videonya sekarang.\n"
        "Bisa kirim file langsung (sampai 2GB) atau Google Drive link."
    )
    return WAIT_VIDEO


async def receive_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    video = update.message.video or update.message.document
    if not video:
        await update.message.reply_text("❌ Bukan video. Kirim file video (MP4).")
        return WAIT_VIDEO

    status = await update.message.reply_text("⬇️ Downloading video...")
    try:
        tmp_dir = os.path.join(os.getcwd(), "videos")
        os.makedirs(tmp_dir, exist_ok=True)
        file_name = getattr(video, "file_name", None) or f"video_{video.file_id}.mp4"
        file_path = os.path.join(tmp_dir, file_name)

        tg_file = await context.bot.get_file(video.file_id)
        await tg_file.download_to_drive(file_path)
        context.user_data["video_path"] = file_path

        size_mb = os.path.getsize(file_path) / (1024 * 1024)
        await status.edit_text(f"✅ Video diterima ({size_mb:.1f}MB)!\n\n📝 Masukkan caption (termasuk hashtag):")
        return WAIT_CAPTION
    except Exception as e:
        await status.edit_text(f"❌ Gagal download video: {e}")
        return ConversationHandler.END


async def receive_gdrive_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    match = re.search(GDRIVE_REGEX, text)
    if not match:
        await update.message.reply_text("❌ Bukan video atau Google Drive link yang valid.\nKirim file video atau link Google Drive.")
        return WAIT_VIDEO

    gdrive_id = match.group(1)
    status = await update.message.reply_text("📥 Downloading dari Google Drive...")

    tmp_dir = os.path.join(os.getcwd(), "videos")
    os.makedirs(tmp_dir, exist_ok=True)
    file_path = os.path.join(tmp_dir, f"gdrive_{gdrive_id}.mp4")

    success = await asyncio.to_thread(download_gdrive, gdrive_id, file_path)
    if not success:
        await status.edit_text(
            "❌ Gagal download dari Google Drive.\n"
            "Pastikan sharing diset ke 'Anyone with the link'."
        )
        return ConversationHandler.END

    size_mb = os.path.getsize(file_path) / (1024 * 1024)
    context.user_data["video_path"] = file_path
    await status.edit_text(f"✅ Video didownload ({size_mb:.1f}MB)!\n\n📝 Masukkan caption (termasuk hashtag):")
    return WAIT_CAPTION


async def receive_caption(update: Update, context: ContextTypes.DEFAULT_TYPE):
    caption = update.message.text.strip()
    video_path = context.user_data.get("video_path")
    if not video_path:
        await update.message.reply_text("❌ Video tidak ditemukan. Mulai ulang dengan /post")
        return ConversationHandler.END

    status = await update.message.reply_text("⏳ Uploading ke TikTok...")
    result = await asyncio.to_thread(tt_upload, video_path, caption)

    if result["success"]:
        await status.edit_text("✅ Video berhasil di-upload ke TikTok!")
    else:
        await status.edit_text(f"❌ Upload gagal:\n{result['error']}")
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("❌ Dibatalkan.")
    return ConversationHandler.END


# ── Main ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if not TOKEN:
        print("❌ TELEGRAM_BOT_TOKEN tidak ditemukan di .env!")
        exit(1)

    USE_LOCAL = os.getenv("USE_LOCAL_API_SERVER", "false").lower() == "true"
    LOCAL_API_URL = os.getenv("LOCAL_API_URL", "http://127.0.0.1:8081/bot")
    LOCAL_FILE_URL = os.getenv("LOCAL_FILE_URL", "http://127.0.0.1:8081/file/bot")

    print("🤖 Starting TitanChess Poster Bot (polling mode)...")
    builder = ApplicationBuilder().token(TOKEN)

    if USE_LOCAL:
        print("⚡ Local API Server aktif — support file sampai 2GB!")
        builder.base_url(LOCAL_API_URL)
        builder.base_file_url(LOCAL_FILE_URL)
        builder.local_mode(True)

    app = builder.build()

    conv = ConversationHandler(
        entry_points=[CommandHandler("post", post_start)],
        states={
            WAIT_VIDEO: [
                MessageHandler(filters.VIDEO | filters.Document.ALL, receive_video),
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_gdrive_link),
            ],
            WAIT_CAPTION: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_caption),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(conv)

    print("✅ Bot siap! Buka Telegram dan kirim /start")
    app.run_polling()

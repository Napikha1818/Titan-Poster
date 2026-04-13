import os
import re
import logging
import asyncio
import requests
import psutil
import platform
from datetime import datetime
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
from telegram import Update, Bot
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    ConversationHandler, filters, ContextTypes,
)
from apscheduler.schedulers.background import BackgroundScheduler

from poster.tiktok import upload_video as tt_upload
from scheduler import add_post, get_pending, mark_done, mark_failed, remove_post, parse_wib_datetime

load_dotenv()
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OWNER_ID = int(os.getenv("TELEGRAM_OWNER_ID", "0"))
WIB = ZoneInfo("Asia/Jakarta")

# Conversation states
WAIT_VIDEO, WAIT_CAPTION, WAIT_SCHEDULE_TIME = range(3)

GDRIVE_REGEX = r"(?:https?:\/\/)?(?:drive\.google\.com\/(?:file\/d\/|open\?id=)|docs\.google\.com\/file\/d\/)([a-zA-Z0-9_-]{33,})"


def is_owner(update: Update) -> bool:
    return update.effective_user.id == OWNER_ID


def download_gdrive(file_id: str, dest: str) -> bool:
    try:
        session = requests.Session()
        url = f"https://drive.google.com/uc?export=download&id={file_id}"
        resp = session.get(url, stream=True, allow_redirects=True)
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
        if resp.headers.get("content-type", "").startswith("text/html"):
            alt = f"https://drive.usercontent.google.com/download?id={file_id}&export=download&confirm=t"
            resp = session.get(alt, stream=True)
        with open(dest, "wb") as f:
            for chunk in resp.iter_content(8192):
                if chunk:
                    f.write(chunk)
        size = os.path.getsize(dest) if os.path.exists(dest) else 0
        return size > 10240
    except Exception as e:
        logger.error(f"GDrive download error: {e}")
        return False


# ── /start ───────────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        await update.message.reply_text("Unauthorized.")
        return
    await update.message.reply_text(
        "👋 *TitanChess Auto-Poster*\n\n"
        "*Upload Sekarang:*\n"
        "/post — kirim video, langsung upload ke TikTok\n\n"
        "*Jadwalkan Upload:*\n"
        "/schedule — kirim video, atur waktu posting\n"
        "/pending — lihat semua jadwal yang belum diposting\n"
        "/cancelschedule 1 — batalkan jadwal (ganti 1 dengan ID)\n\n"
        "*Lainnya:*\n"
        "/info — cek status VPS\n"
        "/cancel — batalkan proses yang sedang berjalan\n\n"
        "📹 Kirim video langsung atau Google Drive link",
        parse_mode="Markdown",
    )


async def info_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        return
    try:
        uname = platform.uname()
        cpu_usage = psutil.cpu_percent(interval=1)
        cpu_count = psutil.cpu_count(logical=True)
        ram = psutil.virtual_memory()
        swap = psutil.swap_memory()
        disk = psutil.disk_usage('/')
        boot_time = datetime.fromtimestamp(int(psutil.boot_time())).strftime("%d %b %Y %H:%M")
        vid_dir = os.path.join(os.getcwd(), "videos")
        vid_count = len([f for f in os.listdir(vid_dir) if f.endswith('.mp4')]) if os.path.exists(vid_dir) else 0
        vid_size = sum(os.path.getsize(os.path.join(vid_dir, f)) for f in os.listdir(vid_dir) if os.path.exists(os.path.join(vid_dir, f))) / (1024**2) if os.path.exists(vid_dir) else 0
        msg = (
            f"🖥️ VPS Status\n\n"
            f"💻 OS: {uname.system} {uname.release}\n"
            f"⚙️ CPU: {cpu_usage}% ({cpu_count} cores)\n"
            f"🧠 RAM: {ram.used // (1024**2)}MB / {ram.total // (1024**2)}MB ({ram.percent}%)\n"
            f"💾 Swap: {swap.used // (1024**2)}MB / {swap.total // (1024**2)}MB\n"
            f"📀 Disk: {disk.used // (1024**3)}GB / {disk.total // (1024**3)}GB ({disk.percent}%)\n"
            f"🕐 Boot: {boot_time}\n"
            f"📹 Videos: {vid_count} files ({vid_size:.1f}MB)"
        )
        await update.message.reply_text(msg)
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")


# ── /post flow (upload sekarang) ─────────────────────────────────────────

async def post_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        return ConversationHandler.END
    context.user_data.clear()
    context.user_data["mode"] = "now"
    await update.message.reply_text("📹 Kirim videonya sekarang.\nBisa kirim file langsung atau Google Drive link.")
    return WAIT_VIDEO


# ── /schedule flow ───────────────────────────────────────────────────────

async def schedule_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        return ConversationHandler.END
    context.user_data.clear()
    context.user_data["mode"] = "schedule"
    await update.message.reply_text("📹 Kirim videonya sekarang.\nBisa kirim file langsung atau Google Drive link.")
    return WAIT_VIDEO


# ── Shared video/caption handlers ────────────────────────────────────────

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
        tg_file = await context.bot.get_file(video.file_id, read_timeout=300, write_timeout=300, connect_timeout=60)
        await tg_file.download_to_drive(file_path, read_timeout=1800, write_timeout=1800, connect_timeout=120)
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
        await update.message.reply_text("❌ Bukan video atau Google Drive link yang valid.")
        return WAIT_VIDEO
    gdrive_id = match.group(1)
    status = await update.message.reply_text("📥 Downloading dari Google Drive...")
    tmp_dir = os.path.join(os.getcwd(), "videos")
    os.makedirs(tmp_dir, exist_ok=True)
    file_path = os.path.join(tmp_dir, f"gdrive_{gdrive_id}.mp4")
    success = await asyncio.to_thread(download_gdrive, gdrive_id, file_path)
    if not success:
        await status.edit_text("❌ Gagal download dari Google Drive.\nPastikan sharing diset ke 'Anyone with the link'.")
        return ConversationHandler.END
    size_mb = os.path.getsize(file_path) / (1024 * 1024)
    context.user_data["video_path"] = file_path
    await status.edit_text(f"✅ Video didownload ({size_mb:.1f}MB)!\n\n📝 Masukkan caption (termasuk hashtag):")
    return WAIT_CAPTION


async def receive_caption(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["caption"] = update.message.text.strip()

    if context.user_data.get("mode") == "schedule":
        await update.message.reply_text(
            "⏰ *Masukkan waktu posting (WIB):*\n\n"
            "Format: `YYYY-MM-DD HH:MM`\n\n"
            "Contoh:\n"
            "• Hari ini jam 8 malam: `2026-04-13 20:00`\n"
            "• Besok jam 3 sore: `2026-04-14 15:00`\n"
            "• Minggu depan: `2026-04-20 12:00`\n\n"
            "Waktu menggunakan WIB (Jakarta)",
            parse_mode="Markdown",
        )
        return WAIT_SCHEDULE_TIME
    else:
        return await _execute_now(update, context)


async def receive_schedule_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    dt = parse_wib_datetime(update.message.text.strip())
    if not dt:
        await update.message.reply_text("❌ Format salah. Gunakan: `2026-04-15 20:00`", parse_mode="Markdown")
        return WAIT_SCHEDULE_TIME

    now = datetime.now(WIB)
    if dt <= now:
        await update.message.reply_text("❌ Waktu sudah lewat. Masukkan waktu yang akan datang.")
        return WAIT_SCHEDULE_TIME

    video_path = context.user_data["video_path"]
    caption = context.user_data["caption"]
    chat_id = update.effective_chat.id

    post_id = add_post(chat_id, video_path, caption, dt)
    time_str = dt.strftime("%d %b %Y %H:%M WIB")
    await update.message.reply_text(
        f"✅ *Jadwal tersimpan!*\n\n"
        f"📅 Waktu: *{time_str}*\n"
        f"📝 Caption: {caption[:50]}{'...' if len(caption) > 50 else ''}\n"
        f"🆔 ID: #{post_id}\n\n"
        f"Bot akan otomatis upload ke TikTok pada waktu tersebut.\n"
        f"Cek jadwal: /pending\n"
        f"Batalkan: /cancelschedule {post_id}",
        parse_mode="Markdown",
    )
    return ConversationHandler.END


async def _execute_now(update: Update, context: ContextTypes.DEFAULT_TYPE):
    video_path = context.user_data.get("video_path")
    caption = context.user_data.get("caption", "")
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


# ── /pending ─────────────────────────────────────────────────────────────

async def pending_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        return
    posts = get_pending()
    if not posts:
        await update.message.reply_text("📭 Tidak ada jadwal pending.")
        return
    lines = ["📅 Jadwal Pending:\n"]
    for p in posts:
        dt = datetime.fromisoformat(p["scheduled_time"])
        dt_str = dt.strftime("%d %b %Y %H:%M WIB")
        caption_short = p["caption"][:40] + "..." if len(p["caption"]) > 40 else p["caption"]
        lines.append(f"#{p['id']} — {dt_str}\n  {caption_short}")
    await update.message.reply_text("\n".join(lines))


# ── /cancelschedule ──────────────────────────────────────────────────────

async def cancelschedule_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        return
    args = context.args
    if not args:
        await update.message.reply_text("Usage: /cancelschedule <ID>\nContoh: /cancelschedule 1")
        return
    try:
        post_id = int(args[0].replace("#", ""))
    except ValueError:
        await update.message.reply_text("❌ ID harus angka.")
        return
    if remove_post(post_id):
        await update.message.reply_text(f"✅ Jadwal #{post_id} dibatalkan.")
    else:
        await update.message.reply_text(f"❌ Jadwal #{post_id} tidak ditemukan.")


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("❌ Dibatalkan.")
    return ConversationHandler.END


# ── Scheduler background job ─────────────────────────────────────────────

def check_scheduled_posts():
    """Cek dan eksekusi jadwal yang sudah waktunya."""
    now = datetime.now(WIB)
    posts = get_pending()

    for post in posts:
        scheduled_time = datetime.fromisoformat(post["scheduled_time"])
        if scheduled_time <= now:
            logger.info(f"⏰ Executing scheduled post #{post['id']}")
            try:
                result = tt_upload(post["video_path"], post["caption"])
                if result["success"]:
                    mark_done(post["id"])
                    logger.info(f"✅ Scheduled post #{post['id']} uploaded!")
                    # Notify via Telegram
                    import asyncio
                    asyncio.run(_notify(post["chat_id"], f"✅ Scheduled post #{post['id']} berhasil di-upload ke TikTok!"))
                else:
                    mark_failed(post["id"], result.get("error", "unknown"))
                    asyncio.run(_notify(post["chat_id"], f"❌ Scheduled post #{post['id']} gagal:\n{result.get('error')}"))
            except Exception as e:
                mark_failed(post["id"], str(e))
                logger.error(f"❌ Scheduled post #{post['id']} error: {e}")


async def _notify(chat_id: int, text: str):
    bot = Bot(token=TOKEN)
    await bot.send_message(chat_id=chat_id, text=text)


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
        entry_points=[
            CommandHandler("post", post_start),
            CommandHandler("schedule", schedule_start),
        ],
        states={
            WAIT_VIDEO: [
                MessageHandler(filters.VIDEO | filters.Document.ALL, receive_video),
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_gdrive_link),
            ],
            WAIT_CAPTION: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_caption),
            ],
            WAIT_SCHEDULE_TIME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_schedule_time),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("info", info_command))
    app.add_handler(CommandHandler("pending", pending_command))
    app.add_handler(CommandHandler("cancelschedule", cancelschedule_command))
    app.add_handler(conv)

    # Start scheduler — cek setiap 30 detik
    scheduler = BackgroundScheduler(timezone="Asia/Jakarta")
    scheduler.add_job(check_scheduled_posts, "interval", seconds=30)
    scheduler.start()
    print("📅 Scheduler aktif (cek setiap 30 detik)")

    print("✅ Bot siap! Buka Telegram dan kirim /start")
    app.run_polling()

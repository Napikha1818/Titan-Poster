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
from poster.youtube import upload_video as yt_upload
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

WAIT_VIDEO, WAIT_PLATFORM, WAIT_CAPTION, WAIT_YT_TITLE, WAIT_SCHEDULE_TIME = range(5)

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
        "/post — kirim video, pilih platform, langsung upload\n\n"
        "*Jadwalkan Upload:*\n"
        "/schedule — kirim video, atur waktu posting\n"
        "/pending — lihat semua jadwal pending\n"
        "/cancelschedule 1 — batalkan jadwal\n\n"
        "*Lainnya:*\n"
        "/info — cek status VPS\n"
        "/updatecookies <id> — update TikTok session\n"
        "/cancel — batalkan proses\n\n"
        "📹 Support: video langsung atau Google Drive link\n"
        "🎯 Platform: TikTok, YouTube Shorts, atau keduanya",
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


# ── /post and /schedule entry ────────────────────────────────────────────

async def post_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        return ConversationHandler.END
    context.user_data.clear()
    context.user_data["mode"] = "now"
    await update.message.reply_text("📹 Kirim videonya sekarang.\nBisa kirim file langsung atau Google Drive link.")
    return WAIT_VIDEO


async def schedule_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        return ConversationHandler.END
    context.user_data.clear()
    context.user_data["mode"] = "schedule"
    await update.message.reply_text("📹 Kirim videonya sekarang.\nBisa kirim file langsung atau Google Drive link.")
    return WAIT_VIDEO


# ── Video receive ────────────────────────────────────────────────────────

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
        await status.edit_text(
            f"✅ Video diterima ({size_mb:.1f}MB)!\n\n"
            "Pilih platform:\n"
            "1 — TikTok\n"
            "2 — YouTube Shorts\n"
            "3 — Keduanya (TikTok + YouTube)"
        )
        return WAIT_PLATFORM
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
    await status.edit_text(
        f"✅ Video didownload ({size_mb:.1f}MB)!\n\n"
        "Pilih platform:\n"
        "1 — TikTok\n"
        "2 — YouTube Shorts\n"
        "3 — Keduanya (TikTok + YouTube)"
    )
    return WAIT_PLATFORM


# ── Platform selection ───────────────────────────────────────────────────

async def receive_platform(update: Update, context: ContextTypes.DEFAULT_TYPE):
    choice = update.message.text.strip()
    mapping = {"1": ["tiktok"], "2": ["youtube"], "3": ["tiktok", "youtube"]}
    platforms = mapping.get(choice)
    if not platforms:
        await update.message.reply_text("❌ Pilih 1, 2, atau 3.")
        return WAIT_PLATFORM

    context.user_data["platforms"] = platforms

    if "youtube" in platforms:
        await update.message.reply_text(
            "📝 Masukkan judul YouTube:\n"
            "Format: Judul | Deskripsi\n\n"
            "Contoh:\n"
            "TitanChess Bullet | Brilliant sacrifice #chess #shorts"
        )
        return WAIT_YT_TITLE
    else:
        await update.message.reply_text("📝 Masukkan caption TikTok (termasuk hashtag):")
        return WAIT_CAPTION


# ── YouTube title ────────────────────────────────────────────────────────

async def receive_yt_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if "|" in text:
        parts = text.split("|", 1)
        context.user_data["yt_title"] = parts[0].strip()
        context.user_data["yt_desc"] = parts[1].strip()
    else:
        context.user_data["yt_title"] = text
        context.user_data["yt_desc"] = "#chess #shorts #titanchess"

    if "tiktok" in context.user_data.get("platforms", []):
        await update.message.reply_text("📝 Masukkan caption TikTok (termasuk hashtag):")
        return WAIT_CAPTION
    else:
        context.user_data["caption"] = ""
        return await _ask_schedule_or_execute(update, context)


# ── Caption ──────────────────────────────────────────────────────────────

async def receive_caption(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["caption"] = update.message.text.strip()
    return await _ask_schedule_or_execute(update, context)


async def _ask_schedule_or_execute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get("mode") == "schedule":
        await update.message.reply_text(
            "⏰ *Masukkan waktu posting (WIB):*\n\n"
            "Format: `YYYY-MM-DD HH:MM`\n\n"
            "Contoh:\n"
            "• Hari ini jam 8 malam: `2026-04-13 20:00`\n"
            "• Besok jam 3 sore: `2026-04-14 15:00`\n\n"
            "Waktu menggunakan WIB (Jakarta)",
            parse_mode="Markdown",
        )
        return WAIT_SCHEDULE_TIME
    else:
        return await _execute_now(update, context)


# ── Execute upload ───────────────────────────────────────────────────────

async def _execute_now(update: Update, context: ContextTypes.DEFAULT_TYPE):
    video_path = context.user_data.get("video_path")
    if not video_path:
        await update.message.reply_text("❌ Video tidak ditemukan. Mulai ulang dengan /post")
        return ConversationHandler.END

    platforms = context.user_data.get("platforms", ["tiktok"])
    status = await update.message.reply_text("⏳ Uploading...")
    results = []

    for plat in platforms:
        if plat == "tiktok":
            caption = context.user_data.get("caption", "")
            r = await asyncio.to_thread(tt_upload, video_path, caption)
            if r["success"]:
                results.append("✅ TikTok: uploaded!")
            else:
                results.append(f"❌ TikTok: {r['error']}")

        elif plat == "youtube":
            title = context.user_data.get("yt_title", "TitanChess Video")
            desc = context.user_data.get("yt_desc", "#chess #shorts")
            r = await asyncio.to_thread(yt_upload, video_path, title, desc)
            if r["success"]:
                results.append(f"✅ YouTube: {r['url']}")
            else:
                results.append(f"❌ YouTube: {r['error']}")

    await status.edit_text("\n".join(results))
    return ConversationHandler.END


# ── Schedule time ────────────────────────────────────────────────────────

async def receive_schedule_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    dt = parse_wib_datetime(update.message.text.strip())
    if not dt:
        await update.message.reply_text("❌ Format salah. Gunakan: `2026-04-15 20:00`", parse_mode="Markdown")
        return WAIT_SCHEDULE_TIME
    now = datetime.now(WIB)
    if dt <= now:
        await update.message.reply_text("❌ Waktu sudah lewat.")
        return WAIT_SCHEDULE_TIME

    video_path = context.user_data["video_path"]
    caption = context.user_data.get("caption", "")
    platforms = context.user_data.get("platforms", ["tiktok"])
    chat_id = update.effective_chat.id

    post_id = add_post(chat_id, video_path, caption, dt)
    # Store extra data in scheduler
    from scheduler import _load, _save
    posts = _load()
    for p in posts:
        if p["id"] == post_id:
            p["platforms"] = platforms
            p["yt_title"] = context.user_data.get("yt_title", "")
            p["yt_desc"] = context.user_data.get("yt_desc", "")
    _save(posts)

    time_str = dt.strftime("%d %b %Y %H:%M WIB")
    plat_str = " + ".join([p.upper() for p in platforms])
    await update.message.reply_text(
        f"✅ *Jadwal tersimpan!*\n\n"
        f"📅 Waktu: *{time_str}*\n"
        f"🎯 Platform: {plat_str}\n"
        f"🆔 ID: #{post_id}\n\n"
        f"Cek jadwal: /pending\n"
        f"Batalkan: /cancelschedule {post_id}",
        parse_mode="Markdown",
    )
    return ConversationHandler.END


# ── /pending, /cancelschedule, /cancel ───────────────────────────────────

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
        plats = " + ".join([x.upper() for x in p.get("platforms", ["tiktok"])])
        lines.append(f"#{p['id']} — {dt_str} [{plats}]")
    await update.message.reply_text("\n".join(lines))


async def cancelschedule_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        return
    args = context.args
    if not args:
        await update.message.reply_text("Usage: /cancelschedule <ID>")
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


async def updatecookies_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Update TikTok session ID via Telegram."""
    if not is_owner(update):
        return
    args = context.args
    if not args:
        await update.message.reply_text(
            "Usage: /updatecookies <session_id>\n\n"
            "Cara dapat session ID:\n"
            "1. Buka tiktok.com di Chrome, login\n"
            "2. F12 → Application → Cookies → tiktok.com\n"
            "3. Cari 'sessionid' → copy value\n"
            "4. Kirim: /updatecookies VALUE_DISINI"
        )
        return

    new_session_id = args[0].strip()
    cookies_file = os.getenv("TIKTOK_COOKIES_FILE", "tiktok_cookies.txt")

    try:
        # Read existing cookies
        if os.path.exists(cookies_file):
            with open(cookies_file, "r") as f:
                content = f.read()
            # Replace sessionid line
            import re as _re
            new_content = _re.sub(
                r'\.tiktok\.com\tTRUE\t/\tFALSE\t\d+\tsessionid\t\S+',
                f'.tiktok.com\tTRUE\t/\tFALSE\t2147483647\tsessionid\t{new_session_id}',
                content
            )
            if new_session_id not in new_content:
                # sessionid line not found, append it
                new_content += f'\n.tiktok.com\tTRUE\t/\tFALSE\t2147483647\tsessionid\t{new_session_id}\n'
        else:
            new_content = f'# Netscape HTTP Cookie File\n.tiktok.com\tTRUE\t/\tFALSE\t2147483647\tsessionid\t{new_session_id}\n'

        with open(cookies_file, "w") as f:
            f.write(new_content)

        await update.message.reply_text("✅ TikTok cookies berhasil diupdate! Coba /post sekarang.")
        logger.info(f"TikTok cookies updated via Telegram")
    except Exception as e:
        await update.message.reply_text(f"❌ Gagal update cookies: {e}")
    context.user_data.clear()
    await update.message.reply_text("❌ Dibatalkan.")
    return ConversationHandler.END


# ── Scheduler background job ─────────────────────────────────────────────

def check_scheduled_posts():
    now = datetime.now(WIB)
    posts = get_pending()
    for post in posts:
        scheduled_time = datetime.fromisoformat(post["scheduled_time"])
        if scheduled_time <= now:
            logger.info(f"⏰ Executing scheduled post #{post['id']}")
            platforms = post.get("platforms", ["tiktok"])
            results = []
            try:
                for plat in platforms:
                    if plat == "tiktok":
                        import subprocess, json as _json
                        proc = subprocess.run(
                            ["/opt/titanchess-poster/venv/bin/python",
                             os.path.join(os.path.dirname(__file__), "upload_tiktok.py"),
                             post["video_path"], post["caption"]],
                            capture_output=True, text=True, timeout=300,
                            cwd=os.path.dirname(__file__)
                        )
                        if proc.returncode == 0 and proc.stdout.strip():
                            try:
                                r = _json.loads(proc.stdout.strip().split('\n')[-1])
                            except _json.JSONDecodeError:
                                r = {"success": False, "error": f"Bad output: {proc.stdout[:100]}"}
                        else:
                            r = {"success": False, "error": proc.stderr[:200] if proc.stderr else "subprocess failed"}
                        results.append(f"TikTok: {'✅' if r['success'] else '❌ ' + r.get('error', '')}")
                    elif plat == "youtube":
                        r = yt_upload(post["video_path"], post.get("yt_title", "Video"), post.get("yt_desc", ""))
                        results.append(f"YouTube: {'✅ ' + r.get('url', '') if r['success'] else '❌ ' + r.get('error', '')}")

                mark_done(post["id"], "\n".join(results))
                _notify_sync(post["chat_id"], f"📤 Scheduled post #{post['id']}:\n" + "\n".join(results))
            except Exception as e:
                mark_failed(post["id"], str(e))
                logger.error(f"❌ Scheduled post #{post['id']} error: {e}")


def _notify_sync(chat_id: int, text: str):
    """Send Telegram notification from background thread."""
    import requests as req
    try:
        req.post(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            json={"chat_id": chat_id, "text": text},
            timeout=10
        )
    except Exception as e:
        logger.error(f"Failed to send notification: {e}")


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
            WAIT_PLATFORM: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_platform),
            ],
            WAIT_YT_TITLE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_yt_title),
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
    app.add_handler(CommandHandler("updatecookies", updatecookies_command))
    app.add_handler(conv)

    scheduler = BackgroundScheduler(timezone="Asia/Jakarta")
    scheduler.add_job(check_scheduled_posts, "interval", seconds=30)
    scheduler.start()
    print("📅 Scheduler aktif (cek setiap 30 detik)")

    print("✅ Bot siap! Buka Telegram dan kirim /start")
    app.run_polling()

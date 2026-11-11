# -*- coding: utf-8 -*-
"""
Astedafa Script Hosting Bot — Termux
Supported runtimes: Python (.py), Node.js (.js), PHP (.php), Bash (.sh)
Security note: approved scripts run with the same permissions as this bot in Termux.
Only approve code you personally trust. This is not a sandbox.
"""

import json
import logging
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path
from threading import RLock

import telebot
from telebot import types

# 1) قسم المعلومات ده بس
TOKEN = "8963542235:AAGqrG4fwvDmPNfoEGV7k2GQaShGIP4mzeo"
ADMIN_ID = "8781218517"
ADMIN_GROUP_ID = "-1004446119432 "    
TRUST_CHANNEL_ID = "-1003982353680"    
DEVELOPER_USERNAME = "@r_r_i_1e"
# المعلومات لحد هنا 
MAX_FILE_MB = 10
MAX_RUNNING_PER_USER = 3
MAX_RUNNING_TOTAL = 8
ALLOWED_EXTENSIONS = {".py", ".js", ".php", ".sh"}
LOG_TAIL_LINES = 35

BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "hosted_files"
LOG_DIR = BASE_DIR / "hosting_logs"
DATA_FILE = BASE_DIR / "jobs.json"
VERIFIED_FILE = BASE_DIR / "verified_users.json"
BOT_LOG_FILE = BASE_DIR / "astedafa.log"

for directory in (UPLOAD_DIR, LOG_DIR):
    directory.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    filename=str(BOT_LOG_FILE),
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
_console = logging.StreamHandler(sys.stdout)
_console.setLevel(logging.INFO)
logging.getLogger().addHandler(_console)

if TOKEN == "PUT_YOUR_NEW_BOT_TOKEN_HERE" or not TOKEN.strip():
    raise SystemExit("افتح astedafa.py وضع التوكن الجديد مكان PUT_YOUR_NEW_BOT_TOKEN_HERE")

bot = telebot.TeleBot(TOKEN, threaded=True, num_threads=4)

lock = RLock()
jobs = {}        # job_id -> metadata (persisted in jobs.json)
processes = {}   # job_id -> subprocess.Popen; live processes only
user_modes = {}  # user_id -> 'hosting' | 'report'
verified_users = set()


def load_json(path, default):
    try:
        with path.open("r", encoding="utf-8") as f:
            value = json.load(f)
        return value
    except FileNotFoundError:
        return default
    except Exception:
        logging.exception("Could not read %s", path.name)
        return default


def save_json(path, value):
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w", encoding="utf-8") as f:
        json.dump(value, f, ensure_ascii=False, indent=2)
    temp.replace(path)


def load_state():
    global jobs, verified_users
    stored_jobs = load_json(DATA_FILE, {})
    if isinstance(stored_jobs, dict):
        jobs = stored_jobs
    stored_verified = load_json(VERIFIED_FILE, [])
    if isinstance(stored_verified, list):
        verified_users = {int(x) for x in stored_verified if str(x).isdigit()}
    # A process cannot be safely reattached after the bot restarts. Mark any
    # previously-running job as interrupted instead of pretending it is alive.
    changed = False
    for meta in jobs.values():
        if meta.get("status") == "running":
            meta["status"] = "interrupted"
            meta.pop("pid", None)
            changed = True
    if changed:
        save_json(DATA_FILE, jobs)


def persist_jobs():
    with lock:
        save_json(DATA_FILE, jobs)


def persist_verified():
    save_json(VERIFIED_FILE, sorted(verified_users))


def is_admin(user_id):
    return int(user_id) == ADMIN_ID


def display_name(user):
    return (user.first_name or f"User {user.id}")[:80]


def safe_filename(name):
    name = Path(name or "script").name
    name = re.sub(r"[^A-Za-z0-9_.-]", "_", name)
    return name[:80] or "script"


def is_verified(user_id):
    return is_admin(user_id) or int(user_id) in verified_users


def main_menu(chat_id, user):
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("📤 رفع ملف", callback_data="upload"),
        types.InlineKeyboardButton("🗂️ ملفاتي", callback_data="myjobs"),
    )
    kb.add(
        types.InlineKeyboardButton("⚡ العمليات النشطة", callback_data="running"),
        types.InlineKeyboardButton("🧪 فحص الأخطاء", callback_data="myjobs"),
    )
    kb.add(
        types.InlineKeyboardButton("🧩 الأنواع المدعومة", callback_data="languages"),
        types.InlineKeyboardButton("🆘 الدعم الفني", callback_data="report"),
    )
    kb.add(types.InlineKeyboardButton("👨‍💻 المطور", callback_data="developer"))
    bot.send_message(
        chat_id,
        f"╔════════════════════╗\n"
        f"   🚀 *ASTEDAFA PRO PLUS*\n"
        f"╚════════════════════╝\n\n"
        f"أهلًا *{display_name(user)}* 👋\n"
        "منصة إدارة ملفاتك البرمجية من مكان واحد.\n\n"
        "📤 ارفع ملفك  •  ▶️ شغّل أو أعد التشغيل\n"
        "⏹️ أوقف العملية  •  🧪 افحص الصياغة\n"
        "🗑️ احذف الملفات غير النشطة\n\n"
        "⚠️ شغّل فقط الملفات التي تثق بمصدرها؛ تعمل بصلاحيات Termux نفسها.",
        parse_mode="Markdown",
        reply_markup=kb,
    )


def ask_contact(chat_id):
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    kb.add(types.KeyboardButton("📱 مشاركة رقم هاتفي عبر تيليجرام", request_contact=True))
    bot.send_message(
        chat_id,
        "🔐 *التحقق مطلوب قبل استخدام البوت*\n\n"
        "اضغط الزر لمشاركة رقم هاتف حسابك عبر تيليجرام.\n"
        "⚠️ عند التحقق، سيصل رقم الهاتف إلى إدارة البوت مع اسمك ومعرّف حسابك.\n"
        "يُقبل فقط رقم جهة الاتصال المرتبط بحسابك نفسه.",
        reply_markup=kb,
    )


def user_running_count(user_id):
    count = 0
    for jid, proc in list(processes.items()):
        meta = jobs.get(jid, {})
        if meta.get("owner_id") == int(user_id) and proc.poll() is None:
            count += 1
    return count


def running_count():
    return sum(1 for p in processes.values() if p.poll() is None)


def build_command(ext, path):
    if ext == ".py":
        return ["python", "-u", str(path)]
    if ext == ".js":
        return ["node", str(path)]
    if ext == ".php":
        return ["php", str(path)]
    if ext == ".sh":
        return ["bash", str(path)]
    raise ValueError("امتداد الملف غير مدعوم.")


def start_job(job_id, actor_id):
    if not is_admin(actor_id):
        raise PermissionError("التشغيل للإدارة فقط.")
    meta = jobs.get(job_id)
    if not meta:
        raise FileNotFoundError("الطلب غير موجود.")
    if meta.get("status") == "rejected":
        raise RuntimeError("هذا الطلب مرفوض؛ ارفع الملف من جديد إذا لزم.")
    current = processes.get(job_id)
    if current and current.poll() is None:
        raise RuntimeError("السكربت يعمل بالفعل.")
    if running_count() >= MAX_RUNNING_TOTAL:
        raise RuntimeError(f"تم بلوغ الحد الإجمالي ({MAX_RUNNING_TOTAL}) للعمليات.")
    if user_running_count(meta["owner_id"]) >= MAX_RUNNING_PER_USER:
        raise RuntimeError(f"حد المستخدم هو {MAX_RUNNING_PER_USER} عمليات نشطة.")

    filename = safe_filename(meta.get("filename", "script"))
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise ValueError("نوع الملف غير مسموح.")
    job_dir = UPLOAD_DIR / job_id
    script_path = job_dir / filename
    if not script_path.is_file():
        raise FileNotFoundError("ملف السكربت غير موجود على الجهاز.")

    log_path = LOG_DIR / f"{job_id}.log"
    with log_path.open("ab") as log_handle:
        proc = subprocess.Popen(
            build_command(ext, script_path),
            cwd=str(job_dir),
            stdin=subprocess.DEVNULL,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
        )
    processes[job_id] = proc
    meta["status"] = "running"
    meta["pid"] = proc.pid
    meta["started_at"] = time.time()
    meta.pop("last_exit_code", None)
    persist_jobs()
    logging.info("Started job=%s owner=%s pid=%s", job_id, meta["owner_id"], proc.pid)
    return proc


def refresh_job_status(job_id):
    proc = processes.get(job_id)
    meta = jobs.get(job_id)
    if proc and meta:
        code = proc.poll()
        if code is not None:
            meta["status"] = "finished" if code == 0 else "error"
            meta["last_exit_code"] = code
            meta.pop("pid", None)
            processes.pop(job_id, None)
            persist_jobs()
    return jobs.get(job_id, {})


def stop_job(job_id):
    proc = processes.get(job_id)
    if not proc or proc.poll() is not None:
        processes.pop(job_id, None)
        if job_id in jobs and jobs[job_id].get("status") == "running":
            jobs[job_id]["status"] = "stopped"
            jobs[job_id].pop("pid", None)
            persist_jobs()
        return False
    try:
        os.killpg(proc.pid, signal.SIGTERM)
        try:
            proc.wait(timeout=4)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
    except Exception:
        logging.exception("Could not stop job %s", job_id)
        try:
            proc.terminate()
        except Exception:
            pass
    processes.pop(job_id, None)
    if job_id in jobs:
        jobs[job_id]["status"] = "stopped"
        jobs[job_id].pop("pid", None)
        persist_jobs()
    logging.info("Stopped job=%s", job_id)
    return True


def status_text(status):
    return {
        "pending": "⏳ قيد المراجعة",
        "running": "🟢 يعمل",
        "finished": "✅ انتهى بنجاح",
        "error": "⚠️ انتهى بخطأ",
        "stopped": "🛑 متوقف",
        "rejected": "❌ مرفوض",
        "interrupted": "🔄 محفوظ — يحتاج تشغيلًا من جديد",
    }.get(status, status or "غير معروف")


def send_running(chat_id, requester_id):
    lines = ["⚡ *العمليات النشطة*"]
    kb = types.InlineKeyboardMarkup(row_width=1)
    found = 0
    for jid in list(jobs.keys()):
        meta = refresh_job_status(jid)
        proc = processes.get(jid)
        if not proc or proc.poll() is not None:
            continue
        if requester_id != ADMIN_ID and meta.get("owner_id") != int(requester_id):
            continue
        found += 1
        lines.append(
            f"\n• `{jid}` — `{meta.get('filename', 'script')}`\n"
            f"المالك: {meta.get('owner_name', 'غير معروف')}"
        )
        if is_admin(requester_id):
            kb.add(types.InlineKeyboardButton(
                f"🛑 إيقاف {meta.get('filename', 'script')[:18]} [{jid}]",
                callback_data=f"stop:{jid}",
            ))
    if not found:
        lines.append("\nلا توجد عمليات نشطة ظاهرة لك.")
    kb.add(types.InlineKeyboardButton("⬅️ القائمة الرئيسية", callback_data="menu"))
    bot.send_message(chat_id, "\n".join(lines), parse_mode="Markdown", reply_markup=kb)


def send_my_jobs(chat_id, user_id):
    lines = ["🗂️ *لوحة ملفاتك وعملياتك*"]
    items = [
        (jid, meta) for jid, meta in jobs.items()
        if is_admin(user_id) or meta.get("owner_id") == int(user_id)
    ]
    items.sort(key=lambda pair: pair[1].get("uploaded_at", 0), reverse=True)
    if not items:
        lines.append("\nلا توجد ملفات محفوظة حتى الآن. اضغط «رفع ملف» لإضافة ملف.")
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("⬅️ القائمة الرئيسية", callback_data="menu"))
        bot.send_message(chat_id, "\n".join(lines), parse_mode="Markdown", reply_markup=kb)
        return

    # Send each job as its own compact card so every file has its own controls.
    for jid, old_meta in items[:15]:
        refresh_job_status(jid)
        meta = jobs[jid]
        card = (
            f"📄 *{meta.get('filename', 'script')}*\n"
            f"🆔 الطلب: `{jid}`\n"
            f"📌 الحالة: {status_text(meta.get('status'))}"
        )
        if is_admin(user_id):
            card += f"\n👤 المالك: {meta.get('owner_name', 'غير معروف')}"
        kb = job_control_keyboard(jid, meta, user_id)
        kb.add(types.InlineKeyboardButton("📃 عرض سجل التشغيل", callback_data=f"log:{jid}"))
        bot.send_message(chat_id, card, parse_mode="Markdown", reply_markup=kb)

    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("⬅️ القائمة الرئيسية", callback_data="menu"))
    bot.send_message(chat_id, "━━━━━━━━━━━━━━━━━━\nاختر الإجراء المطلوب للملف.", reply_markup=kb)


def send_log(chat_id, job_id):
    path = LOG_DIR / f"{job_id}.log"
    if not path.exists():
        bot.send_message(chat_id, "لا يوجد سجل لهذا الطلب حتى الآن.")
        return
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        tail = "\n".join(text.splitlines()[-LOG_TAIL_LINES:])[-3500:]
        if not tail:
            tail = "(السجل فارغ)"
        safe_tail = tail.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        bot.send_message(
            chat_id,
            f"🧾 <b>سجل الملف</b>\nرقم الطلب: <code>{job_id}</code>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            f"<pre>{safe_tail[:3300]}</pre>\n"
            "إذا ظهر Error أو Traceback، فآخر السجل غالبًا يوضح سبب المشكلة.",
            parse_mode="HTML",
        )
    except Exception:
        logging.exception("Could not read job log")
        bot.send_message(chat_id, "تعذر قراءة السجل.")


def check_job_syntax(job_id):
    """Static syntax check only; never executes the uploaded script."""
    meta = jobs.get(job_id)
    if not meta:
        return False, "❌ الطلب غير موجود."
    filename = safe_filename(meta.get("filename", "script"))
    ext = Path(filename).suffix.lower()
    script_path = UPLOAD_DIR / job_id / filename
    if not script_path.is_file():
        return False, "❌ الملف الأصلي غير موجود على الجهاز."
    try:
        if ext == ".py":
            result = subprocess.run(
                [sys.executable, "-m", "py_compile", str(script_path)],
                cwd=str(script_path.parent), capture_output=True, text=True, timeout=12
            )
            # py_compile creates __pycache__; remove it to keep the upload directory tidy.
            shutil.rmtree(script_path.parent / "__pycache__", ignore_errors=True)
        elif ext == ".js":
            if not shutil.which("node"):
                return False, "⚠️ لا يمكن فحص JavaScript لأن Node.js غير مثبت في Termux."
            result = subprocess.run(
                ["node", "--check", str(script_path)],
                cwd=str(script_path.parent), capture_output=True, text=True, timeout=12
            )
        elif ext == ".php":
            if not shutil.which("php"):
                return False, "⚠️ لا يمكن فحص PHP لأن PHP غير مثبت في Termux."
            result = subprocess.run(
                ["php", "-l", str(script_path)],
                cwd=str(script_path.parent), capture_output=True, text=True, timeout=12
            )
        elif ext == ".sh":
            result = subprocess.run(
                ["bash", "-n", str(script_path)],
                cwd=str(script_path.parent), capture_output=True, text=True, timeout=12
            )
        else:
            return False, "❌ امتداد الملف غير مدعوم للفحص."
        if result.returncode == 0:
            return True, (
                "✅ *الفحص اكتمل: لا توجد أخطاء نحوية ظاهرة.*\n"
                "ملاحظة: هذا فحص للصياغة فقط؛ لا يضمن أن السكربت سيعمل دون أخطاء أثناء التشغيل."
            )
        details = (result.stderr or result.stdout or "لم يوضح المفسّر سبب الخطأ.").strip()
        details = details[-2500:]
        safe = details.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        return False, f"❌ *تم العثور على خطأ في صياغة الملف:*\n<pre>{safe}</pre>"
    except subprocess.TimeoutExpired:
        return False, "⏱️ انتهى وقت الفحص؛ الملف كبير أو الفحص استغرق وقتًا طويلًا."
    except Exception as exc:
        logging.exception("Syntax check failed for %s", job_id)
        return False, f"⚠️ تعذر إكمال الفحص: {str(exc)[:500]}"


def job_control_keyboard(job_id, meta, requester_id):
    kb = types.InlineKeyboardMarkup(row_width=2)
    is_running = meta.get("status") == "running" and (
        job_id in processes and processes[job_id].poll() is None
    )
    if is_admin(requester_id):
        if is_running:
            kb.add(types.InlineKeyboardButton("⏹️ إيقاف", callback_data=f"stop:{job_id}"))
        else:
            # Allows restarting a saved script after a bot/network interruption without uploading it again.
            kb.add(types.InlineKeyboardButton("▶️ تشغيل / إعادة تشغيل", callback_data=f"start:{job_id}"))
        kb.add(
            types.InlineKeyboardButton("🧪 فحص الأخطاء", callback_data=f"check:{job_id}"),
            types.InlineKeyboardButton("🗑️ حذف", callback_data=f"delete:{job_id}"),
        )
    else:
        kb.add(types.InlineKeyboardButton("🧪 فحص الأخطاء", callback_data=f"check:{job_id}"))
    return kb


def admin_job_keyboard(job_id):
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("✅ موافقة وتشغيل", callback_data=f"approve:{job_id}"),
        types.InlineKeyboardButton("❌ رفض", callback_data=f"reject:{job_id}"),
    )
    kb.add(types.InlineKeyboardButton("📄 عرض السجل", callback_data=f"log:{job_id}"))
    return kb


load_state()


@bot.message_handler(commands=["start", "menu"])
def start(message):
    uid = message.from_user.id
    if not is_verified(uid):
        ask_contact(message.chat.id)
        return
    main_menu(message.chat.id, message.from_user)


@bot.message_handler(content_types=["contact"])
def contact_verification(message):
    contact = message.contact
    uid = message.from_user.id
    if not contact or contact.user_id != uid:
        bot.reply_to(message, "❌ شارك جهة اتصال حسابك أنت باستخدام زر تيليجرام.")
        return
    verified_users.add(uid)
    persist_verified()
    bot.send_message(
        message.chat.id, "✅ *تم التحقق بنجاح!*\nجاري فتح لوحة التحكم…",
        parse_mode="Markdown", reply_markup=types.ReplyKeyboardRemove()
    )
    # The user was told before sharing that the number is sent to the admin.
    phone = contact.phone_number or "غير متاح"
    try:
        bot.send_message(
            ADMIN_ID,
            "📱 *مستخدم جديد أكمل التحقق*\n━━━━━━━━━━━━━━━━━━\n"
            f"الاسم: {display_name(message.from_user)}\n"
            f"اسم المستخدم: @{message.from_user.username if message.from_user.username else 'بدون username'}\n"
            f"معرّف تيليجرام: `{uid}`\nرقم الهاتف: `{phone}`",
            parse_mode="Markdown"
        )
    except Exception:
        logging.exception("Could not send verified user's phone to admin")
        bot.send_message(message.chat.id, "⚠️ تم التحقق، لكن تعذر إرسال إشعار الرقم للإدارة. تأكد أن الأدمن بدأ محادثة مع البوت.")
    main_menu(message.chat.id, message.from_user)


@bot.message_handler(commands=["help"])
def help_command(message):
    bot.reply_to(
        message,
        "الأوامر:\n/start — القائمة الرئيسية\n/running — العمليات النشطة\n"
        "/report وصف المشكلة — إرسال بلاغ\n\n"
        "الملفات: .py .js .php .sh\n"
        "يجب تثبيت المفسر المناسب في Termux. لا توجد استضافة مواقع أو تثبيت تلقائي للمكتبات.",
    )


@bot.message_handler(commands=["running"])
def running_command(message):
    if not is_verified(message.from_user.id):
        ask_contact(message.chat.id)
        return
    send_running(message.chat.id, message.from_user.id)


@bot.message_handler(commands=["report"])
def report_command(message):
    if not is_verified(message.from_user.id):
        ask_contact(message.chat.id)
        return
    description = message.text.partition(" ")[2].strip()
    if not description:
        bot.reply_to(message, "اكتب البلاغ هكذا:\n/report وصف المشكلة")
        return
    try:
        bot.send_message(
            ADMIN_ID,
            f"🆘 بلاغ مشكلة\nالمستخدم: {display_name(message.from_user)}\n"
            f"ID: {message.from_user.id}\n\n{description[:2500]}",
        )
        bot.reply_to(message, "تم إرسال البلاغ للإدارة.")
    except Exception:
        logging.exception("Could not deliver report")
        bot.reply_to(message, "تعذر إرسال البلاغ حاليًا.")


@bot.callback_query_handler(func=lambda c: c.data in {
    "upload", "running", "myjobs", "languages", "developer", "report", "menu"
})
def menu_callbacks(call):
    uid = call.from_user.id
    try:
        bot.answer_callback_query(call.id)
    except Exception:
        pass
    if not is_verified(uid):
        ask_contact(call.message.chat.id)
        return
    if call.data == "menu":
        main_menu(call.message.chat.id, call.from_user)
    elif call.data == "upload":
        user_modes[uid] = "hosting"
        bot.send_message(
            call.message.chat.id,
            f"📦 أرسل ملفًا واحدًا بصيغة .py أو .js أو .php أو .sh.\nالحد الأقصى {MAX_FILE_MB} MB.\n"
            "ستراجعه الإدارة قبل التشغيل. لا ترفع ملفات تحتوي على كلمات مرور أو مفاتيح سرية.",
        )
    elif call.data == "running":
        send_running(call.message.chat.id, uid)
    elif call.data == "myjobs":
        send_my_jobs(call.message.chat.id, uid)
    elif call.data == "languages":
        bot.send_message(
            call.message.chat.id,
            "📖 *الأنواع المدعومة*\n🐍 Python (.py)\n🟨 Node.js (.js)\n🐘 PHP (.php)\n💻 Bash (.sh)\n\n"
            "يلزم تثبيت المفسر المناسب داخل Termux. لا توجد استضافة مواقع أو تثبيت تلقائي للمكتبات.",
            parse_mode="Markdown",
        )
    elif call.data == "developer":
        bot.send_message(call.message.chat.id, f"👨‍💻 المطور: @{DEVELOPER_USERNAME}")
    elif call.data == "report":
        bot.send_message(call.message.chat.id, "أرسل البلاغ بالأمر:\n/report ثم وصف المشكلة.")


@bot.message_handler(content_types=["document"])
def receive_document(message):
    uid = message.from_user.id
    if not is_verified(uid):
        ask_contact(message.chat.id)
        return
    doc = message.document
    filename = safe_filename(doc.file_name or "script")
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        bot.reply_to(message, "❌ النوع غير مدعوم. المسموح: .py .js .php .sh")
        return
    if not doc.file_size or doc.file_size > MAX_FILE_MB * 1024 * 1024:
        bot.reply_to(message, f"❌ الحد الأقصى لحجم الملف {MAX_FILE_MB} MB.")
        return
    try:
        info = bot.get_file(doc.file_id)
        content = bot.download_file(info.file_path)
    except Exception:
        logging.exception("Telegram download failed")
        bot.reply_to(message, "تعذر تنزيل الملف من تيليجرام.")
        return

    job_id = uuid.uuid4().hex[:8]
    job_dir = UPLOAD_DIR / job_id
    try:
        job_dir.mkdir(parents=True, exist_ok=False)
        (job_dir / filename).write_bytes(content)
    except Exception:
        logging.exception("Could not save uploaded script")
        shutil.rmtree(job_dir, ignore_errors=True)
        bot.reply_to(message, "تعذر حفظ الملف.")
        return

    jobs[job_id] = {
        "job_id": job_id,
        "filename": filename,
        "owner_id": uid,
        "owner_name": display_name(message.from_user),
        "owner_username": f"@{message.from_user.username}" if message.from_user.username else "بدون username",
        "chat_id": message.chat.id,
        "status": "pending",
        "uploaded_at": time.time(),
    }
    persist_jobs()

    # Immediately deliver the actual uploaded file to the admin requests group.
    # The admin can review it and approve/reject directly from that message.
    try:
        caption = (
            "📥 ASTEDAFA PRO — ملف جديد للمراجعة\n"
            "━━━━━━━━━━━━━━━━━━\n"
            f"📄 الملف: {filename}\n🆔 الطلب: {job_id}\n"
            f"👤 المستخدم: {display_name(message.from_user)}\n"
            f"🔗 الحساب: {('@' + message.from_user.username) if message.from_user.username else 'بدون username'}\n"
            f"🆔 User ID: {uid}\n\n"
            "⚠️ راجع الكود قبل التشغيل؛ الملفات تعمل بصلاحيات Termux نفسها."
        )
        with (job_dir / filename).open("rb") as upload_handle:
            bot.send_document(
                ADMIN_GROUP_ID,
                upload_handle,
                caption=caption,
                reply_markup=admin_job_keyboard(job_id),
            )
        jobs[job_id]["status"] = "submitted"
        persist_jobs()
        bot.reply_to(
            message,
            f"✨ تم استلام الملف وإرساله للإدارة تلقائيًا.\n📄 {filename}\n🆔 رقم الطلب: {job_id}\n\n"
            "يمكنك متابعة الحالة من زر «طلباتي».",
        )
    except Exception:
        logging.exception("Could not deliver uploaded file to admin group")
        bot.reply_to(
            message,
            f"⚠️ تم حفظ الملف لكن تعذر إرساله لمجموعة الطلبات.\nرقم الطلب: {job_id}\n"
            "تأكد أن البوت موجود في المجموعة ويستطيع إرسال الملفات والرسائل.",
        )


@bot.callback_query_handler(func=lambda c: c.data.startswith((
    "request:", "approve:", "reject:", "start:", "stop:", "delete:", "log:", "check:"
)))
def job_callbacks(call):
    uid = call.from_user.id
    action, job_id = call.data.split(":", 1)
    meta = jobs.get(job_id)
    if not meta:
        bot.answer_callback_query(call.id, "الطلب غير موجود.", show_alert=True)
        return

    if action == "request":
        if not is_verified(uid) or meta.get("owner_id") != uid:
            bot.answer_callback_query(call.id, "هذا الطلب ليس لك أو لم يتم التحقق.", show_alert=True)
            return
        if meta.get("status") != "pending":
            bot.answer_callback_query(call.id, "الملف وصل للإدارة تلقائيًا أو تغيّرت حالته بالفعل.", show_alert=True)
            return
        try:
            bot.send_message(
                ADMIN_GROUP_ID,
                "📥 طلب تشغيل سكربت\n"
                f"الملف: {meta['filename']}\nرقم الطلب: {job_id}\n"
                f"المستخدم: {meta['owner_name']}\n"
                f"Username: {meta['owner_username']}\nUser ID: {meta['owner_id']}\n\n"
                "راجع الكود قبل التشغيل؛ السكربت سيعمل بصلاحيات Termux نفسها.",
                reply_markup=admin_job_keyboard(job_id),
            )
            meta["status"] = "submitted"
            persist_jobs()
            bot.answer_callback_query(call.id, "تم إرسال الطلب للإدارة.")
            bot.send_message(meta["chat_id"], "⏳ تم إرسال طلبك للمراجعة.")
        except Exception:
            logging.exception("Could not send request to admin group")
            bot.answer_callback_query(call.id, "تعذر إرسال الطلب. تأكد أن البوت يستطيع النشر في مجموعة الطلبات.", show_alert=True)
        return

    if action == "log":
        if not is_admin(uid) and meta.get("owner_id") != uid:
            bot.answer_callback_query(call.id, "لا يمكنك عرض سجل ملف شخص آخر.", show_alert=True)
            return
        bot.answer_callback_query(call.id)
        send_log(call.message.chat.id, job_id)
        return

    if not is_admin(uid):
        bot.answer_callback_query(call.id, "هذا الإجراء للإدارة فقط.", show_alert=True)
        return

    if action == "approve":
        try:
            proc = start_job(job_id, uid)
            bot.answer_callback_query(call.id, "بدأ التشغيل.")
            try:
                bot.edit_message_text(
                    f"🟢 بدأ التشغيل\nالملف: {meta['filename']}\nالطلب: {job_id}\nPID: {proc.pid}",
                    call.message.chat.id, call.message.message_id,
                )
            except Exception:
                pass
            try:
                bot.send_message(meta["chat_id"], f"✅ وافقت الإدارة على {meta['filename']} وبدأ التشغيل. الطلب: {job_id}")
            except Exception:
                pass
            try:
                bot.send_message(TRUST_CHANNEL_ID, f"✅ تم اعتماد سكربت برقم {job_id} — النوع {Path(meta['filename']).suffix.lower()}")
            except Exception:
                logging.warning("Could not post to trust channel; check bot permissions.")
        except Exception as exc:
            logging.exception("Start failed for %s", job_id)
            bot.answer_callback_query(call.id, "فشل التشغيل؛ راجع السجل.", show_alert=True)
            bot.send_message(call.message.chat.id, f"❌ تعذر التشغيل: {str(exc)[:400]}")
        return

    if action == "check":
        if not is_admin(uid) and meta.get("owner_id") != uid:
            bot.answer_callback_query(call.id, "لا يمكنك فحص ملف شخص آخر.", show_alert=True)
            return
        bot.answer_callback_query(call.id, "جارٍ فحص صياغة الملف…")
        ok, report = check_job_syntax(job_id)
        try:
            bot.send_message(call.message.chat.id, report, parse_mode="HTML" if "<pre>" in report else "Markdown")
        except Exception:
            bot.send_message(call.message.chat.id, "تعذر عرض نتيجة الفحص.")
        return

    if action == "start":
        if not is_admin(uid):
            bot.answer_callback_query(call.id, "التشغيل للإدارة فقط.", show_alert=True)
            return
        try:
            proc = start_job(job_id, uid)
            bot.answer_callback_query(call.id, "تم تشغيل الملف.")
            bot.send_message(
                call.message.chat.id,
                f"🟢 *تم التشغيل بنجاح مبدئيًا*\n📄 {meta['filename']}\n🆔 `{job_id}`\n"
                "إذا توقف فورًا، افتح سجل التشغيل لمعرفة السبب.",
                parse_mode="Markdown",
            )
            try:
                bot.send_message(
                    meta["chat_id"],
                    f"▶️ تم تشغيل/إعادة تشغيل الملف {meta['filename']}.\nرقم الطلب: {job_id}",
                )
            except Exception:
                pass
        except Exception as exc:
            logging.exception("Restart/start failed for %s", job_id)
            bot.answer_callback_query(call.id, "تعذر التشغيل؛ افتح السجل.", show_alert=True)
            bot.send_message(call.message.chat.id, f"❌ تعذر التشغيل: {str(exc)[:500]}")
        return

    if action == "reject":
        meta["status"] = "rejected"
        persist_jobs()
        bot.answer_callback_query(call.id, "تم الرفض.")
        try:
            bot.send_message(meta["chat_id"], f"❌ تم رفض طلب تشغيل {meta['filename']}.")
            bot.edit_message_text(f"❌ تم رفض الطلب {job_id} — {meta['filename']}", call.message.chat.id, call.message.message_id)
        except Exception:
            pass
        return

    if action == "stop":
        if meta.get("status") != "running" and job_id not in processes:
            bot.answer_callback_query(call.id, "العملية ليست نشطة.", show_alert=True)
            return
        stopped = stop_job(job_id)
        bot.answer_callback_query(call.id, "تم الإيقاف." if stopped else "العملية متوقفة بالفعل.")
        try:
            bot.edit_message_text(f"🛑 تم إيقاف {meta['filename']} — الطلب {job_id}", call.message.chat.id, call.message.message_id)
        except Exception:
            pass
        return

    if action == "log":
        bot.answer_callback_query(call.id)
        send_log(call.message.chat.id, job_id)
        return

    if action == "delete":
        if meta.get("status") == "running" or (job_id in processes and processes[job_id].poll() is None):
            bot.answer_callback_query(call.id, "أوقف السكربت قبل الحذف.", show_alert=True)
            return
        try:
            shutil.rmtree(UPLOAD_DIR / job_id, ignore_errors=True)
            (LOG_DIR / f"{job_id}.log").unlink(missing_ok=True)
            jobs.pop(job_id, None)
            persist_jobs()
            bot.answer_callback_query(call.id, "تم الحذف.")
            try:
                bot.edit_message_text(f"🗑️ تم حذف الطلب {job_id}.", call.message.chat.id, call.message.message_id)
            except Exception:
                pass
        except Exception:
            logging.exception("Delete failed")
            bot.answer_callback_query(call.id, "تعذر الحذف.", show_alert=True)


@bot.message_handler(func=lambda m: m.from_user and not m.text and not m.document, content_types=["text", "photo", "video", "audio", "voice", "sticker"])
def ignore_other_messages(message):
    if not is_verified(message.from_user.id):
        ask_contact(message.chat.id)
        return
    bot.reply_to(message, "استخدم أزرار القائمة أو /help.")


def notify_hosting_users(text):
    """Notify users with saved jobs; failures are logged and retried on recovery."""
    recipients = set()
    for meta in jobs.values():
        if meta.get("owner_id") and meta.get("chat_id"):
            # Only notify people who have a hosted/submitted/running job.
            if meta.get("status") in {"submitted", "pending", "running", "interrupted", "error"}:
                recipients.add((int(meta["owner_id"]), int(meta["chat_id"])))
    for uid, chat_id in recipients:
        try:
            bot.send_message(chat_id, text)
        except Exception:
            logging.warning("Could not send service notice to user %s", uid)


def shutdown_handler(signum, frame):
    # Do not kill hosted scripts just because the bot process is stopping.
    # Android/Termux can still terminate child processes independently.
    logging.info("Bot process stopping; leaving managed script process groups untouched.")
    raise SystemExit(0)


signal.signal(signal.SIGTERM, shutdown_handler)
signal.signal(signal.SIGINT, shutdown_handler)


if __name__ == "__main__":
    logging.info("Astedafa bot starting.")
    connection_down = False
    while True:
        try:
            # none_stop=False lets the outer loop detect a polling/network failure.
            bot.polling(
                none_stop=False,
                interval=1,
                timeout=20,
                long_polling_timeout=20,
                skip_pending=True,
                allowed_updates=["message", "callback_query"],
            )
            # If polling returned after a previous outage, verify the API is reachable.
            bot.get_me()
            if connection_down:
                notify_hosting_users(
                    "🟢 *ASTEDAFA PRO عاد للعمل!*\n"
                    "تم استعادة اتصال البوت بتيليجرام. تحقق من «طلباتي» لمعرفة حالة كل سكربت؛ "
                    "عودة البوت لا تعني أن كل سكربت أعاد التشغيل تلقائيًا."
                )
                connection_down = False
        except KeyboardInterrupt:
            break
        except Exception:
            logging.exception("Polling/network error; retrying.")
            if not connection_down:
                connection_down = True
                notify_hosting_users(
                    "🟠 *تنبيه ASTEDAFA PRO*\n"
                    "اتصال البوت بتيليجرام انقطع أو حدث خطأ مؤقت. سنحاول الاتصال مجددًا. "
                    "قد يتأخر وصول الإشعارات أثناء انقطاع الإنترنت."
                )
            # Wait until Telegram API becomes reachable, then send recovery notice.
            while True:
                time.sleep(8)
                try:
                    bot.get_me()
                    # Telegram cannot deliver a message while the phone has no internet.
                    # Send the outage notice after reconnection, followed by the recovery notice.
                    notify_hosting_users(
                        "🟠 *إشعار انقطاع مؤقت*\n"
                        "حدث انقطاع اتصال مؤقت مع تيليجرام/الإنترنت. قد تكون الإشعارات تأخرت أثناء الانقطاع."
                    )
                    notify_hosting_users(
                        "🟢 *ASTEDAFA PRO مستمر وشغال!*\n"
                        "عاد اتصال البوت بتيليجرام. افتح «طلباتي» للتأكد من حالة السكربت الخاص بك؛ "
                        "قد تحتاج بعض السكربتات إلى تشغيل يدوي إذا توقفت أثناء الانقطاع."
                    )
                    connection_down = False
                    break
                except Exception:
                    logging.warning("Telegram still unreachable; retrying connection.")

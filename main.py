# main.py
"""
Attendance Bot with FastAPI webhook (python-telegram-bot v13.15)

✅ Features:
 - Inline buttons: Work / Off / Eat / Toilet / Smoke / Meeting / Back
 - Tracks start/end time of each activity
 - Enforces time limits with warnings & fines
 - Daily reset at 15:00
 - Monthly report on the 1st at 15:05
 - /report and /fine admin commands
 - Multilingual: /zh /en /km
 - Auto-leaves if added by non-admin
"""

import os
import logging
import datetime
from typing import Dict, Any
import imghdr
from fastapi import FastAPI

app = FastAPI()

@app.get("/")
def home():
    return {"status": "ok"}

from telegram import (
    Bot, InlineKeyboardButton, InlineKeyboardMarkup, Update
)
from telegram.ext import (
    Dispatcher, CommandHandler, CallbackQueryHandler,
    CallbackContext, MessageHandler, Filters, JobQueue
)

# ================= CONFIG =================
BOT_TOKEN = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "https://your-app.onrender.com/webhook")

ADMIN_USER_IDS = {7124683213}   # your Telegram ID(s)

DAILY_RESET_HOUR = 15
DAILY_RESET_MIN = 0
MONTHLY_REPORT_DAY = 1
MONTHLY_REPORT_HOUR = 15
MONTHLY_REPORT_MIN = 5

ACTIVITY_LIMITS = {
    "eat": {"limit_min": 30, "fine": 10},
    "toilet": {"limit_min": 15, "fine": 10},
    "smoke": {"limit_min": 10, "fine": 10},
    "meeting": {"limit_min": 60, "fine": 0},
}
LATE_WORK_FINE = 50

# ================= LOGGING =================
logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# ================= IN-MEMORY STORAGE =================
group_data: Dict[int, Dict[int, Dict[str, Any]]] = {}

# ================= MULTI-LANGUAGE LABELS =================
NAMES = {
    "work": {"zh": "上班", "en": "Start Work", "km": "ចាប់ផ្តើមការងារ"},
    "off": {"zh": "下班", "en": "End Work", "km": "បញ្ចប់ការងារ"},
    "eat": {"zh": "吃饭", "en": "Eat", "km": "បរិភោគ"},
    "toilet": {"zh": "上厕所", "en": "Toilet", "km": "បង្គន់"},
    "smoke": {"zh": "抽烟", "en": "Smoke", "km": "ជក់បារី"},
    "meeting": {"zh": "会议", "en": "Meeting", "km": "ប្រជុំ"},
    "back": {"zh": "回座", "en": "Back", "km": "ត្រឡប់"},
    "menu_title": {"zh": "请点击下面按钮打卡", "en": "Please tap a button", "km": "សូមចុចប៊ូតុង"},
    "no_activity": {"zh": "⚠️ 您当前没有正在进行的活动。", "en": "⚠️ No activity running.", "km": "⚠️ គ្មានសកម្មភាពកំពុងធ្វើ។"},
}

# ================= HELPERS =================
def ensure_user(chat_id: int, user_id: int, name: str):
    if chat_id not in group_data:
        group_data[chat_id] = {}
    users = group_data[chat_id]
    if user_id not in users:
        users[user_id] = {
            "name": name,
            "activities": [],
            "daily_fines": 0,
            "monthly_fines": 0,
            "lang": "zh",
            "work_start": None,
        }
    return users[user_id]

def format_td(td: datetime.timedelta) -> str:
    total = int(td.total_seconds())
    h, m, s = total // 3600, (total % 3600) // 60, total % 60
    return " ".join([f"{h}h" if h else "", f"{m}m" if m else "", f"{s}s" if s else ""]).strip()

def make_inline_menu(lang: str = "zh") -> InlineKeyboardMarkup:
    kb = [
        [InlineKeyboardButton(NAMES['work'][lang], callback_data="work"),
         InlineKeyboardButton(NAMES['off'][lang], callback_data="off")],
        [InlineKeyboardButton(NAMES['eat'][lang], callback_data="eat"),
         InlineKeyboardButton(NAMES['toilet'][lang], callback_data="toilet"),
         InlineKeyboardButton(NAMES['smoke'][lang], callback_data="smoke")],
        [InlineKeyboardButton(NAMES['meeting'][lang], callback_data="meeting")],
        [InlineKeyboardButton(NAMES['back'][lang], callback_data="back")],
    ]
    return InlineKeyboardMarkup(kb)

# ================= COMMANDS =================
def cmd_start(update: Update, context: CallbackContext):
    user = ensure_user(update.effective_chat.id, update.effective_user.id, update.effective_user.full_name)
    update.message.reply_text(NAMES["menu_title"][user["lang"]], reply_markup=make_inline_menu(user["lang"]))

def cmd_set_lang(update: Update, context: CallbackContext, lang: str):
    u = ensure_user(update.effective_chat.id, update.effective_user.id, update.effective_user.full_name)
    u["lang"] = lang
    cmd_start(update, context)

def cmd_set_zh(update, context): cmd_set_lang(update, context, "zh")
def cmd_set_en(update, context): cmd_set_lang(update, context, "en")
def cmd_set_km(update, context): cmd_set_lang(update, context, "km")

# ================= AUTO-LEAVE =================
def handle_new_chat_members(update: Update, context: CallbackContext):
    chat = update.effective_chat
    bot_id = context.bot.id
    for member in update.message.new_chat_members:
        if member.id == bot_id:  # bot added
            adder = update.message.from_user.id
            if adder not in ADMIN_USER_IDS:
                context.bot.send_message(chat_id=chat.id, text="⚠️ Only admins can add me. Leaving...")
                context.bot.leave_chat(chat.id)
                for admin in ADMIN_USER_IDS:
                    context.bot.send_message(admin, f"❌ Bot was added to {chat.title} by unauthorized user {update.message.from_user.full_name}")

# ================= CALLBACK HANDLER =================
def button_handler(update: Update, context: CallbackContext):
    query = update.callback_query
    user = ensure_user(update.effective_chat.id, query.from_user.id, query.from_user.full_name)
    now = datetime.datetime.now()

    if query.data == "work":
        user["work_start"] = now
        query.answer("✅ Work started")
    elif query.data == "off":
        user["work_start"] = None
        query.answer("✅ Work ended")
    elif query.data in ACTIVITY_LIMITS:
        user["activities"].append({"type": query.data, "start": now})
        query.answer(f"Started {query.data}")
        limit = ACTIVITY_LIMITS[query.data]["limit_min"]
        # schedule warning + timeout
        context.job_queue.run_once(send_warning_job, limit*60 - 60, context=(update.effective_chat.id, query.from_user.id, query.data))
        context.job_queue.run_once(timeout_job, limit*60, context=(update.effective_chat.id, query.from_user.id, query.data))
    elif query.data == "back":
        if not user["activities"]:
            query.answer(NAMES["no_activity"][user["lang"]])
            return
        last = user["activities"].pop()
        duration = now - last["start"]
        query.answer(f"Back from {last['type']} ({format_td(duration)})")

# ================= JOBS =================
def send_warning_job(context: CallbackContext):
    chat_id, user_id, act = context.job.context
    context.bot.send_message(chat_id, f"⚠️ <a href='tg://user?id={user_id}'>User</a>, 1 minute left for {act}!", parse_mode="HTML")

def timeout_job(context: CallbackContext):
    chat_id, user_id, act = context.job.context
    user = ensure_user(chat_id, user_id, str(user_id))
    fine = ACTIVITY_LIMITS[act]["fine"]
    user["daily_fines"] += fine
    user["monthly_fines"] += fine
    context.bot.send_message(chat_id, f"⏰ <a href='tg://user?id={user_id}'>User</a> timeout on {act}! Fine {fine}元", parse_mode="HTML")

def daily_reset_job(context: CallbackContext):
    for chat_id, users in group_data.items():
        for u in users.values():
            u["daily_fines"] = 0
    context.bot.send_message(list(group_data.keys())[0], "🔄 Daily reset complete.")

def monthly_report_job(context: CallbackContext):
    for chat_id, users in group_data.items():
        report = "📊 Monthly Report:\n"
        for u in users.values():
            report += f"{u['name']}: {u['monthly_fines']} 元\n"
            u["monthly_fines"] = 0
        context.bot.send_message(chat_id, report)

# ================= ADMIN COMMANDS =================
def cmd_report(update: Update, context: CallbackContext):
    if update.effective_user.id not in ADMIN_USER_IDS:
        return
    chat_id = update.effective_chat.id
    users = group_data.get(chat_id, {})
    report = "📊 Daily Report:\n"
    for u in users.values():
        report += f"{u['name']}: {u['daily_fines']} 元\n"
    update.message.reply_text(report)

def cmd_fine(update: Update, context: CallbackContext):
    if update.effective_user.id not in ADMIN_USER_IDS:
        return
    if len(context.args) < 2:
        update.message.reply_text("Usage: /fine user_id amount")
        return
    uid, amount = int(context.args[0]), int(context.args[1])
    user = ensure_user(update.effective_chat.id, uid, str(uid))
    user["daily_fines"] += amount
    user["monthly_fines"] += amount
    update.message.reply_text(f"✅ Fine {amount} added to {uid}")

# ================= FASTAPI APP =================
app = FastAPI()
bot = Bot(BOT_TOKEN)
dispatcher = Dispatcher(bot, None, workers=0, use_context=True)

# Handlers
dispatcher.add_handler(CommandHandler("start", cmd_start))
dispatcher.add_handler(CommandHandler("zh", cmd_set_zh))
dispatcher.add_handler(CommandHandler("en", cmd_set_en))
dispatcher.add_handler(CommandHandler("km", cmd_set_km))
dispatcher.add_handler(MessageHandler(Filters.status_update.new_chat_members, handle_new_chat_members))
dispatcher.add_handler(CallbackQueryHandler(button_handler))
dispatcher.add_handler(CommandHandler("report", cmd_report))
dispatcher.add_handler(CommandHandler("fine", cmd_fine))

# Job queue
job_queue = JobQueue(bot)
job_queue.set_dispatcher(dispatcher)
job_queue.run_daily(daily_reset_job, time=datetime.time(hour=DAILY_RESET_HOUR, minute=DAILY_RESET_MIN))
job_queue.run_monthly(monthly_report_job, day=MONTHLY_REPORT_DAY, time=datetime.time(hour=MONTHLY_REPORT_HOUR, minute=MONTHLY_REPORT_MIN))
job_queue.start()

@app.on_event("startup")
async def startup_event():
    await bot.set_webhook(WEBHOOK_URL)

@app.post("/webhook")
async def webhook(request: Request):
    data = await request.json()
    update = Update.de_json(data, bot)
    dispatcher.process_update(update)
    return {"ok": True}





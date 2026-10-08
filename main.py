"""
WP CashBot - Telegram earning bot (pyTelegramBotAPI + SQLite)

Features:
  - Referral system (deep link, 2 Taka bonus)
  - Offerwall tasks added by admin, screenshot proof + admin approval
  - Withdrawals (bKash / Nagad) with admin approval
  - Anti-abuse: cooldown, duplicate screenshot detection, strikes + auto ban
  - Admin tools: /admin /addtask /tasks /deltask /broadcast /ban /unban /backup

Configuration is done ONLY through environment variables:
  BOT_TOKEN, ADMIN_ID, BOT_USERNAME (optional: DB_PATH, PORT)
"""

import functools
import html
import logging
import os
import re
import sqlite3
import sys
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import telebot
from telebot import types
from telebot.apihelper import ApiTelegramException

# ============================================================
# LOGGING
# ============================================================
logging.basicConfig(
    stream=sys.stdout,
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("wpcashbot")
telebot.logger.setLevel(logging.WARNING)

# ============================================================
# CONFIGURATION (environment variables)
# ============================================================
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_ID_RAW = os.getenv("ADMIN_ID", "").strip()
BOT_USERNAME = os.getenv("BOT_USERNAME", "").strip().lstrip("@")
DB_PATH = os.getenv("DB_PATH", "wpcash.db").strip() or "wpcash.db"
PORT = int(os.getenv("PORT", "10000"))

if not BOT_TOKEN:
    logger.error("BOT_TOKEN environment variable is required.")
    sys.exit(1)
if not ADMIN_ID_RAW.isdigit():
    logger.error("ADMIN_ID environment variable must be a numeric Telegram user ID.")
    sys.exit(1)
ADMIN_ID = int(ADMIN_ID_RAW)

# ============================================================
# CONSTANTS
# ============================================================
CURRENCY = "Taka"
REFERRAL_BONUS = 2.0              # Taka paid to the referrer per new user
MIN_WITHDRAW = 50.0               # Minimum withdrawal in Taka
SUBMISSION_COOLDOWN = 10 * 60     # 1 submission per user per 10 minutes (seconds)
MAX_STRIKES = 3                   # Fraud strikes before automatic ban
STATE_TTL = 15 * 60               # Pending input states expire after 15 minutes
MAX_TASKS_SHOWN = 15              # Max tasks listed at once in "Earn Money"
LOCAL_TZ = timezone(timedelta(hours=6))  # Bangladesh time (UTC+6) for display

BTN_EARN = "📱 Earn Money"
BTN_PROFILE = "👤 My Profile"
BTN_REFER = "👫 Refer & Earn"
BTN_WITHDRAW = "💳 Withdraw"

WITHDRAW_METHODS = ("bKash", "Nagad")
# Bangladeshi mobile number: 01XXXXXXXXX (optionally prefixed with +88 / 88)
BD_NUMBER_RE = re.compile(r"^(?:\+?88)?(01[3-9]\d{8})$")

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML", threaded=True)


# ============================================================
# SMALL HELPERS
# ============================================================
def esc(value) -> str:
    """Escape user-controlled text for safe use in HTML messages."""
    return html.escape(str(value if value is not None else ""))


def money(value: float) -> str:
    """Format a Taka amount with two decimals."""
    return f"{float(value):.2f}"


def now_str() -> str:
    """Current UTC time as 'YYYY-MM-DD HH:MM:SS'."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def parse_ts(value: str) -> datetime:
    """Parse a stored UTC timestamp string."""
    return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)


def fmt_local(value: str, with_time: bool = False) -> str:
    """Convert a stored UTC timestamp to local (UTC+6) display text."""
    try:
        dt = parse_ts(value).astimezone(LOCAL_TZ)
        return dt.strftime("%d %b %Y %H:%M" if with_time else "%d %b %Y")
    except Exception:
        return str(value)


def is_admin(user_id: int) -> bool:
    return user_id == ADMIN_ID


def build_task_url(url: str, user_id: int) -> str:
    """Replace the optional {user_id} placeholder so networks can track the user."""
    return url.replace("{user_id}", str(user_id))


# ============================================================
# DATABASE
# ============================================================
_db_lock = threading.Lock()


@contextmanager
def db():
    """
    Open a short-lived SQLite connection guarded by a global lock.
    Commits on success, rolls back on error. Do not nest db() calls.
    """
    with _db_lock:
        conn = sqlite3.connect(DB_PATH, timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def init_db() -> None:
    """Create all tables if they do not exist yet."""
    with db() as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id     INTEGER PRIMARY KEY,
                username    TEXT,
                balance     REAL    NOT NULL DEFAULT 0,
                referrals   INTEGER NOT NULL DEFAULT 0,
                ref_earned  REAL    NOT NULL DEFAULT 0,
                referred_by INTEGER,
                join_date   TEXT    NOT NULL,
                banned      INTEGER NOT NULL DEFAULT 0,
                strikes     INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS tasks (
                task_id INTEGER PRIMARY KEY AUTOINCREMENT,
                title   TEXT    NOT NULL,
                url     TEXT    NOT NULL,
                reward  REAL    NOT NULL,
                active  INTEGER NOT NULL DEFAULT 1
            );

            CREATE TABLE IF NOT EXISTS submissions (
                id                 INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id            INTEGER NOT NULL,
                task_id            INTEGER NOT NULL,
                screenshot_file_id TEXT    NOT NULL,
                file_unique_id     TEXT    NOT NULL,
                status             TEXT    NOT NULL DEFAULT 'pending',
                timestamp          TEXT    NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_sub_unique ON submissions(file_unique_id);
            CREATE INDEX IF NOT EXISTS idx_sub_user   ON submissions(user_id);

            CREATE TABLE IF NOT EXISTS withdrawals (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id   INTEGER NOT NULL,
                method    TEXT    NOT NULL,
                number    TEXT    NOT NULL,
                amount    REAL    NOT NULL,
                status    TEXT    NOT NULL DEFAULT 'pending',
                timestamp TEXT    NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_wd_user ON withdrawals(user_id);
            """
        )
    logger.info("Database ready at %s", DB_PATH)


# ---------------- users ----------------
def get_user(user_id: int):
    with db() as conn:
        return conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()


def register_user(tg_user, referred_by=None):
    """
    Create the user if missing.
    Returns (is_new, credited_referrer_id). The referrer is credited only when the
    user is brand new, the referrer exists, is not banned and is not the user.
    """
    uid = tg_user.id
    credited = None
    with db() as conn:
        exists = conn.execute("SELECT 1 FROM users WHERE user_id=?", (uid,)).fetchone()
        if exists:
            conn.execute("UPDATE users SET username=? WHERE user_id=?", (tg_user.username, uid))
            return False, None

        valid_ref = None
        if referred_by and referred_by != uid:
            ref = conn.execute(
                "SELECT banned FROM users WHERE user_id=?", (referred_by,)
            ).fetchone()
            if ref and not ref["banned"]:
                valid_ref = referred_by

        conn.execute(
            "INSERT INTO users (user_id, username, referred_by, join_date) VALUES (?,?,?,?)",
            (uid, tg_user.username, valid_ref, now_str()),
        )
        if valid_ref:
            conn.execute(
                "UPDATE users SET referrals = referrals + 1, balance = balance + ?, "
                "ref_earned = ref_earned + ? WHERE user_id=?",
                (REFERRAL_BONUS, REFERRAL_BONUS, valid_ref),
            )
            credited = valid_ref
    return True, credited


def _add_strike(conn, user_id: int):
    """Add a fraud strike inside an open transaction. Returns (strikes, banned)."""
    conn.execute("UPDATE users SET strikes = strikes + 1 WHERE user_id=?", (user_id,))
    row = conn.execute("SELECT strikes FROM users WHERE user_id=?", (user_id,)).fetchone()
    strikes = row["strikes"] if row else 0
    banned = False
    if strikes >= MAX_STRIKES and user_id != ADMIN_ID:
        conn.execute("UPDATE users SET banned=1 WHERE user_id=?", (user_id,))
        banned = True
    return strikes, banned


def add_strike(user_id: int):
    with db() as conn:
        return _add_strike(conn, user_id)


def set_ban(user_id: int, banned: bool) -> bool:
    """Ban or unban a user. Returns False if the user does not exist."""
    with db() as conn:
        cur = conn.execute(
            "UPDATE users SET banned=?, strikes=CASE WHEN ?=0 THEN 0 ELSE strikes END "
            "WHERE user_id=?",
            (1 if banned else 0, 1 if banned else 0, user_id),
        )
        return cur.rowcount > 0


# ---------------- tasks ----------------
def get_active_tasks():
    with db() as conn:
        return conn.execute(
            "SELECT * FROM tasks WHERE active=1 ORDER BY task_id DESC LIMIT ?",
            (MAX_TASKS_SHOWN,),
        ).fetchall()


def get_task(task_id: int):
    with db() as conn:
        return conn.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()


def add_task(title: str, url: str, reward: float) -> int:
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO tasks (title, url, reward, active) VALUES (?,?,?,1)",
            (title, url, reward),
        )
        return cur.lastrowid


# ---------------- submissions ----------------
def cooldown_remaining(user_id: int) -> int:
    """Seconds left before the user may submit again (0 if allowed)."""
    with db() as conn:
        row = conn.execute(
            "SELECT timestamp FROM submissions WHERE user_id=? ORDER BY id DESC LIMIT 1",
            (user_id,),
        ).fetchone()
    if not row:
        return 0
    elapsed = (datetime.now(timezone.utc) - parse_ts(row["timestamp"])).total_seconds()
    return max(0, int(SUBMISSION_COOLDOWN - elapsed))


def has_open_submission(user_id: int, task_id: int) -> bool:
    """True if the user already has a pending or approved submission for this task."""
    with db() as conn:
        row = conn.execute(
            "SELECT 1 FROM submissions WHERE user_id=? AND task_id=? "
            "AND status IN ('pending','approved') LIMIT 1",
            (user_id, task_id),
        ).fetchone()
    return row is not None


def screenshot_exists(file_unique_id: str) -> bool:
    with db() as conn:
        row = conn.execute(
            "SELECT 1 FROM submissions WHERE file_unique_id=? LIMIT 1", (file_unique_id,)
        ).fetchone()
    return row is not None


def create_submission(user_id, task_id, file_id, file_unique_id) -> int:
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO submissions (user_id, task_id, screenshot_file_id, "
            "file_unique_id, status, timestamp) VALUES (?,?,?,?, 'pending', ?)",
            (user_id, task_id, file_id, file_unique_id, now_str()),
        )
        return cur.lastrowid


def delete_submission(sub_id: int) -> None:
    with db() as conn:
        conn.execute("DELETE FROM submissions WHERE id=?", (sub_id,))


def approve_submission(sub_id: int):
    """Approve a pending submission and credit the reward. None if not pending."""
    with db() as conn:
        row = conn.execute(
            "SELECT s.id, s.user_id, s.status, t.reward, t.title "
            "FROM submissions s JOIN tasks t ON t.task_id = s.task_id WHERE s.id=?",
            (sub_id,),
        ).fetchone()
        if not row or row["status"] != "pending":
            return None
        conn.execute("UPDATE submissions SET status='approved' WHERE id=?", (sub_id,))
        conn.execute(
            "UPDATE users SET balance = balance + ? WHERE user_id=?",
            (row["reward"], row["user_id"]),
        )
        return dict(row)


def reject_submission(sub_id: int, fraud: bool = False):
    """Reject a pending submission, optionally adding a fraud strike."""
    with db() as conn:
        row = conn.execute(
            "SELECT user_id, status FROM submissions WHERE id=?", (sub_id,)
        ).fetchone()
        if not row or row["status"] != "pending":
            return None
        conn.execute("UPDATE submissions SET status='rejected' WHERE id=?", (sub_id,))
        result = {"user_id": row["user_id"], "strikes": None, "banned": False}
        if fraud:
            result["strikes"], result["banned"] = _add_strike(conn, row["user_id"])
        return result


# ---------------- withdrawals ----------------
def has_pending_withdrawal(user_id: int) -> bool:
    with db() as conn:
        row = conn.execute(
            "SELECT 1 FROM withdrawals WHERE user_id=? AND status='pending' LIMIT 1",
            (user_id,),
        ).fetchone()
    return row is not None


def create_withdrawal(user_id: int, method: str, number: str, amount: float):
    """
    Create a pending withdrawal request.
    Returns (withdrawal_id, error_code). error_code: None | 'pending' | 'balance'.
    """
    with db() as conn:
        pending = conn.execute(
            "SELECT 1 FROM withdrawals WHERE user_id=? AND status='pending' LIMIT 1",
            (user_id,),
        ).fetchone()
        if pending:
            return None, "pending"
        user = conn.execute("SELECT balance FROM users WHERE user_id=?", (user_id,)).fetchone()
        if not user or user["balance"] < amount:
            return None, "balance"
        cur = conn.execute(
            "INSERT INTO withdrawals (user_id, method, number, amount, status, timestamp) "
            "VALUES (?,?,?,?, 'pending', ?)",
            (user_id, method, number, amount, now_str()),
        )
        return cur.lastrowid, None


def delete_withdrawal(wd_id: int) -> None:
    with db() as conn:
        conn.execute("DELETE FROM withdrawals WHERE id=?", (wd_id,))


def approve_withdrawal(wd_id: int):
    """
    Deduct the balance and mark the withdrawal as paid.
    Returns (status, row): status is 'paid', 'insufficient' or None (not pending).
    """
    with db() as conn:
        row = conn.execute("SELECT * FROM withdrawals WHERE id=?", (wd_id,)).fetchone()
        if not row or row["status"] != "pending":
            return None, None
        cur = conn.execute(
            "UPDATE users SET balance = balance - ? WHERE user_id=? AND balance >= ?",
            (row["amount"], row["user_id"], row["amount"]),
        )
        if cur.rowcount == 0:
            conn.execute("UPDATE withdrawals SET status='rejected' WHERE id=?", (wd_id,))
            return "insufficient", dict(row)
        conn.execute("UPDATE withdrawals SET status='paid' WHERE id=?", (wd_id,))
        return "paid", dict(row)


def reject_withdrawal(wd_id: int):
    """Reject a pending withdrawal. Balance is untouched. None if not pending."""
    with db() as conn:
        row = conn.execute("SELECT * FROM withdrawals WHERE id=?", (wd_id,)).fetchone()
        if not row or row["status"] != "pending":
            return None
        conn.execute("UPDATE withdrawals SET status='rejected' WHERE id=?", (wd_id,))
        return dict(row)


# ============================================================
# IN-MEMORY CONVERSATION STATE (expires automatically)
# ============================================================
_states = {}
_states_lock = threading.Lock()


def set_state(user_id: int, **data) -> None:
    with _states_lock:
        _states[user_id] = {**data, "ts": time.time()}


def get_state(user_id: int):
    with _states_lock:
        state = _states.get(user_id)
        if not state:
            return None
        if time.time() - state["ts"] > STATE_TTL:
            del _states[user_id]
            return None
        return dict(state)


def clear_state(user_id: int) -> None:
    with _states_lock:
        _states.pop(user_id, None)


# ============================================================
# UI HELPERS / DECORATORS
# ============================================================
def main_menu() -> types.ReplyKeyboardMarkup:
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add(
        types.KeyboardButton(BTN_EARN),
        types.KeyboardButton(BTN_PROFILE),
        types.KeyboardButton(BTN_REFER),
        types.KeyboardButton(BTN_WITHDRAW),
    )
    return markup


def safe_handler(fn):
    """Catch and log any exception so one bad update never crashes the bot."""

    @functools.wraps(fn)
    def wrapper(obj):
        try:
            return fn(obj)
        except Exception:
            logger.exception("Error in handler %s", fn.__name__)
            try:
                if isinstance(obj, types.CallbackQuery):
                    bot.answer_callback_query(obj.id, "⚠️ Something went wrong.")
                else:
                    bot.send_message(obj.chat.id, "⚠️ Something went wrong. Please try again.")
            except Exception:
                pass

    return wrapper


def admin_only(fn):
    """Allow a command only for ADMIN_ID (silently ignored for everyone else)."""

    @functools.wraps(fn)
    def wrapper(message):
        if not is_admin(message.from_user.id):
            return None
        return fn(message)

    return wrapper


def guard(message) -> bool:
    """Register the user if needed and block banned users. True = allowed."""
    register_user(message.from_user)
    user = get_user(message.from_user.id)
    if user and user["banned"]:
        bot.send_message(message.chat.id, "🚫 Your account has been banned.")
        return False
    return True


def guard_callback(call) -> bool:
    """Same as guard() but for inline button presses."""
    register_user(call.from_user)
    user = get_user(call.from_user.id)
    if user and user["banned"]:
        bot.answer_callback_query(call.id, "🚫 Your account has been banned.", show_alert=True)
        return False
    return True


def safe_send(chat_id, text, **kwargs) -> bool:
    """Send a message and swallow errors (e.g. user blocked the bot)."""
    try:
        bot.send_message(chat_id, text, **kwargs)
        return True
    except Exception as exc:
        logger.warning("Could not message %s: %s", chat_id, exc)
        return False


# ============================================================
# /start, /cancel
# ============================================================
@bot.message_handler(commands=["start"], chat_types=["private"])
@safe_handler
def cmd_start(message):
    user_id = message.from_user.id
    clear_state(user_id)

    # Referral deep link: https://t.me/BOT?start=REFERRER_ID
    referred_by = None
    parts = message.text.split(maxsplit=1)
    if len(parts) > 1 and parts[1].strip().isdigit():
        referred_by = int(parts[1].strip())

    _, credited = register_user(message.from_user, referred_by)

    user = get_user(user_id)
    if user and user["banned"]:
        bot.send_message(message.chat.id, "🚫 Your account has been banned.")
        return

    bot.send_message(
        message.chat.id,
        f"👋 Welcome to <b>WP CashBot</b>, {esc(message.from_user.first_name)}!\n\n"
        f"Complete tasks, invite friends and withdraw your earnings via bKash or Nagad.\n"
        f"Use the menu below to get started.",
        reply_markup=main_menu(),
    )

    if credited:
        safe_send(
            credited,
            f"🎉 A new user joined with your link!\n"
            f"💰 You earned <b>{money(REFERRAL_BONUS)} {CURRENCY}</b>.",
        )


@bot.message_handler(commands=["cancel"], chat_types=["private"])
@safe_handler
def cmd_cancel(message):
    clear_state(message.from_user.id)
    bot.send_message(message.chat.id, "✅ Cancelled.", reply_markup=main_menu())


# ============================================================
# 👤 MY PROFILE
# ============================================================
@bot.message_handler(func=lambda m: m.text == BTN_PROFILE, chat_types=["private"])
@safe_handler
def show_profile(message):
    if not guard(message):
        return
    clear_state(message.from_user.id)
    user = get_user(message.from_user.id)
    bot.send_message(
        message.chat.id,
        f"👤 <b>My Profile</b>\n\n"
        f"🆔 User ID: <code>{user['user_id']}</code>\n"
        f"💰 Balance: <b>{money(user['balance'])} {CURRENCY}</b>\n"
        f"👥 Total Referrals: {user['referrals']}\n"
        f"📅 Join Date: {fmt_local(user['join_date'])}",
    )


# ============================================================
# 👫 REFER & EARN
# ============================================================
@bot.message_handler(func=lambda m: m.text == BTN_REFER, chat_types=["private"])
@safe_handler
def show_refer(message):
    if not guard(message):
        return
    clear_state(message.from_user.id)
    user = get_user(message.from_user.id)
    link = f"https://t.me/{BOT_USERNAME}?start={user['user_id']}"
    bot.send_message(
        message.chat.id,
        f"👫 <b>Refer &amp; Earn</b>\n\n"
        f"Earn <b>{money(REFERRAL_BONUS)} {CURRENCY}</b> for every friend who joins with your link.\n\n"
        f"🔗 Your link:\n{link}\n\n"
        f"👥 Total referrals: <b>{user['referrals']}</b>\n"
        f"💰 Earned from referrals: <b>{money(user['ref_earned'])} {CURRENCY}</b>",
        disable_web_page_preview=True,
    )


# ============================================================
# 📱 EARN MONEY
# ============================================================
@bot.message_handler(func=lambda m: m.text == BTN_EARN, chat_types=["private"])
@safe_handler
def show_tasks(message):
    if not guard(message):
        return
    user_id = message.from_user.id
    clear_state(user_id)

    tasks = get_active_tasks()
    if not tasks:
        bot.send_message(message.chat.id, "😕 No tasks available right now. Please check back later.")
        return

    bot.send_message(
        message.chat.id,
        "📱 <b>Available Tasks</b>\n\n"
        "1️⃣ Tap <b>Open Task</b> and complete it.\n"
        "2️⃣ Tap <b>Submit Proof</b> and send a clear screenshot.\n"
        "3️⃣ Your reward is added after admin approval.",
    )
    for task in tasks:
        try:
            kb = types.InlineKeyboardMarkup()
            kb.add(types.InlineKeyboardButton("🔗 Open Task", url=build_task_url(task["url"], user_id)))
            kb.add(types.InlineKeyboardButton("📸 Submit Proof", callback_data=f"proof:{task['task_id']}"))
            bot.send_message(
                message.chat.id,
                f"🎯 <b>{esc(task['title'])}</b>\n💰 Reward: <b>{money(task['reward'])} {CURRENCY}</b>",
                reply_markup=kb,
            )
        except Exception:
            logger.exception("Could not show task %s", task["task_id"])


@bot.callback_query_handler(func=lambda c: c.data.startswith("proof:"))
@safe_handler
def cb_submit_proof(call):
    if not guard_callback(call):
        return
    user_id = call.from_user.id
    task_id = int(call.data.split(":")[1])

    task = get_task(task_id)
    if not task or not task["active"]:
        bot.answer_callback_query(call.id, "This task is no longer available.", show_alert=True)
        return
    if has_open_submission(user_id, task_id):
        bot.answer_callback_query(
            call.id, "You already submitted proof for this task.", show_alert=True
        )
        return
    wait = cooldown_remaining(user_id)
    if wait > 0:
        bot.answer_callback_query(
            call.id,
            f"⏳ Please wait {wait // 60}m {wait % 60}s before your next submission.",
            show_alert=True,
        )
        return

    set_state(user_id, type="proof", task_id=task_id)
    bot.answer_callback_query(call.id)
    bot.send_message(
        call.message.chat.id,
        f"📸 Send your screenshot for <b>{esc(task['title'])}</b> now.\n"
        f"Send it as a <b>photo</b> (not as a file). Send /cancel to abort.",
    )


@bot.message_handler(content_types=["photo"], chat_types=["private"])
@safe_handler
def on_photo(message):
    if not guard(message):
        return
    user_id = message.from_user.id
    state = get_state(user_id)
    if not state or state.get("type") != "proof":
        bot.send_message(
            message.chat.id,
            "ℹ️ To submit proof, open <b>📱 Earn Money</b> and tap <b>Submit Proof</b> under a task.",
        )
        return

    task = get_task(state["task_id"])
    if not task or not task["active"]:
        clear_state(user_id)
        bot.send_message(message.chat.id, "❌ This task is no longer available.")
        return

    # --- Anti-abuse checks ---
    if has_open_submission(user_id, task["task_id"]):
        clear_state(user_id)
        bot.send_message(message.chat.id, "❌ You already submitted proof for this task.")
        return

    wait = cooldown_remaining(user_id)
    if wait > 0:
        clear_state(user_id)
        bot.send_message(
            message.chat.id,
            f"⏳ Rate limit: please wait {wait // 60}m {wait % 60}s before submitting again.",
        )
        return

    photo = message.photo[-1]  # highest resolution
    if screenshot_exists(photo.file_unique_id):
        clear_state(user_id)
        strikes, banned = add_strike(user_id)
        if banned:
            bot.send_message(
                message.chat.id,
                "🚫 Duplicate screenshot detected. Your account has been banned for repeated fraud.",
            )
            safe_send(
                ADMIN_ID,
                f"🚫 User <code>{user_id}</code> was auto-banned (duplicate screenshots, "
                f"{strikes} strikes).",
            )
        else:
            bot.send_message(
                message.chat.id,
                f"⚠️ This screenshot was already submitted. Fake or reused proofs are not allowed.\n"
                f"Strike {strikes}/{MAX_STRIKES} — you will be banned at {MAX_STRIKES}.",
            )
        return

    # --- Save and forward to admin ---
    sub_id = create_submission(user_id, task["task_id"], photo.file_id, photo.file_unique_id)

    kb = types.InlineKeyboardMarkup()
    kb.row(
        types.InlineKeyboardButton("✅ Approve", callback_data=f"sub:a:{sub_id}"),
        types.InlineKeyboardButton("❌ Reject", callback_data=f"sub:r:{sub_id}"),
    )
    kb.add(types.InlineKeyboardButton("🚫 Reject + Fraud Strike", callback_data=f"sub:f:{sub_id}"))

    caption = (
        f"📸 <b>New Proof</b> #{sub_id}\n"
        f"👤 <a href=\"tg://user?id={user_id}\">{esc(message.from_user.first_name)}</a> "
        f"(<code>{user_id}</code>)\n"
        f"🎯 Task: {esc(task['title'])} (#{task['task_id']})\n"
        f"💰 Reward: {money(task['reward'])} {CURRENCY}\n"
        f"⏰ {fmt_local(now_str(), True)}"
    )
    try:
        bot.send_photo(ADMIN_ID, photo.file_id, caption=caption, reply_markup=kb)
    except Exception:
        logger.exception("Could not forward submission %s to admin", sub_id)
        delete_submission(sub_id)  # roll back so the user can retry
        clear_state(user_id)
        bot.send_message(
            message.chat.id, "⚠️ Could not deliver your proof to the admin. Please try again later."
        )
        return

    clear_state(user_id)
    bot.send_message(
        message.chat.id,
        "✅ Proof submitted! You will be notified once the admin reviews it.",
        reply_markup=main_menu(),
    )


@bot.message_handler(content_types=["document"], chat_types=["private"])
@safe_handler
def on_document(message):
    state = get_state(message.from_user.id)
    if state and state.get("type") == "proof":
        bot.send_message(message.chat.id, "⚠️ Please send the screenshot as a <b>photo</b>, not a file.")


@bot.callback_query_handler(func=lambda c: c.data.startswith("sub:"))
@safe_handler
def cb_review_submission(call):
    if not is_admin(call.from_user.id):
        bot.answer_callback_query(call.id, "Not allowed.", show_alert=True)
        return

    _, action, raw_id = call.data.split(":")
    sub_id = int(raw_id)

    if action == "a":
        result = approve_submission(sub_id)
        if not result:
            bot.answer_callback_query(call.id, "Already processed.", show_alert=True)
            return
        status_line = "✅ <b>APPROVED</b>"
        safe_send(
            result["user_id"],
            f"✅ Your proof for <b>{esc(result['title'])}</b> was approved!\n"
            f"💰 <b>{money(result['reward'])} {CURRENCY}</b> added to your balance.",
        )
    else:
        fraud = action == "f"
        result = reject_submission(sub_id, fraud=fraud)
        if not result:
            bot.answer_callback_query(call.id, "Already processed.", show_alert=True)
            return
        status_line = "🚫 <b>REJECTED + STRIKE</b>" if fraud else "❌ <b>REJECTED</b>"
        text = "❌ Your proof was rejected by the admin."
        if fraud:
            text += f"\n⚠️ Fraud strike {result['strikes']}/{MAX_STRIKES}."
            if result["banned"]:
                text += "\n🚫 Your account has been banned."
        safe_send(result["user_id"], text)

    bot.answer_callback_query(call.id, "Done.")
    try:
        bot.edit_message_caption(
            caption=f"{call.message.html_caption or ''}\n\n{status_line}",
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=None,
        )
    except Exception:
        logger.warning("Could not edit admin message for submission %s", sub_id)


# ============================================================
# 💳 WITHDRAW
# ============================================================
@bot.message_handler(func=lambda m: m.text == BTN_WITHDRAW, chat_types=["private"])
@safe_handler
def start_withdraw(message):
    if not guard(message):
        return
    user_id = message.from_user.id
    clear_state(user_id)
    user = get_user(user_id)

    if user["balance"] < MIN_WITHDRAW:
        bot.send_message(
            message.chat.id,
            f"❌ Minimum withdrawal is <b>{money(MIN_WITHDRAW)} {CURRENCY}</b>.\n"
            f"Your balance: <b>{money(user['balance'])} {CURRENCY}</b>",
        )
        return
    if has_pending_withdrawal(user_id):
        bot.send_message(
            message.chat.id, "⏳ You already have a pending withdrawal. Please wait for it to be processed."
        )
        return

    kb = types.InlineKeyboardMarkup()
    kb.row(*[types.InlineKeyboardButton(m, callback_data=f"wd:m:{m}") for m in WITHDRAW_METHODS])
    bot.send_message(
        message.chat.id,
        f"💳 <b>Withdraw</b>\nBalance: <b>{money(user['balance'])} {CURRENCY}</b>\n\n"
        f"Choose your payment method:",
        reply_markup=kb,
    )


@bot.callback_query_handler(func=lambda c: c.data.startswith("wd:m:"))
@safe_handler
def cb_withdraw_method(call):
    if not guard_callback(call):
        return
    method = call.data.split(":")[2]
    if method not in WITHDRAW_METHODS:
        bot.answer_callback_query(call.id, "Invalid method.", show_alert=True)
        return

    set_state(call.from_user.id, type="wd_number", method=method)
    bot.answer_callback_query(call.id)
    bot.send_message(
        call.message.chat.id,
        f"📱 Send your <b>{method}</b> number (e.g. 01XXXXXXXXX).\nSend /cancel to abort.",
    )


@bot.callback_query_handler(func=lambda c: c.data.startswith("wd:a:") or c.data.startswith("wd:r:"))
@safe_handler
def cb_review_withdrawal(call):
    if not is_admin(call.from_user.id):
        bot.answer_callback_query(call.id, "Not allowed.", show_alert=True)
        return

    _, action, raw_id = call.data.split(":")
    wd_id = int(raw_id)

    if action == "a":
        status, row = approve_withdrawal(wd_id)
        if status is None:
            bot.answer_callback_query(call.id, "Already processed.", show_alert=True)
            return
        if status == "insufficient":
            status_line = "⚠️ <b>REJECTED (insufficient balance)</b>"
            safe_send(row["user_id"], "❌ Your withdrawal was rejected: insufficient balance.")
        else:
            status_line = "✅ <b>PAID</b>"
            safe_send(
                row["user_id"],
                f"✅ Your withdrawal of <b>{money(row['amount'])} {CURRENCY}</b> via "
                f"{esc(row['method'])} has been paid. Thank you!",
            )
    else:
        row = reject_withdrawal(wd_id)
        if not row:
            bot.answer_callback_query(call.id, "Already processed.", show_alert=True)
            return
        status_line = "❌ <b>REJECTED</b>"
        safe_send(
            row["user_id"],
            f"❌ Your withdrawal of {money(row['amount'])} {CURRENCY} was rejected. "
            f"Your balance was not changed.",
        )

    bot.answer_callback_query(call.id, "Done.")
    try:
        bot.edit_message_text(
            f"{call.message.html_text or ''}\n\n{status_line}",
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=None,
        )
    except Exception:
        logger.warning("Could not edit admin message for withdrawal %s", wd_id)


# ============================================================
# ADMIN COMMANDS
# ============================================================
@bot.message_handler(commands=["admin"], chat_types=["private"])
@admin_only
@safe_handler
def cmd_admin(message):
    with db() as conn:
        total_users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        banned = conn.execute("SELECT COUNT(*) FROM users WHERE banned=1").fetchone()[0]
        total_paid = conn.execute(
            "SELECT COALESCE(SUM(amount),0) FROM withdrawals WHERE status='paid'"
        ).fetchone()[0]
        pending_subs = conn.execute(
            "SELECT COUNT(*) FROM submissions WHERE status='pending'"
        ).fetchone()[0]
        pending_wd = conn.execute(
            "SELECT COUNT(*) FROM withdrawals WHERE status='pending'"
        ).fetchone()[0]
        active_tasks = conn.execute("SELECT COUNT(*) FROM tasks WHERE active=1").fetchone()[0]

    bot.send_message(
        message.chat.id,
        f"🛠 <b>Admin Panel</b>\n\n"
        f"👥 Total users: <b>{total_users}</b> (banned: {banned})\n"
        f"💸 Total paid: <b>{money(total_paid)} {CURRENCY}</b>\n"
        f"📸 Pending submissions: <b>{pending_subs}</b>\n"
        f"💳 Pending withdrawals: <b>{pending_wd}</b>\n"
        f"🎯 Active tasks: <b>{active_tasks}</b>\n\n"
        f"<b>Commands</b>\n"
        f"/addtask Title | URL | Reward\n"
        f"/tasks — list tasks\n"
        f"/deltask ID — deactivate a task\n"
        f"/broadcast text — message all users (or reply to a message)\n"
        f"/ban ID — /unban ID\n"
        f"/backup — send the database file",
    )


@bot.message_handler(commands=["addtask"], chat_types=["private"])
@admin_only
@safe_handler
def cmd_addtask(message):
    usage = (
        "Usage:\n<code>/addtask Title | URL | Reward</code>\n\n"
        "Example:\n<code>/addtask Install App X | https://example.com/offer?uid={user_id} | 5</code>\n\n"
        "<code>{user_id}</code> in the URL is replaced with the user's Telegram ID."
    )
    args = message.text.partition(" ")[2].strip()
    parts = [p.strip() for p in args.split("|")]
    if len(parts) != 3 or not all(parts):
        bot.send_message(message.chat.id, usage)
        return

    title, url, reward_raw = parts
    if len(title) > 100:
        bot.send_message(message.chat.id, "❌ Title is too long (max 100 characters).")
        return
    if not re.match(r"^https?://\S+$", url):
        bot.send_message(message.chat.id, "❌ URL must start with http:// or https:// and contain no spaces.")
        return
    try:
        reward = round(float(reward_raw), 2)
        if reward <= 0:
            raise ValueError
    except ValueError:
        bot.send_message(message.chat.id, "❌ Reward must be a positive number.")
        return

    task_id = add_task(title, url, reward)
    bot.send_message(
        message.chat.id,
        f"✅ Task #{task_id} added: <b>{esc(title)}</b> — {money(reward)} {CURRENCY}",
    )


@bot.message_handler(commands=["tasks"], chat_types=["private"])
@admin_only
@safe_handler
def cmd_tasks(message):
    with db() as conn:
        rows = conn.execute("SELECT * FROM tasks ORDER BY task_id DESC LIMIT 30").fetchall()
    if not rows:
        bot.send_message(message.chat.id, "No tasks yet. Add one with /addtask.")
        return
    lines = [
        f"{'🟢' if r['active'] else '⚪️'} #{r['task_id']} — {esc(r['title'])} "
        f"({money(r['reward'])} {CURRENCY})"
        for r in rows
    ]
    bot.send_message(message.chat.id, "🎯 <b>Tasks</b>\n\n" + "\n".join(lines))


@bot.message_handler(commands=["deltask"], chat_types=["private"])
@admin_only
@safe_handler
def cmd_deltask(message):
    raw = message.text.partition(" ")[2].strip()
    if not raw.isdigit():
        bot.send_message(message.chat.id, "Usage: <code>/deltask TASK_ID</code>")
        return
    with db() as conn:
        cur = conn.execute("UPDATE tasks SET active=0 WHERE task_id=?", (int(raw),))
    if cur.rowcount:
        bot.send_message(message.chat.id, f"✅ Task #{raw} deactivated.")
    else:
        bot.send_message(message.chat.id, "❌ Task not found.")


@bot.message_handler(commands=["ban", "unban"], chat_types=["private"])
@admin_only
@safe_handler
def cmd_ban(message):
    command = message.text.split()[0].lstrip("/").split("@")[0].lower()
    raw = message.text.partition(" ")[2].strip()
    if not raw.isdigit():
        bot.send_message(message.chat.id, f"Usage: <code>/{command} USER_ID</code>")
        return
    target = int(raw)
    if target == ADMIN_ID:
        bot.send_message(message.chat.id, "❌ You cannot ban the admin.")
        return
    ok = set_ban(target, banned=(command == "ban"))
    if ok:
        past = "banned" if command == "ban" else "unbanned"
        bot.send_message(message.chat.id, f"✅ User <code>{target}</code> {past}.")
    else:
        bot.send_message(message.chat.id, "❌ User not found.")


@bot.message_handler(commands=["backup"], chat_types=["private"])
@admin_only
@safe_handler
def cmd_backup(message):
    with _db_lock:  # make sure no write is in progress while the file is read
        with open(DB_PATH, "rb") as f:
            data = f.read()
    bot.send_document(
        message.chat.id,
        (f"wpcash_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.db", data),
        caption="🗄 Database backup",
    )


def _send_with_retry(send_fn) -> None:
    """Call send_fn(); on Telegram flood-wait (429) sleep and retry once."""
    try:
        send_fn()
    except ApiTelegramException as exc:
        if exc.error_code == 429:
            retry_after = 5
            try:
                retry_after = int(exc.result_json.get("parameters", {}).get("retry_after", 5))
            except Exception:
                pass
            time.sleep(retry_after + 1)
            send_fn()
        else:
            raise


def _broadcast_worker(admin_chat_id: int, text: str, source_message) -> None:
    """Send the broadcast to every non-banned user (runs in its own thread)."""
    with db() as conn:
        ids = [r["user_id"] for r in conn.execute("SELECT user_id FROM users WHERE banned=0")]

    sent = failed = 0
    for uid in ids:
        try:
            if source_message is not None:
                _send_with_retry(
                    lambda u=uid: bot.copy_message(
                        u, source_message.chat.id, source_message.message_id
                    )
                )
            else:
                _send_with_retry(lambda u=uid: bot.send_message(u, text, parse_mode=None))
            sent += 1
        except Exception:
            failed += 1  # most commonly: the user blocked the bot
        time.sleep(0.05)  # stay well below Telegram's ~30 msgs/sec limit

    logger.info("Broadcast finished: sent=%s failed=%s", sent, failed)
    safe_send(admin_chat_id, f"📢 Broadcast finished.\n✅ Sent: {sent}\n❌ Failed: {failed}")


@bot.message_handler(commands=["broadcast"], chat_types=["private"])
@admin_only
@safe_handler
def cmd_broadcast(message):
    text = message.text.partition(" ")[2].strip()
    source = message.reply_to_message
    if not text and source is None:
        bot.send_message(
            message.chat.id,
            "Usage:\n<code>/broadcast your message</code>\nor reply to any message with /broadcast.",
        )
        return
    bot.send_message(message.chat.id, "📢 Broadcast started...")
    threading.Thread(
        target=_broadcast_worker, args=(message.chat.id, text, source), daemon=True
    ).start()


# ============================================================
# TEXT INPUT (withdraw number / amount) + FALLBACK
# ============================================================
@bot.message_handler(content_types=["text"], chat_types=["private"])
@safe_handler
def on_text(message):
    if not guard(message):
        return
    user_id = message.from_user.id
    state = get_state(user_id)
    text = (message.text or "").strip()

    if not state:
        bot.send_message(message.chat.id, "Please use the menu buttons below.", reply_markup=main_menu())
        return

    # ---- Step 1: payment number ----
    if state["type"] == "wd_number":
        match = BD_NUMBER_RE.match(text.replace(" ", "").replace("-", ""))
        if not match:
            bot.send_message(
                message.chat.id,
                "❌ Invalid number. Send an 11-digit number like 01XXXXXXXXX, or /cancel.",
            )
            return
        set_state(user_id, type="wd_amount", method=state["method"], number=match.group(1))
        user = get_user(user_id)
        bot.send_message(
            message.chat.id,
            f"💰 Enter the amount to withdraw (min {money(MIN_WITHDRAW)}, "
            f"max {money(user['balance'])} {CURRENCY}):",
        )
        return

    # ---- Step 2: amount ----
    if state["type"] == "wd_amount":
        try:
            amount = round(float(text.replace(",", "")), 2)
        except ValueError:
            bot.send_message(message.chat.id, "❌ Please send a valid number, or /cancel.")
            return

        user = get_user(user_id)
        if amount < MIN_WITHDRAW:
            bot.send_message(message.chat.id, f"❌ Minimum withdrawal is {money(MIN_WITHDRAW)} {CURRENCY}.")
            return
        if amount > user["balance"]:
            bot.send_message(
                message.chat.id,
                f"❌ Not enough balance. You have {money(user['balance'])} {CURRENCY}.",
            )
            return

        wd_id, error = create_withdrawal(user_id, state["method"], state["number"], amount)
        if error == "pending":
            clear_state(user_id)
            bot.send_message(message.chat.id, "⏳ You already have a pending withdrawal.")
            return
        if error:
            clear_state(user_id)
            bot.send_message(message.chat.id, "❌ Not enough balance.")
            return

        kb = types.InlineKeyboardMarkup()
        kb.row(
            types.InlineKeyboardButton("✅ Approve (Paid)", callback_data=f"wd:a:{wd_id}"),
            types.InlineKeyboardButton("❌ Reject", callback_data=f"wd:r:{wd_id}"),
        )
        admin_text = (
            f"💳 <b>Withdrawal Request</b> #{wd_id}\n"
            f"👤 <a href=\"tg://user?id={user_id}\">{esc(message.from_user.first_name)}</a> "
            f"(<code>{user_id}</code>)\n"
            f"📲 Method: <b>{esc(state['method'])}</b>\n"
            f"🔢 Number: <code>{esc(state['number'])}</code>\n"
            f"💰 Amount: <b>{money(amount)} {CURRENCY}</b>\n"
            f"🏦 Current balance: {money(user['balance'])} {CURRENCY}\n"
            f"⏰ {fmt_local(now_str(), True)}"
        )
        try:
            bot.send_message(ADMIN_ID, admin_text, reply_markup=kb)
        except Exception:
            logger.exception("Could not forward withdrawal %s to admin", wd_id)
            delete_withdrawal(wd_id)
            clear_state(user_id)
            bot.send_message(message.chat.id, "⚠️ Could not reach the admin. Please try again later.")
            return

        clear_state(user_id)
        bot.send_message(
            message.chat.id,
            f"✅ Withdrawal request for <b>{money(amount)} {CURRENCY}</b> sent to the admin.\n"
            f"Your balance is deducted when the payment is approved.",
            reply_markup=main_menu(),
        )
        return

    # Unknown state (e.g. waiting for a photo but user typed text)
    if state["type"] == "proof":
        bot.send_message(message.chat.id, "📸 Please send your screenshot as a photo, or /cancel.")
        return
    clear_state(user_id)


# ============================================================
# KEEP-ALIVE HTTP SERVER (Render needs an open port; UptimeRobot pings it)
# ============================================================
class HealthHandler(BaseHTTPRequestHandler):
    def _respond(self, include_body: bool) -> None:
        body = b"WP CashBot is running"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if include_body:
            self.wfile.write(body)

    def do_GET(self):
        self._respond(True)

    def do_HEAD(self):
        self._respond(False)

    def log_message(self, fmt, *args):  # keep the console clean
        return


def start_health_server() -> None:
    server = ThreadingHTTPServer(("0.0.0.0", PORT), HealthHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    logger.info("Health server listening on port %s", PORT)


# ============================================================
# ENTRY POINT
# ============================================================
def main() -> None:
    global BOT_USERNAME

    init_db()
    start_health_server()

    try:
        bot.remove_webhook()  # polling and webhooks cannot be used together
    except Exception:
        logger.warning("Could not remove webhook (continuing).")

    if not BOT_USERNAME:
        BOT_USERNAME = bot.get_me().username
        logger.info("BOT_USERNAME not set, detected @%s", BOT_USERNAME)

    logger.info("WP CashBot started as @%s (admin: %s)", BOT_USERNAME, ADMIN_ID)
    bot.infinity_polling(
        skip_pending=True,
        timeout=30,
        long_polling_timeout=30,
        allowed_updates=["message", "callback_query"],
    )


if __name__ == "__main__":
    main()

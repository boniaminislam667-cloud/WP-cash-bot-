import telebot
import sqlite3
import os

# ========== CONFIGURATION ==========
BOT_TOKEN = os.getenv("8845836404:AAGgpCxW7t23dYFMTRfEo5KVynWviJmVWyE")
ADMIN_ID = int(os.getenv("5211862194", 0))
BOT_USERNAME = "@WPCash_bot"  # without @

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN environment variable is required")

bot = telebot.TeleBot(BOT_TOKEN)
DB_NAME = "wpcash.db"

# ========== DATABASE SETUP ==========
def init_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users
                 (user_id INTEGER PRIMARY KEY,
                  balance REAL DEFAULT 0,
                  referrals INTEGER DEFAULT 0,
                  referred_by INTEGER)''')
    conn.commit()
    conn.close()

def get_user(user_id):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT balance, referrals FROM users WHERE user_id=?", (user_id,))
    row = c.fetchone()
    conn.close()
    return row

def add_user(user_id, referred_by=None):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO users (user_id, referred_by) VALUES (?, ?)", 
              (user_id, referred_by))
    if referred_by and c.rowcount > 0:
        c.execute("UPDATE users SET referrals = referrals + 1, balance = balance + 2 WHERE user_id=?", 
                  (referred_by,))
    conn.commit()
    conn.close()

# ========== MENUS ==========
def main_menu():
    markup = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    btn1 = telebot.types.KeyboardButton("📱 Earn Money")
    btn2 = telebot.types.KeyboardButton("👤 My Profile")
    btn3 = telebot.types.KeyboardButton("👫 Refer & Earn")
    btn4 = telebot.types.KeyboardButton("💳 Withdraw")
    markup.add(btn1, btn2, btn3, btn4)
    return markup

# ========== HANDLERS ==========
@bot.message_handler(commands=['start'])
def start_cmd(message):
    user_id = message.from_user.id
    args = message.text.split()
    
    referred_by = None
    if len(args) > 1 and args[1].isdigit():
        referred_by = int(args[1])
        if referred_by == user_id:
            referred_by = None
    
    add_user(user_id, referred_by)
    
    bot.send_message(
        user_id,
        f"Welcome to WP CashBot, {message.from_user.first_name}!\n"
        f"Use the menu below to get started.",
        reply_markup=main_menu()
    )

@bot.message_handler(func=lambda m: m.text == "👤 My Profile")
def profile(message):
    user_id = message.from_user.id
    data = get_user(user_id)
    if data:
        balance, referrals = data
        bot.reply_to(message, 
            f"👤 <b>My Profile</b>\n\n"
            f"🆔 User ID: <code>{user_id}</code>\n"
            f"💰 Balance: {balance:.2f} Taka\n"
            f"👥 Total Referrals: {referrals}",
            parse_mode="HTML")
    else:
        bot.reply_to(message, "Profile not found. Please /start first.")

@bot.message_handler(func=lambda m: m.text == "👫 Refer & Earn")
def refer(message):
    user_id = message.from_user.id
    link = f"https://t.me/{BOT_USERNAME}?start={user_id}"
    bot.reply_to(message, 
        f"Share this link and earn 2 Taka per referral:\n\n{link}",
        disable_web_page_preview=True)

@bot.message_handler(func=lambda m: m.text == "📱 Earn Money")
def earn(message):
    bot.reply_to(message, "Task submission functionality goes here.")

@bot.message_handler(func=lambda m: m.text == "💳 Withdraw")
def withdraw(message):
    data = get_user(message.from_user.id)
    if data and data[0] >= 50:
        bot.reply_to(message, "Withdrawal request functionality goes here.")
    else:
        bot.reply_to(message, f"Minimum withdrawal is 50 Taka. Your balance: {data[0] if data else 0:.2f}")

# ========== ENTRY ==========
if __name__ == "__main__":
    init_db()
    print("Bot is running...")
    bot.infinity_polling()

# WP CashBot 💰

Telegram earning bot — Python 3.11 · pyTelegramBotAPI · SQLite · Render (free tier)

**Language / ভাষা:** [English](#english) · [বাংলা](#বাংলা)

---

## English

### Features
- `/start` with referral deep link (`t.me/BOT?start=USER_ID`) — referrer gets **2 Taka**
- Menu: 📱 Earn Money · 👤 My Profile · 👫 Refer & Earn · 💳 Withdraw
- Offerwall tasks with **Open Task** + **Submit Proof** (screenshot goes to admin → Approve / Reject)
- Withdraw via bKash / Nagad (minimum **50 Taka**), admin approves → balance deducted, marked paid
- Anti-abuse: 1 submission / 10 min, duplicate screenshot detection, strikes + auto-ban (3 strikes)
- Admin: `/admin` `/addtask` `/tasks` `/deltask` `/broadcast` `/ban` `/unban` `/backup`

### 1. Create the bot
1. Open [@BotFather](https://t.me/BotFather) → `/newbot` → copy the **token**.
2. Get your numeric Telegram ID from [@userinfobot](https://t.me/userinfobot) → this is `ADMIN_ID`.
3. **Open your bot once and press Start** (the bot cannot message you until you do).

### 2. Push to GitHub
```bash
git init
git add .
git commit -m "WP CashBot"
git branch -M main
git remote add origin https://github.com/YOUR_USER/WP-cash-bot.git
git push -u origin main
```
> ⚠️ **Never put your token in the code or commit it.** Tokens go only in Render's environment variables.

### 3. Deploy on Render
1. [render.com](https://render.com) → **New → Blueprint** → select your GitHub repo (it reads `render.yaml`).
2. Fill in the environment variables:

| Variable | Value |
|---|---|
| `BOT_TOKEN` | token from BotFather |
| `ADMIN_ID` | your numeric Telegram ID |
| `BOT_USERNAME` | e.g. `WPCash_bot` (with or without `@`) |

3. Deploy. Logs should show `WP CashBot started as @...`.

### 4. Keep it awake with UptimeRobot
Render's free web service sleeps after ~15 minutes without web traffic, which would stop the bot.
1. Create a free account at [uptimerobot.com](https://uptimerobot.com).
2. **Add New Monitor** → type **HTTP(s)** → URL = your Render URL (`https://wp-cashbot.onrender.com`).
3. Interval: **5 minutes**.

### 5. Add tasks (admin)
```
/addtask Title | URL | Reward
/addtask Complete a survey | https://your-offerwall-link?user_id={user_id} | 5
```
- `{user_id}` is replaced by the user's Telegram ID, so you can match completions in your network dashboard.
- Use only networks that **allow incentivized traffic**, e.g. **CPX Research, Timewall, BitLabs, AdGem**. Read each network's rules first. Do **not** use CPALead or any network that bans incentive traffic.
- Keep the reward you pay users **below** what the network pays you, and check the network dashboard before approving a proof.

### Admin commands
| Command | What it does |
|---|---|
| `/admin` | Stats: users, total paid, pending submissions/withdrawals |
| `/addtask Title \| URL \| Reward` | Add a task |
| `/tasks` / `/deltask ID` | List / deactivate tasks |
| `/broadcast text` | Message all users (or reply to a message with `/broadcast`) |
| `/ban ID` / `/unban ID` | Manual ban / unban |
| `/backup` | Sends you the SQLite database file |

### ⚠️ Important: SQLite on Render free tier
The free tier has **no persistent disk**. The database file is **erased on every redeploy and restart**.
- Run `/backup` regularly to download the database.
- For permanent storage, use a paid Render plan with a Disk (mount it e.g. at `/data` and set `DB_PATH=/data/wpcash.db`) or move to an external database.

### Run locally
```bash
pip install -r requirements.txt
export BOT_TOKEN="..." ADMIN_ID="123456789" BOT_USERNAME="YourBot"
python main.py
```

---

## বাংলা

### ফিচার
- `/start` + রেফারেল লিংক (`t.me/BOT?start=USER_ID`) — রেফারার পাবে **২ টাকা**
- মেনু: 📱 Earn Money · 👤 My Profile · 👫 Refer & Earn · 💳 Withdraw
- অফারওয়াল টাস্ক: **Open Task** ও **Submit Proof** (স্ক্রিনশট অ্যাডমিনের কাছে যায় → Approve / Reject)
- bKash / Nagad এ উইথড্র (সর্বনিম্ন **৫০ টাকা**), অ্যাডমিন Approve করলে ব্যালেন্স কাটা হয় ও Paid মার্ক হয়
- অ্যান্টি-অ্যাবিউজ: ১০ মিনিটে ১টি সাবমিশন, ডুপ্লিকেট স্ক্রিনশট ডিটেকশন, স্ট্রাইক + অটো-ব্যান (৩ স্ট্রাইক)
- অ্যাডমিন কমান্ড: `/admin` `/addtask` `/tasks` `/deltask` `/broadcast` `/ban` `/unban` `/backup`

### ১. বট তৈরি করুন
1. [@BotFather](https://t.me/BotFather) এ গিয়ে `/newbot` দিন এবং **টোকেন** কপি করুন।
2. [@userinfobot](https://t.me/userinfobot) থেকে আপনার Telegram ID নিন — এটাই `ADMIN_ID`।
3. **নিজের বট একবার ওপেন করে Start চাপুন** (না চাপলে বট আপনাকে মেসেজ পাঠাতে পারবে না)।

### ২. GitHub এ পুশ করুন
```bash
git init
git add .
git commit -m "WP CashBot"
git branch -M main
git remote add origin https://github.com/YOUR_USER/WP-cash-bot.git
git push -u origin main
```
> ⚠️ **কখনোই টোকেন কোডে লিখবেন না বা GitHub এ পুশ করবেন না।** টোকেন শুধু Render এর Environment Variables এ দিন।

### ৩. Render এ ডিপ্লয় করুন
1. [render.com](https://render.com) → **New → Blueprint** → আপনার GitHub রিপো সিলেক্ট করুন (`render.yaml` অটো পড়বে)।
2. Environment Variables দিন:

| Variable | মান |
|---|---|
| `BOT_TOKEN` | BotFather এর টোকেন |
| `ADMIN_ID` | আপনার Telegram ID (সংখ্যা) |
| `BOT_USERNAME` | যেমন `WPCash_bot` (`@` থাকলেও সমস্যা নেই) |

3. Deploy দিন। লগে `WP CashBot started as @...` দেখা গেলে বট চালু।

### ৪. UptimeRobot দিয়ে বট জাগিয়ে রাখুন
Render ফ্রি প্ল্যানে ~১৫ মিনিট কোনো ওয়েব রিকোয়েস্ট না এলে সার্ভিস ঘুমিয়ে যায়, ফলে বটও বন্ধ হয়ে যায়।
1. [uptimerobot.com](https://uptimerobot.com) এ ফ্রি অ্যাকাউন্ট খুলুন।
2. **Add New Monitor** → টাইপ **HTTP(s)** → URL = আপনার Render URL (`https://wp-cashbot.onrender.com`)।
3. Interval: **৫ মিনিট**।

### ৫. টাস্ক যোগ করুন (অ্যাডমিন)
```
/addtask Title | URL | Reward
/addtask Complete a survey | https://your-offerwall-link?user_id={user_id} | 5
```
- URL এ `{user_id}` লিখলে সেটা ইউজারের Telegram ID দিয়ে বদলে যাবে, ফলে নেটওয়ার্ক ড্যাশবোর্ডে মিলিয়ে দেখতে পারবেন।
- শুধু এমন নেটওয়ার্ক ব্যবহার করুন যারা **ইনসেনটিভ ট্রাফিক অনুমোদন করে**, যেমন **CPX Research, Timewall, BitLabs, AdGem**। প্রতিটি নেটওয়ার্কের নিয়ম আগে পড়ে নিন। **CPALead** বা ইনসেনটিভ ট্রাফিক নিষিদ্ধ করে এমন কোনো নেটওয়ার্ক ব্যবহার করবেন না।
- ইউজারকে যে রিওয়ার্ড দেবেন তা নেটওয়ার্ক আপনাকে যা দেয় তার **চেয়ে কম** রাখুন, এবং Approve করার আগে নেটওয়ার্ক ড্যাশবোর্ড চেক করুন।

### অ্যাডমিন কমান্ড
| কমান্ড | কাজ |
|---|---|
| `/admin` | স্ট্যাটস: ইউজার, মোট পেইড, পেন্ডিং সাবমিশন/উইথড্র |
| `/addtask Title \| URL \| Reward` | নতুন টাস্ক যোগ |
| `/tasks` / `/deltask ID` | টাস্ক লিস্ট / বন্ধ করা |
| `/broadcast text` | সব ইউজারকে মেসেজ (অথবা কোনো মেসেজে রিপ্লাই করে `/broadcast`) |
| `/ban ID` / `/unban ID` | ম্যানুয়াল ব্যান / আনব্যান |
| `/backup` | SQLite ডাটাবেস ফাইল আপনাকে পাঠায় |

### ⚠️ গুরুত্বপূর্ণ: Render ফ্রি প্ল্যানে SQLite
ফ্রি প্ল্যানে **স্থায়ী ডিস্ক নেই**। প্রতিবার রিডিপ্লয় বা রিস্টার্টে **ডাটাবেস ফাইল মুছে যায়**।
- নিয়মিত `/backup` দিয়ে ডাটাবেস ডাউনলোড করে রাখুন।
- স্থায়ী ডাটার জন্য Render এর পেইড প্ল্যানে Disk নিন (যেমন `/data` এ মাউন্ট করে `DB_PATH=/data/wpcash.db` সেট করুন) অথবা এক্সটার্নাল ডাটাবেস ব্যবহার করুন।

### লোকালি চালানো
```bash
pip install -r requirements.txt
export BOT_TOKEN="..." ADMIN_ID="123456789" BOT_USERNAME="YourBot"
python main.py
```

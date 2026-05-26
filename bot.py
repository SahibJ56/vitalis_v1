"""
Vitalis Health Agent — Telegram Bot
------------------------------------
Voice note or text → food logging → nutrition analysis → deficiency detection
→ grocery list → doctor appointment on Google Calendar (autonomous)

Modified to use:
- Gemini 1.5 Flash (free) instead of Anthropic Claude
- Groq Whisper (free) instead of OpenAI Whisper
"""

import os
import json
import sqlite3
import tempfile
from datetime import datetime, timedelta
from telegram import Update
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    filters, ContextTypes
)
from google import genai
from groq import Groq

# ── Clients ───────────────────────────────────────────────────────────────────
gemini_client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
groq_client = Groq(api_key=os.environ["GROQ_API_KEY"])

DB_PATH = "vitalis.db"

# ── Database ──────────────────────────────────────────────────────────────────
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id   TEXT PRIMARY KEY,
            username  TEXT,
            name      TEXT,
            goals     TEXT DEFAULT 'eat healthy and stay balanced',
            family_group TEXT
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS food_logs (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     TEXT,
            username    TEXT,
            food_raw    TEXT,
            food_clean  TEXT,
            calories    REAL DEFAULT 0,
            protein     REAL DEFAULT 0,
            carbs       REAL DEFAULT 0,
            fat         REAL DEFAULT 0,
            iron        REAL DEFAULT 0,
            vitamin_d   REAL DEFAULT 0,
            vitamin_b12 REAL DEFAULT 0,
            calcium     REAL DEFAULT 0,
            health_score INTEGER DEFAULT 5,
            logged_at   DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS appointments (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     TEXT,
            reason      TEXT,
            event_id    TEXT,
            scheduled   DATETIME,
            created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()


def save_user(user_id, username, name):
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT OR IGNORE INTO users (user_id, username, name) VALUES (?,?,?)",
        (str(user_id), username or "", name or "")
    )
    conn.commit()
    conn.close()


def get_user(user_id):
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute(
        "SELECT * FROM users WHERE user_id=?", (str(user_id),)
    ).fetchone()
    conn.close()
    return row


def save_food(user_id, username, food_raw, nutrition: dict):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        INSERT INTO food_logs
            (user_id, username, food_raw, food_clean,
             calories, protein, carbs, fat,
             iron, vitamin_d, vitamin_b12, calcium, health_score)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        str(user_id), username, food_raw,
        nutrition.get("food_name", food_raw),
        nutrition.get("calories", 0),
        nutrition.get("protein", 0),
        nutrition.get("carbs", 0),
        nutrition.get("fat", 0),
        nutrition.get("iron", 0),
        nutrition.get("vitamin_d", 0),
        nutrition.get("vitamin_b12", 0),
        nutrition.get("calcium", 0),
        nutrition.get("health_score", 5),
    ))
    conn.commit()
    conn.close()


def get_history(user_id, days=7):
    conn = sqlite3.connect(DB_PATH)
    since = (datetime.now() - timedelta(days=days)).isoformat()
    rows = conn.execute("""
        SELECT food_clean, calories, protein, carbs, fat,
               iron, vitamin_d, vitamin_b12, calcium, health_score, logged_at
        FROM food_logs
        WHERE user_id=? AND logged_at>?
        ORDER BY logged_at DESC
    """, (str(user_id), since)).fetchall()
    conn.close()
    return rows


def history_totals(rows):
    keys = ["calories","protein","carbs","fat","iron","vitamin_d","vitamin_b12","calcium"]
    totals = {k: 0.0 for k in keys}
    for r in rows:
        totals["calories"]   += r[1] or 0
        totals["protein"]    += r[2] or 0
        totals["carbs"]      += r[3] or 0
        totals["fat"]        += r[4] or 0
        totals["iron"]       += r[5] or 0
        totals["vitamin_d"]  += r[6] or 0
        totals["vitamin_b12"]+= r[7] or 0
        totals["calcium"]    += r[8] or 0
    return totals


# ── Voice transcription (Groq Whisper — free) ─────────────────────────────────
async def transcribe(file_path: str) -> str:
    with open(file_path, "rb") as f:
        result = groq_client.audio.transcriptions.create(
            model="whisper-large-v3",
            file=f
        )
    return result.text


# ── Gemini food analysis ───────────────────────────────────────────────────────
ANALYZE_PROMPT = """You are a clinical nutrition AI. Analyse the food described and return ONLY valid JSON — no markdown, no explanation, no backticks.

Food logged: {food}
User goals: {goals}
7-day nutritional history (totals): {history}

Weekly deficiency thresholds (flag if below):
- Iron: 90mg/week (women), 56mg/week (men)
- Vitamin D: 4200 IU/week
- Vitamin B12: 16.8 mcg/week
- Calcium: 7000 mg/week
- Protein: 350g/week

Return exactly this JSON and nothing else:
{{
  "food_name": "cleaned readable food name",
  "calories": 0,
  "protein": 0,
  "carbs": 0,
  "fat": 0,
  "iron": 0,
  "vitamin_d": 0,
  "vitamin_b12": 0,
  "calcium": 0,
  "health_score": 7,
  "insight": "one personalised sentence about this meal",
  "deficiencies": ["nutrient names that are critically low based on history"],
  "grocery_list": ["5 specific foods to buy to fix deficiencies"],
  "needs_doctor": false,
  "doctor_reason": ""
}}

needs_doctor should be true ONLY if there is a serious sustained pattern (e.g. vitamin D critically low for 7 days, iron deficiency anaemia pattern, extreme calorie restriction).
"""

async def analyse_food(food_text: str, goals: str, history_totals_data: dict) -> dict:
    prompt = ANALYZE_PROMPT.format(
        food=food_text,
        goals=goals,
        history=json.dumps(history_totals_data)
    )
    response = gemini_client.models.generate_content(
        model="gemini-2.5-flash-lite",
        contents=prompt
    )
    raw = response.text.strip()
    raw = raw.replace("```json", "").replace("```", "").strip()
    return json.loads(raw)


# ── Grocery list generation ────────────────────────────────────────────────────
async def build_grocery_list(user_id: str, goals: str) -> str:
    rows = get_history(user_id, days=7)
    if not rows:
        return "No meals logged yet — send me a voice note or type what you ate first!"

    totals = history_totals(rows)
    response = gemini_client.models.generate_content(
        model="gemini-2.5-flash-lite",
        contents=f"""
Create a practical weekly grocery list based on these 7-day nutritional totals:
{json.dumps(totals)}

User goals: {goals}

Weekly targets: iron 126mg, vitamin_d 4200IU, vitamin_b12 16.8mcg, calcium 7000mg, protein 350g

Return ONLY a grocery list with emojis. Each item on its own line. Include the reason in brackets.
Example: 🐟 Salmon — 2 fillets (vitamin D + B12)
"""
    )
    return response.text.strip()


# ── Doctor appointment ────────────────────────────────────────────────────────
async def schedule_appointment(user_id: str, reason: str) -> str:
    appt_time = (datetime.now() + timedelta(days=3)).replace(
        hour=10, minute=0, second=0, microsecond=0
    )

    # Try Google Calendar if credentials are set
    creds_json = os.environ.get("GOOGLE_CREDENTIALS_JSON")
    event_id = None

    if creds_json:
        try:
            from google.oauth2.credentials import Credentials
            from googleapiclient.discovery import build as gbuild

            creds   = Credentials.from_authorized_user_info(json.loads(creds_json))
            service = gbuild("calendar", "v3", credentials=creds)

            event = service.events().insert(
                calendarId="primary",
                body={
                    "summary": "Doctor Appointment — Vitalis Alert",
                    "description": f"Scheduled automatically by Vitalis.\nReason: {reason}",
                    "start": {
                        "dateTime": appt_time.isoformat(),
                        "timeZone": "America/Toronto"
                    },
                    "end": {
                        "dateTime": (appt_time + timedelta(hours=1)).isoformat(),
                        "timeZone": "America/Toronto"
                    },
                    "reminders": {
                        "useDefault": False,
                        "overrides": [
                            {"method": "popup", "minutes": 30},
                            {"method": "email",  "minutes": 60},
                        ]
                    }
                }
            ).execute()
            event_id = event.get("id")
        except Exception as e:
            print(f"Google Calendar error: {e}")

    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT INTO appointments (user_id, reason, event_id, scheduled) VALUES (?,?,?,?)",
        (str(user_id), reason, event_id, appt_time.isoformat())
    )
    conn.commit()
    conn.close()

    formatted = appt_time.strftime("%A, %B %d at %I:%M %p")
    if event_id:
        return (f"📅 *Doctor appointment added to your Google Calendar*\n"
                f"📆 {formatted}\n"
                f"🔔 You'll get a reminder 30 minutes before.")
    else:
        return (f"📅 *Vitalis recommends a doctor visit*\n"
                f"📆 Suggested: {formatted}\n"
                f"Please book with your GP. Reason: {reason}")


# ── Telegram handlers ─────────────────────────────────────────────────────────
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    save_user(u.id, u.username, u.first_name)
    await update.message.reply_text(
        f"👋 Hey *{u.first_name}*! I'm *Vitalis*, your personal health AI.\n\n"
        f"Just send me a 🎙️ *voice note* or 💬 *text* telling me what you ate — I'll log it, analyse it, and tell you what your body needs.\n\n"
        f"*What I do automatically:*\n"
        f"• Log meals from voice or text\n"
        f"• Track nutrition across the week\n"
        f"• Detect deficiencies and suggest groceries\n"
        f"• Schedule a doctor appointment if something looks serious\n\n"
        f"*Commands:*\n"
        f"/grocery — your weekly grocery list\n"
        f"/summary — your 7-day nutrition report\n"
        f"/setgoals — update your health goals\n"
        f"/help — show this menu",
        parse_mode="Markdown"
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "*Vitalis — Help*\n\n"
        "🎙️ Send a voice note — log a meal\n"
        "💬 Send a text — log a meal\n"
        "/grocery — personalised grocery list\n"
        "/summary — 7-day nutrition summary\n"
        "/setgoals [your goals] — update your health goals\n"
        "/help — this menu\n\n"
        "_Vitalis automatically detects deficiencies and schedules "
        "doctor appointments when patterns look serious._",
        parse_mode="Markdown"
    )


async def cmd_grocery(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u    = update.effective_user
    user = get_user(u.id)
    goals = user[3] if user and user[3] else "eat healthy"
    await update.message.reply_text("🛒 Building your personalised grocery list...")
    lst = await build_grocery_list(str(u.id), goals)
    await update.message.reply_text(f"*Your Weekly Grocery List*\n\n{lst}", parse_mode="Markdown")


async def cmd_summary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u    = update.effective_user
    rows = get_history(str(u.id), days=7)

    if not rows:
        await update.message.reply_text("No meals logged yet. Send me a voice note or type what you ate!")
        return

    t = history_totals(rows)

    def pct(actual, target):
        p = (actual / target * 100) if target else 0
        return f"{'🟢' if p >= 80 else '🟡' if p >= 50 else '🔴'} {p:.0f}%"

    msg = (
        f"📈 *Your 7-Day Summary*\n"
        f"Meals logged: {len(rows)}\n\n"
        f"*Macros*\n"
        f"Calories:    {t['calories']:.0f} kcal\n"
        f"Protein:     {t['protein']:.1f}g   {pct(t['protein'], 350)}\n"
        f"Carbs:       {t['carbs']:.1f}g\n"
        f"Fat:         {t['fat']:.1f}g\n\n"
        f"*Micronutrients*\n"
        f"Iron:        {t['iron']:.1f}mg   {pct(t['iron'], 126)}\n"
        f"Vitamin D:   {t['vitamin_d']:.0f}IU {pct(t['vitamin_d'], 4200)}\n"
        f"Vitamin B12: {t['vitamin_b12']:.1f}mcg {pct(t['vitamin_b12'], 16.8)}\n"
        f"Calcium:     {t['calcium']:.0f}mg  {pct(t['calcium'], 7000)}\n\n"
        f"_🔴 = below 50% · 🟡 = 50–80% · 🟢 = on track_"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")


async def cmd_setgoals(update: Update, context: ContextTypes.DEFAULT_TYPE):
    goals = " ".join(context.args)
    if not goals:
        await update.message.reply_text(
            "Tell me your goals after the command. Example:\n"
            "/setgoals high protein, improve energy, reduce sugar"
        )
        return
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "UPDATE users SET goals=? WHERE user_id=?",
        (goals, str(update.effective_user.id))
    )
    conn.commit()
    conn.close()
    await update.message.reply_text(f"✅ Goals updated: _{goals}_", parse_mode="Markdown")


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    save_user(u.id, u.username, u.first_name)
    await update.message.reply_text("🎙️ Got your voice note — transcribing...")

    voice = update.message.voice
    file  = await context.bot.get_file(voice.file_id)

    with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
        await file.download_to_drive(tmp.name)
        food_text = await transcribe(tmp.name)
        os.unlink(tmp.name)

    await update.message.reply_text(
        f"✅ Heard: _{food_text}_\n\nAnalysing your meal...",
        parse_mode="Markdown"
    )
    await process_meal(update, context, food_text, u)


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u    = update.effective_user
    text = update.message.text.strip()
    if not text or text.startswith("/"):
        return
    save_user(u.id, u.username, u.first_name)
    await update.message.reply_text("🔍 Analysing your meal...")
    await process_meal(update, context, text, u)


async def process_meal(update: Update, context: ContextTypes.DEFAULT_TYPE, food_text: str, user):
    user_id = str(user.id)
    user_row = get_user(user_id)
    goals    = user_row[3] if user_row and user_row[3] else "eat healthy"
    rows     = get_history(user_id, days=7)
    totals   = history_totals(rows)

    try:
        nutrition = await analyse_food(food_text, goals, totals)
    except Exception as e:
        await update.message.reply_text(f"❌ Couldn't analyse that. Try again.\nError: {e}")
        return

    save_food(user_id, user.username or user.first_name, food_text, nutrition)

    score = nutrition.get("health_score", 5)
    emoji = "🟢" if score >= 7 else "🟡" if score >= 4 else "🔴"

    msg = (
        f"*{nutrition.get('food_name', food_text)}*\n"
        f"{emoji} Health Score: *{score}/10*\n\n"
        f"📊 *Nutrition*\n"
        f"• Calories:   {nutrition.get('calories',0):.0f} kcal\n"
        f"• Protein:    {nutrition.get('protein',0):.1f}g\n"
        f"• Carbs:      {nutrition.get('carbs',0):.1f}g\n"
        f"• Fat:        {nutrition.get('fat',0):.1f}g\n"
        f"• Iron:       {nutrition.get('iron',0):.1f}mg\n"
        f"• Vitamin D:  {nutrition.get('vitamin_d',0):.0f}IU\n\n"
        f"💡 _{nutrition.get('insight','')}_"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")

    # ── Deficiency warning + grocery nudge ─────────────────────────────────
    deficiencies = nutrition.get("deficiencies", [])
    if deficiencies:
        grocery = nutrition.get("grocery_list", [])
        grocery_text = "\n".join(f"• {item}" for item in grocery[:5])
        await update.message.reply_text(
            f"⚠️ *Deficiencies detected:* {', '.join(deficiencies)}\n\n"
            f"🛒 *Pick these up:*\n{grocery_text}\n\n"
            f"Type /grocery for your full weekly list.",
            parse_mode="Markdown"
        )

    # ── Autonomous doctor appointment ───────────────────────────────────────
    if nutrition.get("needs_doctor"):
        reason = nutrition.get("doctor_reason", "Concerning nutritional pattern detected")
        await update.message.reply_text(
            f"🚨 *Vitalis Health Alert*\n\n"
            f"Based on your recent food patterns, Vitalis thinks you should see a doctor.\n"
            f"_{reason}_\n\n"
            f"Scheduling an appointment for you now...",
            parse_mode="Markdown"
        )
        appt_msg = await schedule_appointment(user_id, reason)
        await update.message.reply_text(appt_msg, parse_mode="Markdown")


# ── Entry point ───────────────────────────────────────────────────────────────
def main():
    init_db()

    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise EnvironmentError("TELEGRAM_BOT_TOKEN is not set in your environment.")

    app = Application.builder().token(token).build()

    app.add_handler(CommandHandler("start",    cmd_start))
    app.add_handler(CommandHandler("help",     cmd_help))
    app.add_handler(CommandHandler("grocery",  cmd_grocery))
    app.add_handler(CommandHandler("summary",  cmd_summary))
    app.add_handler(CommandHandler("setgoals", cmd_setgoals))
    app.add_handler(MessageHandler(filters.VOICE,                  handle_voice))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    print("✅ Vitalis is running. Send it a message on Telegram.")
    app.run_polling()


if __name__ == "__main__":
    main()


import os
import re
import sqlite3
import logging
import threading
from datetime import datetime, timezone
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from telegram import Update, ReplyKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)

BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
DB_NAME = os.environ.get("DB_NAME", "copyright_bot.db")
OWNER = "@g0dfvd"
PORT = int(os.environ.get("PORT", "10000"))

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)

TARGET, COUNTRY, ORIGINAL_LINK, REPORTED_LINK = range(4)

COUNTRIES = [
    "Afghanistan", "Albania", "Algeria", "Andorra",
    "Angola", "Antigua and Barbuda", "Argentina",
    "Armenia", "Australia", "Austria", "Azerbaijan",
    "Bahamas", "Bahrain", "Bangladesh", "Barbados",
    "Belarus", "Belgium", "Belize", "Benin", "Bhutan",
    "Bolivia", "Bosnia and Herzegovina", "Botswana",
    "Brazil", "Brunei", "Bulgaria", "Burkina Faso",
    "Burundi", "Cabo Verde", "Cambodia", "Cameroon",
    "Canada", "Central African Republic", "Chad",
    "Chile", "China", "Colombia", "Comoros",
    "Costa Rica", "Croatia", "Cuba", "Cyprus",
    "Czechia", "Democratic Republic of the Congo",
    "Denmark", "Djibouti", "Dominica",
    "Dominican Republic", "Ecuador", "Egypt",
    "El Salvador", "Equatorial Guinea", "Eritrea",
    "Estonia", "Eswatini", "Ethiopia", "Fiji",
    "Finland", "France", "Gabon", "Gambia", "Georgia",
    "Germany", "Ghana", "Greece", "Grenada",
    "Guatemala", "Guinea", "Guinea-Bissau", "Guyana",
    "Haiti", "Honduras", "Hungary", "Iceland", "India",
    "Indonesia", "Iran", "Iraq", "Ireland", "Israel",
    "Italy", "Jamaica", "Japan", "Jordan", "Kazakhstan",
    "Kenya", "Kiribati", "Kuwait", "Kyrgyzstan", "Laos",
    "Latvia", "Lebanon", "Lesotho", "Liberia", "Libya",
    "Liechtenstein", "Lithuania", "Luxembourg",
    "Madagascar", "Malawi", "Malaysia", "Maldives",
    "Mali", "Malta", "Marshall Islands", "Mauritania",
    "Mauritius", "Mexico", "Micronesia", "Moldova",
    "Monaco", "Mongolia", "Montenegro", "Morocco",
    "Mozambique", "Myanmar", "Namibia", "Nauru", "Nepal",
    "Netherlands", "New Zealand", "Nicaragua", "Niger",
    "Nigeria", "North Korea", "North Macedonia", "Norway",
    "Oman", "Pakistan", "Palau", "Palestine", "Panama",
    "Papua New Guinea", "Paraguay", "Peru", "Philippines",
    "Poland", "Portugal", "Qatar", "Romania", "Russia",
    "Rwanda", "Saint Kitts and Nevis", "Saint Lucia",
    "Saint Vincent and the Grenadines", "Samoa",
    "San Marino", "Sao Tome and Principe", "Saudi Arabia",
    "Senegal", "Serbia", "Seychelles", "Sierra Leone",
    "Singapore", "Slovakia", "Slovenia", "Solomon Islands",
    "Somalia", "South Africa", "South Korea", "South Sudan",
    "Spain", "Sri Lanka", "Sudan", "Suriname", "Sweden",
    "Switzerland", "Syria", "Taiwan", "Tajikistan",
    "Tanzania", "Thailand", "Timor-Leste", "Togo", "Tonga",
    "Trinidad and Tobago", "Tunisia", "Turkey", "Turkmenistan",
    "Tuvalu", "Uganda", "Ukraine", "United Arab Emirates",
    "United Kingdom", "United States", "Uruguay", "Uzbekistan",
    "Vanuatu", "Vatican City", "Venezuela", "Vietnam",
    "Yemen", "Zambia", "Zimbabwe", "Kosovo", "Hong Kong",
    "Macau", "Puerto Rico", "Greenland", "Faroe Islands",
    "Bermuda", "Cayman Islands", "Guam", "American Samoa",
    "U.S. Virgin Islands", "British Virgin Islands", "Aruba",
    "Curacao", "Gibraltar", "Isle of Man", "Jersey",
    "Guernsey", "New Caledonia", "French Polynesia", "Reunion",
    "Martinique", "Guadeloupe", "Mayotte", "French Guiana",
    "Western Sahara", "Cook Islands", "Niue", "Tokelau",
    "Northern Mariana Islands", "Other / Not listed",
]


# ---------------- DATABASE ----------------

def db():
    conn = sqlite3.connect(DB_NAME, timeout=30)
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


def init_db():
    with db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS submissions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                target_handle TEXT NOT NULL,
                country TEXT NOT NULL,
                original_link TEXT NOT NULL,
                reported_link TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL,
                reporter_name TEXT NOT NULL DEFAULT 'Anonymous'
            )
        """)

        columns = {
            row[1] for row in
            conn.execute("PRAGMA table_info(submissions)").fetchall()
        }

        if "reporter_name" not in columns:
            conn.execute("""
                ALTER TABLE submissions
                ADD COLUMN reporter_name TEXT NOT NULL DEFAULT 'Anonymous'
            """)


def add_submission(user_id, reporter_name, target, country, original, reported):
    with db() as conn:
        cur = conn.execute("""
            INSERT INTO submissions (
                user_id, target_handle, country, original_link,
                reported_link, status, created_at, reporter_name
            )
            VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)
        """, (
            user_id,
            target,
            country,
            original,
            reported,
            datetime.now(timezone.utc).isoformat(timespec="seconds"),
            reporter_name,
        ))
        return cur.lastrowid


def set_status(user_id, submission_id, status):
    with db() as conn:
        cur = conn.execute("""
            UPDATE submissions
            SET status = ?
            WHERE id = ? AND user_id = ?
        """, (status, submission_id, user_id))
        return cur.rowcount > 0


def get_stats(user_id):
    result = {"pending": 0, "success": 0, "failed": 0}
    with db() as conn:
        rows = conn.execute("""
            SELECT status, COUNT(*)
            FROM submissions
            WHERE user_id = ?
            GROUP BY status
        """, (user_id,)).fetchall()

    for status, count in rows:
        if status in result:
            result[status] = count
    return result


def get_global_stats():
    result = {"pending": 0, "success": 0, "failed": 0}

    with db() as conn:
        rows = conn.execute("""
            SELECT status, COUNT(*)
            FROM submissions
            GROUP BY status
        """).fetchall()

    for status, count in rows:
        if status in result:
            result[status] = count
    return result


def get_country_stats():
    with db() as conn:
        return conn.execute("""
            SELECT country,
                   COUNT(*) AS total,
                   SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END),
                   SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END),
                   SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END)
            FROM submissions
            GROUP BY country
            ORDER BY total DESC, country ASC
        """).fetchall()


def get_public_recent(limit=10):
    with db() as conn:
        return conn.execute("""
            SELECT id, reporter_name, country, status, created_at
            FROM submissions
            ORDER BY id DESC
            LIMIT ?
        """, (limit,)).fetchall()


def get_history(user_id, limit=10):
    with db() as conn:
        return conn.execute("""
            SELECT id, target_handle, country, status, created_at
            FROM submissions
            WHERE user_id = ?
            ORDER BY id DESC
            LIMIT ?
        """, (user_id, limit)).fetchall()


# ---------------- HELPERS ----------------

def valid_url(text):
    return bool(re.match(r"^https?://\S+$", text.strip(), re.I))


def normalize_target(text):
    text = text.strip()
    if text.startswith(("https://", "http://", "@")):
        return text
    return "@" + text


def country_matches(text):
    query = text.strip().casefold()

    for country in COUNTRIES:
        if country.casefold() == query:
            return country

    matches = [
        country for country in COUNTRIES
        if query and query in country.casefold()
    ]

    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        return matches[:10]
    return None


def main_keyboard():
    return ReplyKeyboardMarkup(
        [
            ["📝 Create report"],
            ["🌍 Countries", "📊 My statistics"],
            ["🌐 Global Status", "📂 My history"],
            ["❌ Cancel"],
        ],
        resize_keyboard=True,
    )


def report_text(target, country, original, reported):
    return (
        "COPYRIGHT REPORT DRAFT\n"
        "======================\n\n"
        f"Reported account/content: {target}\n"
        f"Selected country/territory: {country}\n\n"
        "ORIGINAL WORK:\n"
        f"{original}\n\n"
        "REPORTED CONTENT:\n"
        f"{reported}\n\n"
        "DESCRIPTION:\n"
        f"I am submitting this report in relation to content "
        f"that I believe uses my original copyrighted work "
        f"without my permission. I believe this use may violate "
        f"copyright protections applicable in {country} and "
        "other applicable laws, depending on the facts and "
        "the applicable legal requirements. I am the copyright "
        "owner or authorized to act for the copyright owner. "
        "Please review the original work, the reported content, "
        "and the evidence provided. I can supply further "
        "information if required.\n\n"
        "IMPORTANT:\n"
        "Use this draft only if every statement is true and "
        "you are authorized to submit the report. Edit it if "
        "necessary. Do not make false or misleading claims. "
        "The selected country does not establish legal "
        "jurisdiction or reporting eligibility. This bot does "
        "not submit reports to Meta and cannot guarantee removal."
    )


def calculate_rate(stats):
    decided = stats["success"] + stats["failed"]
    if decided == 0:
        return "N/A (no decided feedback yet)"
    return f"{stats['success'] / decided * 100:.1f}%"


# ---------------- COMMANDS ----------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    message = (
        "👋 <b>Welcome to Copyright Report Assistant</b>\n\n"
        f"Owner: {escape(OWNER)}\n\n"
        "Prepare copyright report drafts, save your report "
        "details, and record your own feedback.\n\n"
        "🌐 Global Status shows aggregate statistics and recent "
        "public activity. Your Telegram username may appear "
        "there with your selected country and report status.\n\n"
        "⚠️ This bot does not submit reports to Meta. "
        "Reported outcomes are user feedback, not verified Meta results.\n\n"
        "Choose an option from the menu."
    )
    await update.message.reply_text(
        message,
        parse_mode=ParseMode.HTML,
        reply_markup=main_keyboard(),
    )


async def countries_command(update, context):
    message = "🌍 Country/territory list:\n\n" + "\n".join(
        f"{i + 1}. {name}" for i, name in enumerate(COUNTRIES)
    )
    # Split long lists to stay within Telegram message limits.
    for start_index in range(0, len(message), 3500):
        await update.message.reply_text(
            message[start_index:start_index + 3500],
            reply_markup=main_keyboard(),
        )


async def stats_command(update, context):
    s = get_stats(update.effective_user.id)
    await update.message.reply_text(
        "📊 Your statistics\n\n"
        f"Pending: {s['pending']}\n"
        f"Marked successful by you: {s['success']}\n"
        f"Marked unsuccessful by you: {s['failed']}\n"
        f"Success rate among decided feedback: {calculate_rate(s)}\n\n"
        "These are your own feedback entries, not verified Meta outcomes.",
        reply_markup=main_keyboard(),
    )


async def global_command(update, context):
    s = get_global_stats()
    total = sum(s.values())

    lines = [
        "🌐 GLOBAL STATUS",
        "================",
        f"Total reports: {total}",
        f"Pending: {s['pending']}",
        f"Marked successful: {s['success']}",
        f"Marked unsuccessful: {s['failed']}",
        f"Success rate among decided feedback: {calculate_rate(s)}",
        "",
        "🌍 COUNTRY-WISE REPORTS",
    ]

    country_rows = get_country_stats()
    if not country_rows:
        lines.append("No reports have been recorded yet.")
    else:
        for country, count, pending, success, failed in country_rows:
            lines.append(
                f"\n{country}: {count} total"
                f"\n  Pending: {pending or 0}"
                f"\n  Successful feedback: {success or 0}"
                f"\n  Unsuccessful feedback: {failed or 0}"
            )

    lines.extend([
        "",
        "🕘 RECENT PUBLIC ACTIVITY",
        "Username, selected country and status may be shown.",
        "Report URLs and private account IDs are not shown.",
        "Success/failed entries are user-reported feedback, not verified results.",
    ])

    recent = get_public_recent(10)
    if recent:
        for rid, reporter, country, status, created in recent:
            lines.append(
                f"\nID: {rid} | Reporter: {reporter}"
                f"\nCountry: {country} | Status: {status}"
                f"\nDate: {created}"
            )
    else:
        lines.append("No public activity yet.")

    # Telegram has a message length limit.
    full_text = "\n".join(lines)
    chunks = []
    while len(full_text) > 3500:
        split_at = full_text.rfind("\n", 0, 3500)
        if split_at < 1:
            split_at = 3500
        chunks.append(full_text[:split_at])
        full_text = full_text[split_at:].lstrip("\n")
    chunks.append(full_text)

    for chunk in chunks:
        await update.message.reply_text(
            chunk,
            reply_markup=main_keyboard(),
        )


async def history_command(update, context):
    rows = get_history(update.effective_user.id)

    if not rows:
        await update.message.reply_text(
            "You have no saved reports yet.",
            reply_markup=main_keyboard(),
        )
        return

    lines = ["📂 Your latest reports:"]
    for rid, target, country, status, created in rows:
        lines.append(
            f"\nID: {rid}\nTarget: {target}\n"
            f"Country: {country}\nStatus: {status}\nDate: {created}"
        )

    await update.message.reply_text(
        "\n".join(lines),
        reply_markup=main_keyboard(),
    )


async def cancel(update, context):
    context.user_data.clear()
    await update.message.reply_text(
        "Cancelled. Use the menu whenever you are ready.",
        reply_markup=main_keyboard(),
    )
    return ConversationHandler.END


# ---------------- REPORT CREATION ----------------

async def begin_report(update, context):
    context.user_data.clear()
    await update.message.reply_text(
        "Step 1/4\n\n"
        "Enter the target username (for example, @username) "
        "or the public URL of the content/account.\n\n"
        "Type /cancel to stop."
    )
    return TARGET


async def receive_target(update, context):
    target = update.message.text.strip()

    if len(target) < 2 or len(target) > 500:
        await update.message.reply_text(
            "Please enter a valid username or public URL."
        )
        return TARGET

    context.user_data["target"] = normalize_target(target)

    await update.message.reply_text(
        "Step 2/4\n\n"
        "Type the country/territory name exactly as listed.\n"
        "Example: Italy, Bangladesh, United States.\n\n"
        "Use /countries to see the list."
    )
    return COUNTRY


async def receive_country(update, context):
    result = country_matches(update.message.text)

    if result is None:
        await update.message.reply_text(
            "I couldn't find that country. Please try again or use /countries."
        )
        return COUNTRY

    if isinstance(result, list):
        await update.message.reply_text(
            "Several matches found:\n\n"
            + "\n".join(result)
            + "\n\nPlease type the full country name."
        )
        return COUNTRY

    context.user_data["country"] = result

    await update.message.reply_text(
        "Step 3/4\n\n"
        "Send the direct URL of your original work "
        "(the content you own or are authorized to represent).\n"
        "It must begin with http:// or https://."
    )
    return ORIGINAL_LINK


async def receive_original(update, context):
    link = update.message.text.strip()

    if not valid_url(link):
        await update.message.reply_text(
            "Please send a valid URL starting with https:// or http://."
        )
        return ORIGINAL_LINK

    context.user_data["original"] = link

    await update.message.reply_text(
        "Step 4/4\n\n"
        "Now send the direct URL of the content you believe "
        "copies your original work."
    )
    return REPORTED_LINK


async def receive_reported(update, context):
    link = update.message.text.strip()

    if not valid_url(link):
        await update.message.reply_text(
            "Please send a valid URL starting with https:// or http://."
        )
        return REPORTED_LINK

    user = update.effective_user
    reporter_name = f"@{user.username}" if user.username else "Anonymous"

    target = context.user_data["target"]
    country = context.user_data["country"]
    original = context.user_data["original"]

    rid = add_submission(
        user.id,
        reporter_name,
        target,
        country,
        original,
        link,
    )

    draft = report_text(target, country, original, link)

    await update.message.reply_text(
        f"✅ Draft created and saved. Report ID: {rid}\n\n"
        "Review every statement before using the draft.\n"
        f"To record your feedback later, use /success {rid} "
        f"or /failed {rid}.\n\n"
        "Your selected country is a record of your choice; "
        "it does not prove legal jurisdiction or eligibility.",
        reply_markup=main_keyboard(),
    )

    # Send as plain text to avoid HTML formatting issues in URLs.
    await update.message.reply_text(draft)

    context.user_data.clear()
    return ConversationHandler.END


# ---------------- USER FEEDBACK ----------------

async def success_command(update, context):
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text(
            "Usage: /success REPORT_ID\nExample: /success 1"
        )
        return

    rid = int(context.args[0])
    ok = set_status(update.effective_user.id, rid, "success")

    if ok:
        await update.message.reply_text(
            f"Report {rid} marked successful based on your feedback. "
            "Global Status has been updated."
        )
    else:
        await update.message.reply_text(
            "Report ID not found in your own history."
        )


async def failed_command(update, context):
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text(
            "Usage: /failed REPORT_ID\nExample: /failed 1"
        )
        return

    rid = int(context.args[0])
    ok = set_status(update.effective_user.id, rid, "failed")

    if ok:
        await update.message.reply_text(
            f"Report {rid} marked unsuccessful based on your feedback. "
            "Global Status has been updated."
        )
    else:
        await update.message.reply_text(
            "Report ID not found in your own history."
        )


async def menu_text(update, context):
    text = update.message.text.strip()

    if text == "📝 Create report":
        return await begin_report(update, context)

    if text == "🌍 Countries":
        return await countries_command(update, context)

    if text == "📊 My statistics":
        return await stats_command(update, context)

    if text == "🌐 Global Status":
        return await global_command(update, context)

    if text == "📂 My history":
        return await history_command(update, context)

    if text == "❌ Cancel":
        return await cancel(update, context)

    await update.message.reply_text(
        "Choose an option from the menu or use /start.",
        reply_markup=main_keyboard(),
    )


# ---------------- HEALTH CHECK ----------------

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path not in ("/", "/health"):
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"Not found")
            return

        body = b"Telegram bot is running!"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        return


def start_health_server():
    server = ThreadingHTTPServer(("0.0.0.0", PORT), HealthHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    logging.info("Health endpoint started on port %s", PORT)


# ---------------- START BOT ----------------

def main():
    if not BOT_TOKEN:
        raise RuntimeError(
            "BOT_TOKEN is missing. Set it in the hosting environment variables."
        )

    init_db()
    start_health_server()

    app = Application.builder().token(BOT_TOKEN).build()

    conversation = ConversationHandler(
        entry_points=[
            CommandHandler("create", begin_report),
            MessageHandler(
                filters.Regex(r"^📝 Create report$"),
                begin_report,
            ),
        ],
        states={
            TARGET: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND, receive_target
                )
            ],
            COUNTRY: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND, receive_country
                )
            ],
            ORIGINAL_LINK: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND, receive_original
                )
            ],
            REPORTED_LINK: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND, receive_reported
                )
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("countries", countries_command))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(CommandHandler("history", history_command))
    app.add_handler(CommandHandler("global", global_command))
    app.add_handler(CommandHandler("success", success_command))
    app.add_handler(CommandHandler("failed", failed_command))
    app.add_handler(conversation)
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, menu_text)
    )

    logging.info("Bot is starting...")
    app.run_polling(drop_pending_updates=False)


if __name__ == "__main__":
    main()

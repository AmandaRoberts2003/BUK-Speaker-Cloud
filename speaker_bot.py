import asyncio
import logging
import html
import os
import re
import secrets
import sqlite3
import json
import urllib.request
import urllib.error
import urllib.parse
from datetime import timedelta
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from contextlib import contextmanager

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ChatType
from telegram.error import RetryAfter, TelegramError
from telegram.ext import (
    Application,
    ChatMemberHandler,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# Keep credentials in the cloud host's environment variables, never in source code.
BOT_TOKEN = os.getenv("BOT_TOKEN")
# Existing primary admin remains authorized. Add two more admins in FadeHost's
# environment variables using their NUMERIC Telegram user IDs (not usernames).
ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID", "488671902"))


def load_admin_user_ids():
    """Preserve primary admin, and load additional trusted administrators."""
    raw = os.getenv("ADDITIONAL_ADMIN_USER_IDS", "")
    ids = {ADMIN_USER_ID}
    for value in raw.split(","):
        value = value.strip()
        if not value:
            continue
        if not value.isdecimal() or int(value) <= 0:
            raise ValueError(
                "ADDITIONAL_ADMIN_USER_IDS must contain comma-separated numeric Telegram user IDs"
            )
        ids.add(int(value))
    return frozenset(ids)


ADMIN_USER_IDS = load_admin_user_ids()

DATA_DIR = os.getenv("RAILWAY_VOLUME_MOUNT_PATH", ".")
os.makedirs(DATA_DIR, exist_ok=True)
DB_FILE = os.path.join(DATA_DIR, "speaker_groups.db")

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
)
logger = logging.getLogger("buk_speaker")

WELCOME_MESSAGE = """📢 Dear BUK Drivers,

To make communication faster, cleaner, and a little less chaotic, we have officially introduced BUK Speaker to your individual group chats.

🤖 <b>BUK Speaker is your loyal messenger — it doesn’t think, it doesn’t judge, and it definitely doesn’t gossip. It just delivers</b>!

Its job is simple: make sure important BUK messages actually reach you.

From now on, BUK Speaker may contact you with:

📣 Company announcements
📋 Policy and procedure updates
📸 PTI / inspection photo requests
🦺 Safety reminders and required safety actions
🚛 Operational updates and instructions
⚠️ Urgent notices that may require immediate attention

<b>So when BUK Speaker talks, please don’t ghost it. 👀</b>

Read the message carefully, follow any instructions provided, and complete any required action as soon as possible.

Some messages may be simple reminders. Others may involve safety, compliance, or important operational requirements — so please treat messages from BUK Speaker as official company communication.

🛡 Our goal is simple: fewer missed messages, faster communication, safer operations, and less confusion for everyone.

<b>BUK Speaker won’t call you at 2 AM to gossip — but when it sends a message, it probably has something important to say.</b> 📢

Thank you for your cooperation and for helping us keep BUK communication fast, organized, and reliable!"""


# Public-facing interactive assistant. We will refine each section's content together.
COMPANY_WEBSITE = os.getenv("BUK_WEBSITE_URL", "https://bukharausa.co/")
GOOGLE_URL = "https://www.google.com/search?q=BUK+Transportation"
INSTAGRAM_URL = os.getenv("BUK_INSTAGRAM_URL", "https://www.instagram.com/buk__transportation/")
FACEBOOK_URL = os.getenv("BUK_FACEBOOK_URL", "https://www.facebook.com/buktransportation/")

SECTION_CONTENT = {
    "about": (
        "🏢 <b>About BUK Transportation</b>\n\n"
        "BUK Transportation began its journey in 2017 with its first truck and first customer. "
        "Since then, the company has continued to grow, expand into new markets, and strengthen its transportation operations.\n\n"
        "Our responsibility goes beyond moving freight. We are committed to safety, communication, reliability, responsibility, and professionalism.\n\n"
        "Our goal is to provide dependable transportation service while building strong, lasting relationships with our drivers, customers, and business partners.\n\n"
        "<b>Our mission:</b> Put safety first, communicate promptly, keep our commitments, handle every load responsibly, and represent BUK with pride.\n\n"
        "<i>Moving forward, every mile and every time.</i>"
    ),
    "services": (
        "🚛 <b>Our Services</b>\n\n"
        "BUK Transportation supports the transportation industry through the following service areas:\n\n"
        "<b>1. Freight Transportation</b>\n"
        "Transportation solutions for customer freight, including Power Only, Dry Van, and Refrigerated transportation.\n\n"
        "<b>2. Accounting & Financial Services</b>\n"
        "Accounting and financial support for transportation businesses, including accounts receivable, accounts payable, bookkeeping, payroll-related support, and financial reporting.\n\n"
        "<b>3. Driver Relations</b>\n"
        "Support and communication to help drivers understand company procedures, reach the right department, and get assistance with driver-related questions.\n\n"
        "<b>4. Operations & Safety Support</b>\n"
        "Operational coordination and safety-focused procedures that support reliable transportation, equipment care, and communication.\n\n"
        "For details about a specific service, please contact the appropriate BUK department."
    ),
    "drivers": (
        "👥 <b>Driver Opportunities</b>\n\n"
        "BUK Transportation is always interested in connecting with professional, responsible, and safety-conscious drivers who want to grow in the transportation industry.\n\n"
        "<b>1. Company Driver Opportunities</b>\n"
        "Join our driving team and become part of a transportation operation focused on safety, reliability, communication, and professionalism.\n\n"
        "We value drivers who maintain safe driving practices, demonstrate reliability, communicate effectively with Dispatch, take care of their equipment, and follow company procedures and applicable regulations.\n\n"
        "<b>2. Owner-Operator Opportunities</b>\n"
        "We welcome inquiries from independent owner-operators interested in working with BUK Transportation. We value professional operators who prioritize safe operations, responsible equipment management, clear communication, and dependable performance.\n\n"
        "<b>3. Career Development</b>\n"
        "We believe professional growth comes through experience, responsibility, and continuous improvement. Drivers are encouraged to build strong safety and performance records, improve communication and operational skills, and develop their experience and leadership abilities.\n\n"
        "<b>Interested in joining BUK?</b> Contact Driver Relations & Recruiting for information about current opportunities, qualifications, and the application process.\n\n"
        "<i>Drive with purpose. Grow with BUK.</i>"
    ),
    "business": (
        "🤝 <b>Business Inquiries</b>\n\n"
        "At BUK Transportation, we value strong business relationships and opportunities to build long-term partnerships. We work with businesses across the transportation and logistics industry to deliver reliable freight solutions and support their operational needs.\n\n"
        "<b>1. Shippers & Customers</b>\n"
        "Looking for a dependable transportation partner? We welcome inquiries from businesses seeking freight transportation services, consistent communication, and reliable delivery support.\n\n"
        "<b>2. Brokers & Logistics Partners</b>\n"
        "We are open to building professional relationships with freight brokers, logistics providers, and transportation partners interested in working together.\n\n"
        "<b>3. Business Partnerships</b>\n"
        "We welcome opportunities to collaborate with companies that provide transportation-related services, equipment, technology, and operational solutions.\n\n"
        "<b>4. General Business Inquiries</b>\n"
        "For questions about our services, potential partnerships, or other business opportunities, please contact our Management team.\n\n"
        "<i>Let's build reliable partnerships and move business forward with BUK.</i>"
    ),
    "contact": (
        "📞 <b>Contact BUK Transportation</b>\n\n"
        "Effective communication is essential to safe operations, reliable service, and strong business relationships. Contact the department best suited to your inquiry.\n\n"
        "<b>📍 Track & Trace</b>\n"
        "Shipment tracking, delivery updates, estimated arrival times (ETA), and load status inquiries.\n"
        "✉️ trace@buktransport.com\n"
        "☎️ (878) 978-3268\n\n"
        "<b>🚛 Dispatch & Operations</b>\n"
        "Load assignments, pickup and delivery appointments, routing, dispatch instructions, and operational coordination.\n"
        "✉️ dispatch@bukharausa.co\n"
        "☎️ (878) 999-8549\n\n"
        "<b>👥 Driver Relations & Recruiting</b>\n"
        "Driver support, recruiting opportunities, employment inquiries, and general driver-related assistance.\n"
        "✉️ support@buktransport.com\n"
        "☎️ (223) 895-3391 | (717) 963-7004\n\n"
        "<b>🏢 Management</b>\n"
        "Management-related matters, business inquiries, escalations, and general company matters.\n"
        "✉️ management@bukharausa.co\n"
        "☎️ (646) 683-2859\n\n"
        "<b>🦺 Safety & Compliance</b>\n"
        "Safety concerns, accident reporting, roadside inspections, Hours of Service (HOS), ELD issues, and compliance-related matters.\n"
        "✉️ safety@buktransport.com\n"
        "☎️ (878) 305-3533\n\n"
        "<i>BUK Transportation — The right department. The right communication. The right solution.</i>"
    ),
    "faq": (
        "❓ <b>Frequently Asked Questions</b>\n\n"
        "Choose a question below to find the information you need."
    ),
}

FAQ_CONTENT = {
    "faq:company": (
        "🏢 <b>What is BUK Transportation?</b>\n\n"
        "BUK Transportation is a transportation company that began its journey in 2017. Our focus is on safe operations, reliable service, clear communication, and professional relationships."
    ),
    "faq:services": (
        "🚛 <b>What transportation services does BUK provide?</b>\n\n"
        "BUK's freight transportation services include Power Only, Dry Van, and Refrigerated transportation. Please contact our team for service details and availability."
    ),
    "faq:apply": (
        "👥 <b>How can I apply for a driving position?</b>\n\n"
        "Contact Driver Relations & Recruiting at support@buktransport.com or call (223) 895-3391 or (717) 963-7004. The team can provide information about current opportunities, qualifications, and the application process."
    ),
    "faq:owner": (
        "🚚 <b>Does BUK work with owner-operators?</b>\n\n"
        "Yes. We welcome inquiries from independent owner-operators interested in working with BUK Transportation. Contact Driver Relations & Recruiting for more information."
    ),
    "faq:track": (
        "📍 <b>How can I track my shipment?</b>\n\n"
        "Contact Track & Trace at trace@buktransport.com or call (878) 978-3268 for shipment status, delivery updates, or estimated arrival times."
    ),
    "faq:department": (
        "☎️ <b>How can I contact the correct department?</b>\n\n"
        "Open the Contact Us section in the main menu to see the responsibilities and contact details for Track & Trace, Dispatch & Operations, Driver Relations & Recruiting, Management, and Safety & Compliance."
    ),
    "faq:partner": (
        "🤝 <b>How can my company work with BUK?</b>\n\n"
        "For shipper, broker, logistics, or business partnership inquiries, contact Management at management@bukharausa.co or call (646) 683-2859."
    ),
}


def main_menu_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🏢 About BUK", callback_data="section:about"),
         InlineKeyboardButton("🚛 Our Services", callback_data="section:services")],
        [InlineKeyboardButton("👥 Driver Opportunities", callback_data="section:drivers"),
         InlineKeyboardButton("🤝 Business Inquiries", callback_data="section:business")],
        [InlineKeyboardButton("📞 Contact Us", callback_data="section:contact"),
         InlineKeyboardButton("🌐 Website & Social Media", callback_data="section:web")],
        [InlineKeyboardButton("❓ Frequently Asked Questions", callback_data="section:faq")],
    ])


def faq_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🏢 What is BUK Transportation?", callback_data="faq:company")],
        [InlineKeyboardButton("🚛 What services does BUK provide?", callback_data="faq:services")],
        [InlineKeyboardButton("👥 How can I apply as a driver?", callback_data="faq:apply")],
        [InlineKeyboardButton("🚚 Does BUK work with owner-operators?", callback_data="faq:owner")],
        [InlineKeyboardButton("📍 How can I track my shipment?", callback_data="faq:track")],
        [InlineKeyboardButton("☎️ How do I contact a department?", callback_data="faq:department")],
        [InlineKeyboardButton("🤝 How can my company work with BUK?", callback_data="faq:partner")],
        [InlineKeyboardButton("⬅️ Back to Main Menu", callback_data="menu:main")],
    ])


def website_keyboard():
    buttons = [
        [InlineKeyboardButton("🌐 Official BUK Website", url=COMPANY_WEBSITE)],
        [InlineKeyboardButton("🔎 Google – Company Overview", url=GOOGLE_URL)],
        [InlineKeyboardButton("📸 Instagram", url=INSTAGRAM_URL)],
        [InlineKeyboardButton("📘 Facebook", url=FACEBOOK_URL)],
        [InlineKeyboardButton("⬅️ Back to Main Menu", callback_data="menu:main")],
    ]
    return InlineKeyboardMarkup(buttons)


async def show_main_menu(message):
    await message.reply_text(
        "👋 <b>Welcome to BUK Transportation!</b>\n\n"
        "I'm BUK Speaker, your BUK information assistant. Choose a section below:",
        parse_mode="HTML",
        reply_markup=main_menu_keyboard(),
        disable_web_page_preview=True,
    )


async def interactive_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return
    # Interactive responses are private-chat only; never respond in driver groups.
    if query.message is None or query.message.chat.type != ChatType.PRIVATE:
        await query.answer()
        return

    await query.answer()
    selection = query.data or ""

    if selection == "menu:main":
        await query.edit_message_text(
            "👋 <b>Welcome to BUK Transportation!</b>\n\nChoose a section below:",
            parse_mode="HTML",
            reply_markup=main_menu_keyboard(),
        )
    elif selection == "section:web":
        links_exist = bool(COMPANY_WEBSITE or INSTAGRAM_URL or FACEBOOK_URL)
        message = (
            "🌐 <b>BUK Website & Social Media</b>\n\n"
            "Connect with BUK Transportation through our official online channels."
        )
        if not links_exist:
            message += "\n\nOur links are being configured and will appear here soon."
        await query.edit_message_text(
            message, parse_mode="HTML", reply_markup=website_keyboard(),
            disable_web_page_preview=True,
        )
    elif selection == "section:faq":
        await query.edit_message_text(
            SECTION_CONTENT["faq"], parse_mode="HTML", reply_markup=faq_keyboard()
        )
    elif selection in FAQ_CONTENT:
        await query.edit_message_text(
            FAQ_CONTENT[selection],
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ Back to FAQs", callback_data="section:faq")],
                [InlineKeyboardButton("🏠 Main Menu", callback_data="menu:main")],
            ]),
            disable_web_page_preview=True,
        )
    else:
        key = selection.removeprefix("section:")
        message = SECTION_CONTENT.get(key)
        if message:
            await query.edit_message_text(
                message,
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("⬅️ Main Menu", callback_data="menu:main")
                ]]),
                disable_web_page_preview=True,
            )
        else:
            await query.edit_message_text(
                "Sorry, I couldn't find that section.",
                reply_markup=main_menu_keyboard(),
            )



@contextmanager
def get_connection():
    conn = sqlite3.connect(DB_FILE, timeout=30)
    conn.execute("PRAGMA busy_timeout = 30000")
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def init_database():
    """Create the group table and safely upgrade the previous database."""
    with get_connection() as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS groups (
                chat_id INTEGER PRIMARY KEY,
                group_name TEXT NOT NULL,
                welcome_sent INTEGER NOT NULL DEFAULT 1
            )"""
        )
        columns = {
            row[1] for row in conn.execute("PRAGMA table_info(groups)").fetchall()
        }
        if "welcome_sent" not in columns:
            # Existing registered groups retain their one-time welcome status.
            conn.execute(
                "ALTER TABLE groups ADD COLUMN welcome_sent INTEGER NOT NULL DEFAULT 1"
            )
        conn.execute("""CREATE TABLE IF NOT EXISTS weather_locations (
            chat_id INTEGER PRIMARY KEY,
            latitude REAL NOT NULL,
            longitude REAL NOT NULL,
            assigned_at TEXT NOT NULL,
            assigned_by INTEGER NOT NULL
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS weather_deliveries (
            chat_id INTEGER NOT NULL,
            alert_id TEXT NOT NULL,
            sent_at TEXT NOT NULL,
            PRIMARY KEY (chat_id, alert_id)
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS weather_advisory_cooldown (
            chat_id INTEGER NOT NULL,
            event TEXT NOT NULL,
            sent_at TEXT NOT NULL,
            PRIMARY KEY (chat_id, event)
        )""")
        # Explicit group-to-vehicle links are never inferred from names.
        conn.execute("""CREATE TABLE IF NOT EXISTS samsara_group_links (
            chat_id INTEGER PRIMARY KEY,
            vehicle_id TEXT NOT NULL UNIQUE,
            linked_at TEXT NOT NULL,
            linked_by INTEGER NOT NULL
        )""")
        # Pending match reviews must be confirmed explicitly by the same admin.
        conn.execute("""CREATE TABLE IF NOT EXISTS samsara_match_reviews (
            admin_id INTEGER NOT NULL,
            unit TEXT NOT NULL,
            chat_id INTEGER NOT NULL,
            vehicle_id TEXT NOT NULL,
            reviewed_at TEXT NOT NULL,
            PRIMARY KEY (admin_id, unit)
        )""")
        # Read-only status/audit of automatic onboarding, preserved across restarts.
        conn.execute("""CREATE TABLE IF NOT EXISTS samsara_auto_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            unit TEXT NOT NULL,
            chat_id INTEGER NOT NULL,
            vehicle_id TEXT NOT NULL,
            status TEXT NOT NULL
        )""")
        # Separate private monitoring log. Only an authenticated admin may read results.
        # Unique key persists across restarts and prevents repeated notifications.
        conn.execute("""CREATE TABLE IF NOT EXISTS weather_admin_notifications (
            admin_id INTEGER NOT NULL,
            chat_id INTEGER NOT NULL,
            alert_id TEXT NOT NULL,
            event TEXT NOT NULL,
            delivered_at TEXT NOT NULL,
            PRIMARY KEY(admin_id, chat_id, alert_id)
        )""")
        # GPS health episodes and per-admin delivery receipts survive bot restarts.
        conn.execute("""CREATE TABLE IF NOT EXISTS gps_health_episodes (
            chat_id INTEGER PRIMARY KEY,
            vehicle_id TEXT NOT NULL,
            started_at TEXT NOT NULL,
            state TEXT NOT NULL CHECK(state IN ('issue', 'recovered')),
            last_checked_at TEXT NOT NULL
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS gps_health_notifications (
            chat_id INTEGER NOT NULL,
            episode_started_at TEXT NOT NULL,
            admin_id INTEGER NOT NULL,
            kind TEXT NOT NULL CHECK(kind IN ('stale', 'recovered')),
            delivered_at TEXT NOT NULL,
            PRIMARY KEY (chat_id, episode_started_at, admin_id, kind)
        )""")
        # v24: persistent pilot-only real-NWS test delivery records. Never driver groups.
        conn.execute("""CREATE TABLE IF NOT EXISTS weather_pilot_live_deliveries (
            source_chat_id INTEGER NOT NULL,
            alert_id TEXT NOT NULL,
            destination_chat_id INTEGER NOT NULL,
            event TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('pending','sent')),
            created_at TEXT NOT NULL,
            sent_at TEXT,
            PRIMARY KEY(source_chat_id, alert_id)
        )""")
        # Archive is shared by authorized admins; no Telegram private-chat access is granted.
        conn.execute("""CREATE TABLE IF NOT EXISTS announcements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            sender_id INTEGER NOT NULL,
            kind TEXT NOT NULL,
            body TEXT,
            source_chat_id INTEGER,
            source_message_id INTEGER,
            delivered INTEGER NOT NULL,
            inactive INTEGER NOT NULL,
            unverified INTEGER NOT NULL,
            failed INTEGER NOT NULL,
            report TEXT NOT NULL
        )""")


def is_admin(update: Update) -> bool:
    return (
        update.effective_user is not None
        and update.effective_user.id in ADMIN_USER_IDS
    )


def is_group_chat(update: Update) -> bool:
    return (
        update.effective_chat is not None
        and update.effective_chat.type in (ChatType.GROUP, ChatType.SUPERGROUP)
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Public interactive menu in private chat; group chats remain silent.
    if update.effective_chat and update.effective_chat.type == ChatType.PRIVATE:
        await show_main_menu(update.effective_message)


async def myid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user:
        await update.effective_message.reply_text(
            f"Your Telegram User ID is:\n{update.effective_user.id}"
        )


async def groupid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    if not is_group_chat(update):
        await update.effective_message.reply_text("Please use this command inside a group.")
        return
    chat = update.effective_chat
    await update.effective_message.reply_text(
        f"📢 Group Name: {chat.title or 'Unnamed Group'}\nGroup ID: {chat.id}"
    )


async def addgroup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Manually register a group without sending/re-sending the introduction."""
    if not is_admin(update):
        await update.effective_message.reply_text("⛔ You are not authorized to use this bot.")
        return
    if not is_group_chat(update):
        await update.effective_message.reply_text(
            "❌ Please use /addgroup inside the Telegram group you want to register."
        )
        return

    chat = update.effective_chat
    with get_connection() as conn:
        # Preserve the existing welcome_sent flag on an already registered group.
        conn.execute(
            """INSERT INTO groups (chat_id, group_name, welcome_sent)
               VALUES (?, ?, 1)
               ON CONFLICT(chat_id) DO UPDATE SET group_name = excluded.group_name""",
            (chat.id, chat.title or "Unnamed Group"),
        )
    await update.effective_message.reply_text(
        f"✅ Group registered successfully!\n\n"
        f"📢 Group: {chat.title or 'Unnamed Group'}\nGroup ID: {chat.id}"
    )


async def send_welcome_if_pending(chat_id: int, group_name: str, context: ContextTypes.DEFAULT_TYPE):
    """Send only if this group's welcome has not yet been recorded as sent."""
    with get_connection() as conn:
        conn.execute(
            """INSERT OR IGNORE INTO groups (chat_id, group_name, welcome_sent)
               VALUES (?, ?, 0)""",
            (chat_id, group_name),
        )
        row = conn.execute(
            "SELECT welcome_sent FROM groups WHERE chat_id = ?", (chat_id,)
        ).fetchone()

    if row is None or row[0] == 1:
        logger.info("Welcome already sent or group unavailable: %s (%s)", group_name, chat_id)
        return False

    try:
        await context.bot.send_message(
            chat_id=chat_id,
            text=WELCOME_MESSAGE,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
    except TelegramError:
        logger.exception("Could not send BUK Speaker introduction to %s (%s)", group_name, chat_id)
        return False

    # Mark as sent only after Telegram confirms the send request succeeded.
    with get_connection() as conn:
        conn.execute(
            "UPDATE groups SET group_name = ?, welcome_sent = 1 WHERE chat_id = ?",
            (group_name, chat_id),
        )
    logger.info("Sent one-time introduction to %s (%s)", group_name, chat_id)
    return True


async def on_bot_membership_change(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Automatically introduce the bot when it is first added to a group."""
    membership = update.my_chat_member
    if membership is None:
        return

    chat = membership.chat
    if chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        return

    old_status = membership.old_chat_member.status
    new_status = membership.new_chat_member.status
    joined = old_status in ("left", "kicked") and new_status in ("member", "administrator")
    if not joined:
        return

    await send_welcome_if_pending(
        chat_id=chat.id,
        group_name=chat.title or "Unnamed Group",
        context=context,
    )


async def retrywelcome(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin-only retry for a welcome that could not be delivered earlier."""
    if not is_admin(update):
        await update.effective_message.reply_text("⛔ You are not authorized to use this bot.")
        return
    if not is_group_chat(update):
        await update.effective_message.reply_text("Please use /retrywelcome inside the group.")
        return

    chat = update.effective_chat
    sent = await send_welcome_if_pending(
        chat_id=chat.id,
        group_name=chat.title or "Unnamed Group",
        context=context,
    )
    if sent:
        await update.effective_message.reply_text("✅ The introduction message has been sent.")
    else:
        await update.effective_message.reply_text(
            "ℹ️ The introduction is already recorded as sent, or Telegram could not deliver it. "
            "Please check that BUK Speaker has permission to send messages."
        )


# Only a status label at the start of the title marks a group inactive.
# Examples: "Inactive BUK 113 Jermall Dent" and "[INACTIVE] BUK 113".
INACTIVE_PREFIX = re.compile(r"^\s*(?:\[\s*inactive\s*\]|inactive\b)", re.IGNORECASE)


def is_inactive_group_name(title: str) -> bool:
    return bool(INACTIVE_PREFIX.search(title or ""))


async def get_live_group(chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    """Verify a group's current title; never trust a stale saved title to send."""
    try:
        return await context.bot.get_chat(chat_id)
    except RetryAfter as error:
        await asyncio.sleep(float(error.retry_after) + 1)
        try:
            return await context.bot.get_chat(chat_id)
        except TelegramError:
            logger.exception("Group verification failed after retry: %s", chat_id)
    except TelegramError:
        logger.exception("Group verification failed: %s", chat_id)
    return None


async def on_group_title_change(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cache title changes, but always fetch live titles before broadcasts."""
    if not is_group_chat(update) or not update.effective_message:
        return
    title = update.effective_message.new_chat_title
    if title:
        with get_connection() as conn:
            conn.execute("UPDATE groups SET group_name = ? WHERE chat_id = ?", (title, update.effective_chat.id))
        # Auto-matching will check the live title on its next scheduled scan.
        # Existing links are NOT silently reassigned when drivers/groups change.


# SAMSARA PHASE 2: read-only GPS + PRIVATE preview. Never automatically sends to groups.
# Require explicit exact ID links; group names/unit numbers are not safe identifiers.
SAMSARA_API_TOKEN = os.getenv("SAMSARA_API_TOKEN", "").strip()
SAMSARA_API_BASE = "https://api.samsara.com"
SAMSARA_MAX_GPS_AGE_MINUTES = max(1, min(60, int(os.getenv("SAMSARA_MAX_GPS_AGE_MINUTES", "15"))))


def samsara_valid_gps(gps, now=None):
    """Reject old, future, unparseable or out-of-bounds GPS. Never extrapolate."""
    now = now or datetime.now(timezone.utc)
    if not isinstance(gps, dict):
        return None
    ts = weather_parse_time(gps.get("time"))
    if ts is None or ts > now + timedelta(minutes=1) or now - ts > timedelta(minutes=SAMSARA_MAX_GPS_AGE_MINUTES):
        return None
    lat, lon = gps.get("latitude"), gps.get("longitude")
    if type(lat) not in (int, float) or type(lon) not in (int, float):
        return None
    if not (18 <= lat <= 72 and -180 <= lon <= -60):
        return None
    return lat, lon, ts


def samsara_get_gps_snapshot():
    """Current fleet GPS snapshot, read-only. Paginate; never log credentials."""
    if not SAMSARA_API_TOKEN:
        raise ValueError("Samsara token is not configured")
    result = {}
    cursor = None
    seen_cursors = set()
    for _ in range(20):
        params = {"types": "gps"}
        if cursor:
            params["after"] = cursor
        url = SAMSARA_API_BASE + "/fleet/vehicles/stats?" + urllib.parse.urlencode(params)
        request = urllib.request.Request(url, headers={
            "Authorization": "Bearer " + SAMSARA_API_TOKEN,
            "Accept": "application/json",
        })
        with urllib.request.urlopen(request, timeout=18) as response:
            payload = json.load(response)
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
            raise ValueError("Unexpected Samsara API response")
        for record in payload["data"]:
            if isinstance(record, dict) and record.get("id") is not None:
                result[str(record["id"])] = record
        pg = payload.get("pagination") or {}
        if not pg.get("hasNextPage"):
            return result
        new_cursor = pg.get("endCursor")
        if not isinstance(new_cursor, str) or not new_cursor or new_cursor in seen_cursors:
            raise ValueError("Invalid Samsara pagination")
        seen_cursors.add(new_cursor)
        cursor = new_cursor
    raise ValueError("Samsara snapshot exceeded safe page limit")


async def samsara_snapshot_or_error(update):
    if not SAMSARA_API_TOKEN:
        await update.effective_message.reply_text(
            "Samsara not connected. Add SAMSARA_API_TOKEN in FadeHost Settings first."
        )
        return None
    try:
        return await asyncio.to_thread(samsara_get_gps_snapshot)
    except urllib.error.HTTPError as error:
        # Never log the request, URL, response body or authorization header.
        # They may contain sensitive information. HTTP status is safe to share.
        status = int(error.code)
        explanations = {
            400: "Invalid API request parameters",
            401: "Authentication failed: check the COMPLETE, current API token",
            403: "Permission denied: check Samsara token scopes and organization access",
            404: "Endpoint not found",
            429: "Rate limit reached; retry later",
        }
        description = explanations.get(status, "API request failed")
        logger.warning("Samsara vehicle stats API returned HTTP %s", status)
        await update.effective_message.reply_text(
            f"Samsara GPS check failed: HTTP {status}. {description}.\n"
            "No messages sent to driver groups."
        )
        return None
    except Exception as error:
        # Do not serialize exception objects or URLs; they can expose credentials.
        logger.warning("Samsara snapshot unavailable: %s", type(error).__name__)
        await update.effective_message.reply_text(
            f"Samsara GPS check failed ({type(error).__name__}). "
            "No messages sent to driver groups."
        )
        return None


def samsara_private_admin(update):
    return bool(is_admin(update) and update.effective_chat and
                update.effective_chat.type == ChatType.PRIVATE)


async def samsarastatus_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not samsara_private_admin(update):
        return
    with get_connection() as conn:
        links = conn.execute("SELECT COUNT(*) FROM samsara_group_links").fetchone()[0]
    await update.effective_message.reply_text(
        f"Samsara token: {'configured' if SAMSARA_API_TOKEN else 'NOT CONFIGURED'}\n"
        f"Verified group-to-vehicle links: {links}\n"
        f"GPS max age: {SAMSARA_MAX_GPS_AGE_MINUTES} minutes\n"
        "Samsara weather delivery: OFF (private preview only)\n"
        "Automatic driver weather messages: NOT ENABLED by this integration."
    )


async def samsaraunits_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not samsara_private_admin(update):
        return
    data = await samsara_snapshot_or_error(update)
    if data is None:
        return
    pattern = " ".join(context.args).casefold().strip()
    matching = [(vid, rec) for vid, rec in data.items()
                if not pattern or pattern in str(rec.get("name", "")).casefold() or pattern in vid.casefold()]
    matching.sort(key=lambda entry: str(entry[1].get("name", "")))
    lines = [f"{str(rec.get('name') or 'Unnamed')[:45]} | ID: {vid}" for vid, rec in matching[:20]]
    await update.effective_message.reply_text(
        f"Samsara vehicles matching query: {len(matching)}\n" +
        ("\n".join(lines) if lines else "No matches") +
        "\n\nPrivate admin view only. Use exact vehicle ID for linking."
    )


# Exact unit matching, including approved alphanumeric identifiers such as
# 126B, 701A, and S009. Never use substrings or infer a vehicle from a name.
# A unit must begin with a digit, or contain one leading letter followed by digits;
# one final letter is allowed only when the unit begins with a digit.
UNIT_PATTERN = r"(?:[0-9]{2,6}[A-Z]?|[A-Z][0-9]{2,6})"
GROUP_UNIT_RE = re.compile(r"^\s*BUK\s+(" + UNIT_PATTERN + r")(?=\s|$)", re.IGNORECASE)
VEHICLE_UNIT_RE = re.compile(r"^\s*(" + UNIT_PATTERN + r")\s*$", re.IGNORECASE)
MATCH_REVIEW_TTL_MINUTES = 20


def group_unit_from_title(title):
    if not title or is_inactive_group_name(title):
        return None
    match = GROUP_UNIT_RE.match(title)
    return match.group(1).upper() if match else None


def vehicle_unit_from_name(name):
    match = VEHICLE_UNIT_RE.fullmatch(str(name or ""))
    return match.group(1).upper() if match else None


def unique_match_candidates(groups, vehicles, linked_groups=None, linked_vehicles=None):
    """Return unambiguous 1:1 matches and counts, never guess from name similarity."""
    linked_groups = set(linked_groups or ())
    linked_vehicles = set(linked_vehicles or ())
    by_unit_groups = {}
    by_unit_vehicles = {}
    skipped_inactive = 0
    for chat_id, title in groups:
        if is_inactive_group_name(title):
            skipped_inactive += 1
            continue
        if chat_id in linked_groups:
            continue
        unit = group_unit_from_title(title)
        if unit:
            by_unit_groups.setdefault(unit, []).append((chat_id, title))
    for vehicle_id, record in vehicles.items():
        if vehicle_id in linked_vehicles:
            continue
        unit = vehicle_unit_from_name(record.get("name"))
        if unit:
            by_unit_vehicles.setdefault(unit, []).append((vehicle_id, record.get("name")))
    matches = []
    ambiguous = []
    for unit in sorted(set(by_unit_groups) & set(by_unit_vehicles), key=lambda x: (x,)):
        gs, vs = by_unit_groups[unit], by_unit_vehicles[unit]
        if len(gs) == 1 and len(vs) == 1:
            matches.append((unit, gs[0][0], gs[0][1], vs[0][0]))
        else:
            ambiguous.append(unit)
    return matches, ambiguous, skipped_inactive


# Future groups are onboarded without manual commands after an exact unique match.
# IMPORTANT: This never turns on driver weather alerts (WEATHER_ENABLED is hard-OFF).
SAMSARA_AUTO_ONBOARD_ENABLED = os.getenv("SAMSARA_AUTO_ONBOARD_ENABLED", "true").lower() == "true"
SAMSARA_AUTO_SCAN_SECONDS = max(300, int(os.getenv("SAMSARA_AUTO_SCAN_SECONDS", "600")))
AUTO_ONBOARD_ADMIN_NOTICES = os.getenv("SAMSARA_AUTO_ADMIN_NOTICES", "true").lower() == "true"


def auto_onboard_candidates(groups, vehicles, linked):
    """Strict global uniqueness, including already-linked groups and vehicles.

    Never silently assign one vehicle to another group or guess from a driver name.
    """
    unit_groups = {}
    unit_vehicles = {}
    linked_chats = {row[0] for row in linked}
    linked_vehicle_ids = {row[1] for row in linked}
    for chat_id, title in groups:
        unit = group_unit_from_title(title)
        if unit:
            unit_groups.setdefault(unit, []).append((chat_id, title))
    for vehicle_id, rec in vehicles.items():
        unit = vehicle_unit_from_name(rec.get("name"))
        if unit:
            unit_vehicles.setdefault(unit, []).append(vehicle_id)
    candidates = []
    ambiguous = []
    no_vehicle = []
    for unit, entries in unit_groups.items():
        if len(entries) != 1 or len(unit_vehicles.get(unit, [])) > 1:
            ambiguous.append(unit)
            continue
        if len(unit_vehicles.get(unit, [])) == 0:
            no_vehicle.append(unit)
            continue
        chat_id, title = entries[0]
        vid = unit_vehicles[unit][0]
        if chat_id not in linked_chats and vid not in linked_vehicle_ids:
            candidates.append((unit, chat_id, title, vid))
    return candidates, ambiguous, no_vehicle


async def run_auto_onboarding(app):
    """Fail closed: partial snapshots, unverifiable titles, and conflicts aren't linked."""
    result = {"new": [], "retired_inactive": [], "ambiguous": [], "missing": [], "error": None}
    if not SAMSARA_AUTO_ONBOARD_ENABLED or not SAMSARA_API_TOKEN:
        result["error"] = "Automatic matching disabled or Samsara token missing"
        return result
    lock = app.bot_data.setdefault("auto_match_lock", asyncio.Lock())
    async with lock:
        try:
            vehicles = await asyncio.to_thread(samsara_get_gps_snapshot)
        except Exception as exc:
            logger.warning("Automatic Samsara matching skipped: %s", type(exc).__name__)
            result["error"] = "Samsara vehicle snapshot unavailable"
            return result
        with get_connection() as conn:
            groups = conn.execute("SELECT chat_id, group_name FROM groups").fetchall()
            linked = conn.execute("SELECT chat_id, vehicle_id FROM samsara_group_links").fetchall()
        # Verify current titles. An inaccessible group can block ONLY its own
        # known unit, never automatic matching across the entire fleet.
        # Its SAVED title is used to veto potentially conflicting assignments,
        # but is never used to authorize a new link.
        live_groups = []
        unverified_group_count = 0
        blocked_units = set()
        for chat_id, stored_title in groups:
            live = await get_live_group(chat_id, app)
            if not live or live.type not in (ChatType.GROUP, ChatType.SUPERGROUP) or not live.title:
                unverified_group_count += 1
                # If we cannot verify a previously active group's current title,
                # don't risk connecting another group with that same unit.
                # Historical titles marked Inactive cannot authorize a link.
                old_unit = group_unit_from_title(stored_title)
                if old_unit:
                    blocked_units.add(old_unit)
                continue
            live_groups.append((chat_id, live.title))
            await asyncio.sleep(0.03)
        # Only live Telegram titles are authoritative for retiring old links.
        # Update saved titles for accurate read-only reports, too.
        live_titles = dict(live_groups)
        with get_connection() as conn:
            conn.executemany(
                "UPDATE groups SET group_name=? WHERE chat_id=?",
                [(title, chat_id) for chat_id, title in live_groups],
            )
            # An old INACTIVE group's Samsara link is historical, not operational.
            # Retire it only after Telegram confirms the current title is inactive.
            # Never remove a link from an unverified group or a verified active group.
            for chat_id, vehicle_id in linked:
                title = live_titles.get(chat_id)
                if title is None or not is_inactive_group_name(title):
                    continue
                deleted = conn.execute(
                    "DELETE FROM samsara_group_links WHERE chat_id=? AND vehicle_id=?",
                    (chat_id, vehicle_id),
                ).rowcount
                if deleted:
                    now = datetime.now(timezone.utc).isoformat()
                    unit = vehicle_unit_from_name(vehicles.get(str(vehicle_id), {}).get("name")) or "unknown"
                    conn.execute(
                        "INSERT INTO samsara_auto_events(created_at,unit,chat_id,vehicle_id,status) VALUES (?,?,?,?,?)",
                        (now, unit, chat_id, vehicle_id, "retired_inactive"),
                    )
                    result["retired_inactive"].append((chat_id, vehicle_id))
                    logger.info("Retired inactive group link for group %s", chat_id)
            linked = conn.execute("SELECT chat_id, vehicle_id FROM samsara_group_links").fetchall()
        candidates, ambiguous, missing = auto_onboard_candidates(live_groups, vehicles, linked)
        result["ambiguous"] = sorted(set(ambiguous))
        result["missing"] = sorted(set(missing))
        result["unverified_groups"] = unverified_group_count
        result["blocked_units"] = sorted(blocked_units)
        for unit, chat_id, stored_title, vehicle_id in candidates:
            # An unverifiable group with this SAME saved unit prevents unsafe
            # remapping, but does not block unrelated verified trucks.
            if unit in blocked_units:
                continue
            live = await get_live_group(chat_id, app)
            if (not live or live.type not in (ChatType.GROUP, ChatType.SUPERGROUP)
                    or group_unit_from_title(live.title) != unit):
                continue
            if vehicle_unit_from_name(vehicles[vehicle_id].get("name")) != unit:
                continue
            try:
                with get_connection() as conn:
                    previous = conn.execute(
                        "SELECT vehicle_id FROM samsara_group_links WHERE chat_id=?", (chat_id,)
                    ).fetchone()
                    used = conn.execute(
                        "SELECT chat_id FROM samsara_group_links WHERE vehicle_id=?", (vehicle_id,)
                    ).fetchone()
                    registered = conn.execute(
                        "SELECT 1 FROM groups WHERE chat_id=?", (chat_id,)
                    ).fetchone()
                    if previous or used or not registered:
                        continue
                    now = datetime.now(timezone.utc).isoformat()
                    conn.execute(
                        "INSERT INTO samsara_group_links(chat_id,vehicle_id,linked_at,linked_by) VALUES (?,?,?,?)",
                        (chat_id, vehicle_id, now, 0),
                    )
                    conn.execute(
                        "INSERT INTO samsara_auto_events(created_at,unit,chat_id,vehicle_id,status) VALUES (?,?,?,?,?)",
                        (now, unit, chat_id, vehicle_id, "linked"),
                    )
            except sqlite3.IntegrityError:
                logger.info("Auto-link skipped due to simultaneous mapping change (unit %s)", unit)
                continue
            result["new"].append((unit, chat_id, live.title, vehicle_id))
            logger.info("Auto-linked Samsara unit %s to verified Telegram group %s", unit, chat_id)
        app.bot_data["auto_match_last_run"] = datetime.now(timezone.utc).isoformat()
        app.bot_data["auto_match_last_result"] = {
            "new": len(result["new"]), "retired_inactive": len(result["retired_inactive"]),
            "ambiguous": len(result["ambiguous"]),
            "missing": len(result["missing"]),
            "unverified_groups": unverified_group_count,
            "blocked_units": len(blocked_units), "error": result["error"]
        }
    if (result["new"] or result["retired_inactive"]) and AUTO_ONBOARD_ADMIN_NOTICES:
        lines = ["SAMSARA AUTO-LINK | GROUP STATUS CHANGES", ""]
        lines.append(f"Inactive links retired: {len(result['retired_inactive'])}")
        lines.append(f"New active links created: {len(result['new'])}")
        lines.append("")
        lines.extend(f"Unit {unit}: {title} (group {chat_id})" for unit, chat_id, title, _ in result["new"])
        lines.append("Weather delivery to driver groups remains OFF. GPS freshness checked only during previews.")
        message = "\n".join(lines)
        for admin_id in ADMIN_USER_IDS:
            try:
                await app.bot.send_message(chat_id=admin_id, text=message[:3900])
            except TelegramError:
                logger.warning("Unable to deliver onboarding notice to admin %s", admin_id)
    return result


async def samsara_auto_loop(app):
    while True:
        try:
            await run_auto_onboarding(app)
        except Exception as exc:
            logger.warning("Automatic onboarding cycle failed: %s", type(exc).__name__)
        await asyncio.sleep(SAMSARA_AUTO_SCAN_SECONDS)


async def samsaraautostatus_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not samsara_private_admin(update):
        return
    with get_connection() as conn:
        total = conn.execute("SELECT COUNT(*) FROM samsara_group_links").fetchone()[0]
        automatic = conn.execute("SELECT COUNT(*) FROM samsara_auto_events WHERE status='linked'").fetchone()[0]
    last = context.application.bot_data.get("auto_match_last_result", {})
    await update.effective_message.reply_text(
        f"Automatic onboarding: {'ON' if SAMSARA_AUTO_ONBOARD_ENABLED else 'OFF'}\n"
        f"Check interval: {SAMSARA_AUTO_SCAN_SECONDS // 60} minutes\n"
        f"Total linked groups: {total}\n"
        f"Links created automatically: {automatic}\n"
        f"Last scan: {context.application.bot_data.get('auto_match_last_run', 'Not yet completed')}\n"
        f"Last result: {last}\n"
        "Driver weather delivery: OFF (private preview only)."
    )


async def samsaraautoscan_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not samsara_private_admin(update):
        return
    await update.effective_message.reply_text("Checking new registered driver groups against Samsara...")
    result = await run_auto_onboarding(context.application)
    await update.effective_message.reply_text(
        f"New verified active links: {len(result['new'])}\n"
        f"Inactive group links retired: {len(result['retired_inactive'])}\n"
        f"Unverified Telegram groups: {result.get('unverified_groups', 0)}\n"
        f"Units held for verification: {len(result.get('blocked_units', []))}\n"
        f"Ambiguous unit numbers: {len(result['ambiguous'])}\n"
        f"Missing Samsara units: {len(result['missing'])}\n"
        f"Status: {result['error'] or 'Complete'}\n"
        "No weather alerts sent to driver groups."
    )


async def samsaramatchpreview_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Private, read-only match discovery. Stores proposals, never creates links."""
    if not samsara_private_admin(update):
        return
    vehicles = await samsara_snapshot_or_error(update)
    if vehicles is None:
        return
    with get_connection() as conn:
        groups = conn.execute("SELECT chat_id, group_name FROM groups").fetchall()
        linked = conn.execute("SELECT chat_id, vehicle_id FROM samsara_group_links").fetchall()
    matches, ambiguous, inactive_count = unique_match_candidates(
        groups, vehicles, {r[0] for r in linked}, {r[1] for r in linked}
    )
    # Verify the *current* Telegram title before offering a potential match.
    verified = []
    not_verified = 0
    for unit, chat_id, title, vehicle_id in matches:
        live = await get_live_group(chat_id, context)
        if not live or live.type not in (ChatType.GROUP, ChatType.SUPERGROUP) or group_unit_from_title(live.title) != unit:
            not_verified += 1
            continue
        verified.append((unit, chat_id, live.title, vehicle_id))
    now = datetime.now(timezone.utc).isoformat()
    with get_connection() as conn:
        conn.execute("DELETE FROM samsara_match_reviews WHERE admin_id=?", (update.effective_user.id,))
        conn.executemany(
            "INSERT INTO samsara_match_reviews (admin_id, unit, chat_id, vehicle_id, reviewed_at) VALUES (?, ?, ?, ?, ?)",
            [(update.effective_user.id, unit, chat_id, vid, now) for unit, chat_id, _, vid in verified]
        )
    if not verified:
        await update.effective_message.reply_text(
            f"No unambiguous new matches. Ambiguous unit numbers: {len(ambiguous)}. "
            f"Unverified groups: {not_verified}. Inactive skipped: {inactive_count}. "
            "Existing links remain unchanged."
        )
        return
    pagesize = 12
    for i in range(0, len(verified), pagesize):
        lines = ["SAMSARA MATCH PREVIEW - NO LINKS SAVED"]
        if i == 0:
            lines.append(f"Proposed: {len(verified)} | Ambiguous: {len(ambiguous)} | Unverified: {not_verified} | Inactive skipped: {inactive_count}")
        for unit, chat_id, title, vid in verified[i:i+pagesize]:
            lines.append(f"Unit {unit}: {title[:40]}\nGroup {chat_id} | Vehicle {vid}")
        lines.append("Confirm ONE unit privately with: /samsaramatchconfirm 111")
        await update.effective_message.reply_text("\n\n".join(lines))
    if ambiguous:
        await update.effective_message.reply_text(
            "Ambiguous unit numbers (manual review only): " + ", ".join(ambiguous[:30])
        )


async def samsaramatchconfirm_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Confirm one previously reviewed match, rechecking every input live."""
    if not samsara_private_admin(update):
        return
    if len(context.args) != 1 or not VEHICLE_UNIT_RE.fullmatch(context.args[0]):
        await update.effective_message.reply_text("Usage: /samsaramatchconfirm UNIT_NUMBER (after /samsaramatchpreview)")
        return
    unit = context.args[0].upper()
    with get_connection() as conn:
        proposal = conn.execute(
            "SELECT chat_id, vehicle_id, reviewed_at FROM samsara_match_reviews WHERE admin_id=? AND unit=?",
            (update.effective_user.id, unit),
        ).fetchone()
    if not proposal:
        await update.effective_message.reply_text("No reviewed match for that unit. Run /samsaramatchpreview first.")
        return
    chat_id, vehicle_id, reviewed_at = proposal
    proposed_at = weather_parse_time(reviewed_at)
    if not proposed_at or datetime.now(timezone.utc) - proposed_at > timedelta(minutes=MATCH_REVIEW_TTL_MINUTES):
        await update.effective_message.reply_text("Match review expired. Run /samsaramatchpreview again.")
        return
    live = await get_live_group(chat_id, context)
    if not live or live.type not in (ChatType.GROUP, ChatType.SUPERGROUP) or group_unit_from_title(live.title) != unit:
        await update.effective_message.reply_text("Group title changed or could not be verified. Nothing linked.")
        return
    vehicles = await samsara_snapshot_or_error(update)
    if vehicles is None:
        return
    if vehicle_id not in vehicles or vehicle_unit_from_name(vehicles[vehicle_id].get("name")) != unit:
        await update.effective_message.reply_text("Samsara vehicle name changed or missing. Nothing linked.")
        return
    with get_connection() as conn:
        row = conn.execute("SELECT 1 FROM groups WHERE chat_id=?", (chat_id,)).fetchone()
        existing_group = conn.execute("SELECT vehicle_id FROM samsara_group_links WHERE chat_id=?", (chat_id,)).fetchone()
        existing_vehicle = conn.execute("SELECT chat_id FROM samsara_group_links WHERE vehicle_id=?", (vehicle_id,)).fetchone()
        if not row or existing_group or existing_vehicle:
            await update.effective_message.reply_text("Group or vehicle already linked, or registration missing. Nothing changed.")
            return
        conn.execute(
            "INSERT INTO samsara_group_links (chat_id, vehicle_id, linked_at, linked_by) VALUES (?, ?, ?, ?)",
            (chat_id, vehicle_id, datetime.now(timezone.utc).isoformat(), update.effective_user.id)
        )
        conn.execute("DELETE FROM samsara_match_reviews WHERE unit=?", (unit,))
    await update.effective_message.reply_text(
        f"Verified and linked unit {unit}.\nGroup: {live.title}\nVehicle ID: {vehicle_id}\n"
        f"Next private GPS check: /samsarapreview {chat_id}\n"
        "Automatic weather messages remain OFF."
    )


async def samsaralink_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Private linking by exact chat ID + exact Samsara vehicle ID; avoid bad guesses."""
    if not samsara_private_admin(update):
        return
    if len(context.args) != 2:
        await update.effective_message.reply_text("Usage: /samsaralink GROUP_CHAT_ID SAMSARA_VEHICLE_ID")
        return
    try:
        chat_id = int(context.args[0])
    except ValueError:
        await update.effective_message.reply_text("Invalid Telegram group ID.")
        return
    vehicle_id = context.args[1].strip()
    live = await get_live_group(chat_id, context)
    if not live or live.type not in (ChatType.GROUP, ChatType.SUPERGROUP) or not live.title or is_inactive_group_name(live.title):
        await update.effective_message.reply_text("Link not saved: active group could not be verified.")
        return
    with get_connection() as conn:
        registered = conn.execute("SELECT 1 FROM groups WHERE chat_id=?", (chat_id,)).fetchone()
    if not registered:
        await update.effective_message.reply_text("Group not registered in BUK Speaker; use /addgroup first.")
        return
    vehicles = await samsara_snapshot_or_error(update)
    if vehicles is None:
        return
    if vehicle_id not in vehicles:
        await update.effective_message.reply_text("Vehicle ID not found in Samsara. No link saved.")
        return
    with get_connection() as conn:
        # Prevent one vehicle ID from silently attaching to multiple driver groups.
        existing = conn.execute("SELECT chat_id FROM samsara_group_links WHERE vehicle_id=?", (vehicle_id,)).fetchone()
        if existing and existing[0] != chat_id:
            await update.effective_message.reply_text("Vehicle is already linked to a different group; unlink it first.")
            return
        conn.execute("""INSERT INTO samsara_group_links (chat_id, vehicle_id, linked_at, linked_by)
            VALUES (?, ?, ?, ?) ON CONFLICT(chat_id) DO UPDATE SET vehicle_id=excluded.vehicle_id,
            linked_at=excluded.linked_at, linked_by=excluded.linked_by""",
            (chat_id, vehicle_id, datetime.now(timezone.utc).isoformat(), update.effective_user.id))
    await update.effective_message.reply_text(
        f"Linked group: {live.title}\n"
        f"Samsara vehicle: {vehicles[vehicle_id].get('name', 'Unnamed')} (ID {vehicle_id})\n"
        "This link enables PRIVATE GPS/weather previews only; no group weather alerts."
    )


async def samsaraunlink_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not samsara_private_admin(update):
        return
    if len(context.args) != 1:
        await update.effective_message.reply_text("Usage: /samsaraunlink GROUP_CHAT_ID")
        return
    try:
        chat_id = int(context.args[0])
    except ValueError:
        await update.effective_message.reply_text("Invalid group ID")
        return
    with get_connection() as conn:
        deleted = conn.execute("DELETE FROM samsara_group_links WHERE chat_id=?", (chat_id,)).rowcount
    await update.effective_message.reply_text("Group link removed." if deleted else "No link found for that group.")


async def samsarapreview_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin-only, real GPS -> actual NWS alerts. NEVER messages the linked group."""
    if not samsara_private_admin(update):
        return
    if len(context.args) != 1:
        await update.effective_message.reply_text("Usage: /samsarapreview GROUP_CHAT_ID")
        return
    try:
        chat_id = int(context.args[0])
    except ValueError:
        await update.effective_message.reply_text("Invalid group ID")
        return
    with get_connection() as conn:
        link = conn.execute("SELECT vehicle_id FROM samsara_group_links WHERE chat_id=?", (chat_id,)).fetchone()
    if not link:
        await update.effective_message.reply_text("No verified vehicle link for this group.")
        return
    live = await get_live_group(chat_id, context)
    if not live or not live.title or is_inactive_group_name(live.title):
        await update.effective_message.reply_text("Inactive or unverified group; skipped.")
        return
    data = await samsara_snapshot_or_error(update)
    if data is None:
        return
    vehicle = data.get(link[0])
    if not vehicle:
        await update.effective_message.reply_text("Linked vehicle missing from Samsara snapshot. Skipped.")
        return
    gps = samsara_valid_gps(vehicle.get("gps"))
    if gps is None:
        await update.effective_message.reply_text("GPS is stale, missing, or outside supported area. Skipped.")
        return
    lat, lon, timestamp = gps
    await update.effective_message.reply_text(
        f"PRIVATE WEATHER PREVIEW\n"
        f"Group: {live.title}\n"
        f"Truck: {vehicle.get('name') or link[0]}\n"
        f"GPS timestamp (UTC): {timestamp:%Y-%m-%d %H:%M}\n"
        "No driver group messages will be sent."
    )
    try:
        features = await asyncio.to_thread(nws_get_active_point, lat, lon)
    except Exception:
        logger.warning("NWS preview request failed; withheld", exc_info=False)
        await update.effective_message.reply_text("NWS currently unavailable. No alert sent.")
        return
    valid = [item for feature in features if (item := weather_active_record(feature)) is not None]
    if not valid:
        await update.effective_message.reply_text("No qualifying active NWS warnings at this truck location right now.")
        return
    try:
        point_timezone = await asyncio.to_thread(nws_get_point_timezone, lat, lon)
    except Exception:
        logger.warning("NWS point time zone unavailable; using UTC in private preview")
        point_timezone = None
    for item in valid[:5]:
        for message in weather_message_parts(item, unit=vehicle.get("name") or link[0],
                                             timezone_name=point_timezone):
            await update.effective_message.reply_text(message, parse_mode="HTML")
    if len(valid) > 5:
        await update.effective_message.reply_text(f"{len(valid)-5} additional warnings not shown.")


# WEATHER PHASE 1: Official NWS point alerts only. No DOT, route, or Samsara
# claims are made here. Driver delivery is OFF until explicitly enabled.
# Two independent switches prevent accidental production notifications on upgrade.
WEATHER_REQUESTED = os.getenv("WEATHER_ALERTS_ENABLED", "false").lower() == "true"
WEATHER_TEMPLATES_APPROVED = os.getenv("WEATHER_TEMPLATES_APPROVED", "false").lower() == "true"
# v11 testing gate: do not send automated weather to any group, even if old
# environment flags were mistakenly left enabled. Later release must explicitly
# replace this hard gate after admin approval and Samsara mapping validation.
WEATHER_ENABLED = False
WEATHER_POLL_SECONDS = max(120, int(os.getenv("WEATHER_POLL_SECONDS", "180")))
# A manually supplied monitoring point expires quickly; it is NOT live GPS.
WEATHER_POINT_TTL_HOURS = 6
NWS_USER_AGENT = os.getenv("NWS_USER_AGENT", "BUKSpeakerWeather/1.0 (operations@example.com)")
WEATHER_EVENTS = frozenset({
    "Tornado Warning", "Flash Flood Warning", "Severe Thunderstorm Warning",
    "Blizzard Warning", "Winter Storm Warning", "Ice Storm Warning",
    "Snow Squall Warning", "High Wind Warning", "Extreme Wind Warning",
    "Dust Storm Warning", "Flood Warning", "Hurricane Warning",
    "Tropical Storm Warning", "Red Flag Warning",
    # Only official NWS advisories with a clear, verifiable hazard.
    "Winter Weather Advisory", "Wind Advisory", "Dense Fog Advisory",
    "Freezing Fog Advisory", "Flood Advisory", "Heat Advisory",
    "Cold Weather Advisory", "Extreme Cold Warning", "Extreme Heat Warning",
})

GENERAL_ADVISORY_EVENTS = frozenset({
    "Winter Weather Advisory", "Wind Advisory", "Dense Fog Advisory",
    "Freezing Fog Advisory", "Flood Advisory", "Heat Advisory",
    "Cold Weather Advisory",
})

# Driver messages use only event-specific, conservative guidance.
EVENT_ACTIONS = {
    "Tornado Warning": "Seek sturdy indoor shelter immediately. Do not shelter beneath an overpass.",
    "Flash Flood Warning": "Do not drive through floodwater. Find a safe route or stop and contact Dispatch.",
    "Blizzard Warning": "Avoid travel in whiteout conditions. Stop at a safe location if conditions are unsafe.",
    "Winter Storm Warning": "Check road conditions and stop safely if visibility or traction becomes unsafe.",
    "Ice Storm Warning": "Avoid unnecessary travel and use extreme caution on icy roads.",
    "Snow Squall Warning": "Avoid entering the affected roadway during sudden whiteout conditions.",
    "High Wind Warning": "Use caution in crosswinds, especially with an empty or lightly loaded trailer.",
    "Severe Thunderstorm Warning": "Watch for damaging winds and hail; stop safely if conditions become hazardous.",
    "Dust Storm Warning": "If visibility collapses, leave the roadway safely; avoid stopping in travel lanes.",
    "Winter Weather Advisory": "Reduce speed and allow extra stopping distance if roads become slick.",
    "Wind Advisory": "Use caution on exposed roads, especially with high-profile or empty trailers.",
    "Dense Fog Advisory": "Slow down, increase following distance, and use appropriate lights.",
    "Freezing Fog Advisory": "Watch for slick bridges and reduced visibility; adjust speed accordingly.",
    "Flood Advisory": "Avoid flooded roads. Do not drive through standing water.",
    "Heat Advisory": "Stay hydrated and monitor for heat-related illness during stops and inspections.",
    "Cold Weather Advisory": "Use appropriate cold-weather precautions during stops and inspections.",
    "Extreme Cold Warning": "Limit outdoor exposure and use appropriate cold-weather precautions.",
    "Extreme Heat Warning": "Limit outdoor heat exposure and monitor for signs of heat illness.",
    "Red Flag Warning": "Avoid parking on dry vegetation; follow local fire restrictions and report smoke or fire.",
    "Flood Warning": "Do not drive through flooded roads. Check road closures before proceeding.",
    "Extreme Wind Warning": "Avoid travel in dangerous winds and seek safe shelter as instructed by authorities.",
    "Hurricane Warning": "Avoid affected routes; follow official evacuation and road closure instructions.",
    "Tropical Storm Warning": "Monitor official road closures and stop safely if wind or flooding becomes hazardous.",
}

# These are illustrative templates for private admin preview; never represented as
# a live, verified warning or automatically sent on their own.
PREVIEW_EXAMPLES = {
    "snow": ("GENERAL WEATHER REMINDER", "Snow", "Snow may reduce traction and visibility.",
             "Reduce speed, increase following distance, and avoid sudden braking."),
    "rain": ("GENERAL WEATHER REMINDER", "Rain", "Wet roads may increase stopping distances.",
             "Adjust speed and following distance; avoid sudden steering or braking."),
    "ice": ("GENERAL WEATHER REMINDER", "Ice", "Icy conditions may affect bridges and overpasses.",
            "Use caution, avoid sudden maneuvers, and stop safely if needed."),
    "fog": ("GENERAL WEATHER REMINDER", "Fog", "Reduced visibility may affect road safety.",
            "Slow down, increase following distance, and use appropriate lights."),
    "wind": ("GENERAL WEATHER REMINDER", "Wind", "Crosswinds may affect high-profile vehicles.",
             "Use caution, especially with empty or lightly loaded trailers."),
    "tornado": ("URGENT WEATHER ALERT", "Tornado Warning", "Official tornado warning affects your location.",
                EVENT_ACTIONS["Tornado Warning"]),
}



def weather_parse_time(value):
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def weather_active_record(feature, now=None):
    """Validate live, actual NWS warning; do not infer hazards or severity."""
    now = now or datetime.now(timezone.utc)
    props = feature.get("properties", {})
    if not isinstance(props, dict):
        return None
    if props.get("status", "").lower() != "actual":
        return None
    if props.get("messageType", "").lower() == "cancel":
        return None
    event = props.get("event")
    if event not in WEATHER_EVENTS:
        return None
    # expires refers to the NWS alert product, not necessarily the hazard end.
    expires = weather_parse_time(props.get("expires"))
    starts = weather_parse_time(props.get("onset") or props.get("effective"))
    if not expires or expires <= now or not starts:
        return None
    area = props.get("areaDesc")
    alert_id = props.get("id") or feature.get("id")
    if not isinstance(area, str) or not area.strip() or not alert_id:
        return None
    return {
        "id": str(alert_id), "event": event, "area": area.strip(),
        "starts": starts, "expires": expires,
        "severity": str(props.get("severity") or "Unspecified"),
        "instruction": str(props.get("instruction") or "").strip(),
    }


def nws_get_active_point(latitude, longitude):
    """Blocking official NWS request; invoked with asyncio.to_thread."""
    query = urllib.parse.urlencode({"point": f"{latitude:.4f},{longitude:.4f}"})
    url = f"https://api.weather.gov/alerts/active?{query}"
    request = urllib.request.Request(url, headers={
        "Accept": "application/geo+json", "User-Agent": NWS_USER_AGENT,
    })
    with urllib.request.urlopen(request, timeout=18) as response:
        data = json.load(response)
    if not isinstance(data, dict) or not isinstance(data.get("features"), list):
        raise ValueError("Unexpected NWS response; do not broadcast")
    return data["features"]


# Telegram-native Unicode emojis and HTML bold; no external sticker files.
WEATHER_EMOJI = {
    "Tornado Warning": "🌪️", "Flash Flood Warning": "🌊",
    "Blizzard Warning": "❄️", "Winter Storm Warning": "❄️",
    "Ice Storm Warning": "🧊", "Snow Squall Warning": "🌨️",
    "High Wind Warning": "💨", "Extreme Wind Warning": "💨",
    "Severe Thunderstorm Warning": "⛈️", "Dust Storm Warning": "🌫️",
    "Winter Weather Advisory": "❄️", "Wind Advisory": "💨",
    "Dense Fog Advisory": "🌫️", "Freezing Fog Advisory": "🌫️",
    "Flood Advisory": "🌧️", "Heat Advisory": "🌡️",
    "Cold Weather Advisory": "🥶", "Extreme Cold Warning": "🥶",
    "Extreme Heat Warning": "🌡️", "Flood Warning": "🌊",
    "Hurricane Warning": "🌀", "Tropical Storm Warning": "🌀",
    "Red Flag Warning": "🔥",
}
PREVIEW_EMOJI = {"snow": "❄️", "rain": "🌧️", "ice": "🧊",
                 "fog": "🌫️", "wind": "💨", "tornado": "🌪️"}


def weather_heading(event, general=False):
    if event in {"Tornado Warning", "Flash Flood Warning", "Snow Squall Warning"}:
        return "🚨 URGENT WEATHER ALERT"
    if general:
        return "🌤️ WEATHER ADVISORY"
    return "⚠️ SEVERE WEATHER ALERT"


def nws_get_point_timezone(latitude, longitude):
    """Get the official IANA time zone for the truck's actual GPS point.

    Failure is safe: the formatter explicitly falls back to UTC rather than
    guessing an offset from the state or alert coverage area.
    """
    url = f"https://api.weather.gov/points/{latitude:.4f},{longitude:.4f}"
    request = urllib.request.Request(url, headers={
        "Accept": "application/geo+json", "User-Agent": NWS_USER_AGENT,
    })
    with urllib.request.urlopen(request, timeout=18) as response:
        result = json.load(response)
    name = (result.get("properties") or {}).get("timeZone")
    if not isinstance(name, str) or not name:
        return None
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return None
    return name


def weather_expiration_label(value, timezone_name=None):
    """Convert an NWS UTC expiry to the verified point's local time zone."""
    if timezone_name:
        try:
            local = value.astimezone(ZoneInfo(timezone_name))
            return local.strftime("%b %d, %I:%M %p %Z")
        except (ZoneInfoNotFoundError, ValueError):
            pass
    return value.astimezone(timezone.utc).strftime("%b %d, %I:%M %p UTC")


def weather_message_parts(info, unit=None, timezone_name=None):
    """Complete NWS region, no omitted zones, with Telegram-safe pagination.

    No unit is inferred from an NWS region. A unit is included only when
    verified against an explicitly linked Samsara vehicle.
    """
    event = info["event"]
    title = weather_heading(event, event in GENERAL_ADVISORY_EVENTS)
    icon = WEATHER_EMOJI.get(event, "🔎")
    if icon in title:
        icon = "🔎"
    header = [f"<b>{title}</b>"]
    if unit is not None:
        header.append(f"🚛 <b>Alert For:</b> Unit {html.escape(str(unit))}")
    header.append(f"{icon} <b>Hazard:</b> {html.escape(event)}")
    header.append(f"🕒 <b>Expires:</b> {weather_expiration_label(info['expires'], timezone_name)}")
    action = EVENT_ACTIONS.get(event)
    if action:
        header.append(f"⚠️ <b>Driver Action:</b> {html.escape(action)}")
    header.append("<i>Source: National Weather Service</i>")
    # Do not summarize, trim, or substitute an inferred county for the NWS text.
    area = info["area"].strip()
    full = header[:3] + [f"📍 <b>Full Affected Region:</b> {html.escape(area)}"] + header[3:]
    combined = "\n\n".join(full)
    if len(combined) <= 3800:
        return [combined]

    # Long NWS region lists are sent as continuation parts, preserving EVERY
    # character. HTML-escape after splitting the original text to avoid cuts
    # through HTML entities. Keep each Telegram message well below 4096.
    first = "\n\n".join(header + ["📍 <b>Full Affected Region:</b> See following region message(s)."]) 
    parts = []
    remaining = area
    while remaining:
        length = min(1800, len(remaining))
        if length < len(remaining):
            boundary = remaining.rfind(";", 0, length)
            if boundary > length // 2:
                length = boundary + 1
        segment, remaining = remaining[:length], remaining[length:]
        parts.append(segment)
    result = [first]
    for index, part in enumerate(parts, 1):
        result.append(f"📍 <b>Full Affected Region ({index}/{len(parts)}):</b>\n\n{html.escape(part)}")
    return result


def weather_message(info, unit=None, timezone_name=None):
    """Single message for ordinary regions; multi-part alerts use parts helper."""
    return weather_message_parts(info, unit, timezone_name)[0]


# Curated icons: each sample uses distinct symbols for title, condition and
# driver action. These are Unicode Telegram emojis, not external sticker files.
PREVIEW_VISUALS = {
    "snow": ("❄️ SNOW CONDITIONS ADVISORY", "🌨️", "🚛"),
    "rain": ("🌧️ RAIN CONDITIONS ADVISORY", "💦", "🛞"),
    "ice": ("🧊 ICY ROAD ADVISORY", "🛣️", "⚠️"),
    "fog": ("🌫️ LOW VISIBILITY ADVISORY", "👁️", "💡"),
    "wind": ("💨 WIND CONDITIONS ADVISORY", "🌬️", "🚚"),
    "tornado": ("🚨 URGENT WEATHER ALERT", "🌪️", "🏠"),
}


def weather_preview_message(kind):
    """Private examples only, always prominently labeled as non-live."""
    _, event, condition, action = PREVIEW_EXAMPLES[kind]
    heading, condition_icon, action_icon = PREVIEW_VISUALS[kind]
    lines = [
        "🧪 <b>SAMPLE - NOT A LIVE ALERT</b>",
        f"<b>{heading}</b>",
        f"{condition_icon} <b>Conditions:</b> {html.escape(condition)}",
        f"{action_icon} <b>Driver Action:</b> {html.escape(action)}",
        "<i>Preview for administrator approval only</i>",
    ]
    return "\n\n".join(lines)


async def weatherpreview_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Administrator-only private template review; never group broadcasts."""
    if not is_admin(update) or update.effective_chat.type != ChatType.PRIVATE:
        return
    kind = context.args[0].lower() if context.args else "all"
    if kind != "all" and kind not in PREVIEW_EXAMPLES:
        await update.effective_message.reply_text(
            "Usage: /weatherpreview [all|snow|rain|ice|fog|wind|tornado]")
        return
    keys = list(PREVIEW_EXAMPLES) if kind == "all" else [kind]
    for name in keys:
        await update.effective_message.reply_text(
            weather_preview_message(name), parse_mode="HTML",
            disable_web_page_preview=True)


async def weathercheck_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Fetch REAL current NWS alerts for a supplied point, admin PRIVATE preview only.

    Never sends anything to a driver group and never saves a monitoring point.
    """
    if not is_admin(update) or update.effective_chat.type != ChatType.PRIVATE:
        return
    if len(context.args) != 2:
        await update.effective_message.reply_text("Usage: /weathercheck LATITUDE LONGITUDE (private preview only)")
        return
    try:
        lat, lon = [float(x) for x in context.args]
        if not 18 <= lat <= 72 or not -180 <= lon <= -60:
            raise ValueError("Out of supported US bounds")
    except ValueError:
        await update.effective_message.reply_text("Invalid coordinates. Nothing sent.")
        return
    try:
        features = await asyncio.to_thread(nws_get_active_point, lat, lon)
    except Exception:
        logger.exception("NWS preview lookup failed")
        await update.effective_message.reply_text("NWS data unavailable. No alert sent.")
        return
    items = [item for feature in features
             if (item := weather_active_record(feature)) is not None]
    if not items:
        await update.effective_message.reply_text(
            "No qualifying active NWS alerts for this point right now. No group messages sent.")
        return
    await update.effective_message.reply_text(
        f"PRIVATE PREVIEW | {len(items)} qualifying active NWS alert(s). Nothing sent to groups.")
    try:
        point_timezone = await asyncio.to_thread(nws_get_point_timezone, lat, lon)
    except Exception:
        logger.warning("NWS point time zone unavailable; using UTC in private preview")
        point_timezone = None
    for item in items[:8]:
        for message in weather_message_parts(item, timezone_name=point_timezone):
            await update.effective_message.reply_text(
                message, parse_mode="HTML", disable_web_page_preview=True)
    if len(items) > 8:
        await update.effective_message.reply_text(f"{len(items)-8} additional alerts omitted from preview.")


def weather_location_recent(assigned_at, now):
    assigned = weather_parse_time(assigned_at)
    return bool(assigned and assigned <= now and now - assigned < timedelta(hours=WEATHER_POINT_TTL_HOURS))


async def weatherpoint_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin explicitly assigns a TEMPORARY monitoring coordinate to one group.

    This is NOT a truck location integration and must be updated when the truck moves.
    """
    if not is_admin(update) or not is_group_chat(update):
        await update.effective_message.reply_text("Use /weatherpoint in a driver group as an authorized BUK Speaker admin.")
        return
    if len(context.args) != 2:
        await update.effective_message.reply_text("Usage: /weatherpoint LATITUDE LONGITUDE (valid for 6 hours)")
        return
    try:
        lat, lon = [float(x) for x in context.args]
        if not 18 <= lat <= 72 or not -180 <= lon <= -60:
            raise ValueError("Outside supported US monitoring bounds")
    except ValueError:
        await update.effective_message.reply_text("Invalid US latitude/longitude. No monitoring location saved.")
        return
    chat = await get_live_group(update.effective_chat.id, context)
    if not chat or not chat.title or is_inactive_group_name(chat.title):
        await update.effective_message.reply_text("Location not saved: active group could not be verified.")
        return
    with get_connection() as conn:
        conn.execute("""INSERT INTO weather_locations (chat_id, latitude, longitude, assigned_at, assigned_by)
            VALUES (?, ?, ?, ?, ?) ON CONFLICT(chat_id) DO UPDATE SET
            latitude=excluded.latitude, longitude=excluded.longitude,
            assigned_at=excluded.assigned_at, assigned_by=excluded.assigned_by""",
            (chat.id, lat, lon, datetime.now(timezone.utc).isoformat(), update.effective_user.id))
    await update.effective_message.reply_text(
        "Temporary NWS monitoring point saved for 6 hours. This is a manually assigned point, "
        "not live truck GPS. No alerts will be sent until BOTH weather approval switches are enabled."
    )


async def weatheroff_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update) or not is_group_chat(update):
        return
    with get_connection() as conn:
        conn.execute("DELETE FROM weather_locations WHERE chat_id=?", (update.effective_chat.id,))
    await update.effective_message.reply_text("Weather monitoring disabled for this group.")


async def weatherstatus_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    with get_connection() as conn:
        rows = conn.execute("SELECT chat_id, assigned_at FROM weather_locations").fetchall()
    now = datetime.now(timezone.utc)
    valid = sum(weather_location_recent(item[1], now) for item in rows)
    await update.effective_message.reply_text(
        f"NWS weather engine: {'ON' if WEATHER_ENABLED else 'OFF (approval required)'}\n"
        f"Manually assigned monitoring points: {len(rows)}\n"
        f"Points within 6-hour validity: {valid}\n"
        "Automatic group alerts require WEATHER_ALERTS_ENABLED=true AND "
        "WEATHER_TEMPLATES_APPROVED=true. Rain/snow general reminders are preview-only; "
        "official NWS advisories can be automated once approved. "
        "DOT, Samsara, and routes are not connected."
    )


async def weather_scan(context):
    """No group messaging unless enabled, location fresh, verified live active title.

    Fail closed on data/API errors. Only send NWS actual active warnings to
    deliberately configured, non-stale group points. Dedup persisted in SQLite.
    """
    if not WEATHER_ENABLED:
        return
    now = datetime.now(timezone.utc)
    with get_connection() as conn:
        points = conn.execute("""SELECT g.chat_id, w.latitude, w.longitude, w.assigned_at
            FROM groups g JOIN weather_locations w ON w.chat_id=g.chat_id""").fetchall()
    # One fetch per distinct coordinate to reduce official endpoint load.
    fetched = {}
    for chat_id, lat, lon, assigned_at in points:
        if not weather_location_recent(assigned_at, now):
            continue
        live = await get_live_group(chat_id, context)
        if live is None or not live.title or is_inactive_group_name(live.title):
            continue
        key = (round(lat, 4), round(lon, 4))
        if key not in fetched:
            try:
                fetched[key] = await asyncio.to_thread(nws_get_active_point, *key)
            except Exception:
                logger.exception("NWS request failed for monitored coordinate; group alert skipped")
                fetched[key] = []
        for feature in fetched[key]:
            item = weather_active_record(feature, now)
            if not item:
                continue
            with get_connection() as conn:
                delivered = conn.execute(
                    "SELECT 1 FROM weather_deliveries WHERE chat_id=? AND alert_id=?",
                    (chat_id, item["id"])).fetchone()
            if delivered:
                continue
            # General advisories: never repeat the same hazard for a group
            # more often than every 12 hours, even if NWS refreshes its ID.
            # New severe warnings are not silenced by advisory cooldowns.
            if item["event"] in GENERAL_ADVISORY_EVENTS:
                with get_connection() as conn:
                    cooldown = conn.execute(
                        "SELECT sent_at FROM weather_advisory_cooldown WHERE chat_id=? AND event=?",
                        (chat_id, item["event"])).fetchone()
                if cooldown:
                    prior = weather_parse_time(cooldown[0])
                    if prior and now - prior < timedelta(hours=12):
                        continue
            # Last check immediately before sending in case admins renamed group.
            latest = await get_live_group(chat_id, context)
            if latest is None or not latest.title or is_inactive_group_name(latest.title):
                break
            try:
                # This path remains disabled in v14. If a later release enables
                # it, preserve the full NWS area instead of truncating it.
                for message in weather_message_parts(item):
                    await context.bot.send_message(chat_id=chat_id, text=message,
                                                   parse_mode="HTML", disable_web_page_preview=True)
            except TelegramError:
                logger.exception("NWS alert delivery failed for chat %s", chat_id)
                continue
            with get_connection() as conn:
                conn.execute("INSERT OR IGNORE INTO weather_deliveries (chat_id, alert_id, sent_at) VALUES (?, ?, ?)",
                             (chat_id, item["id"], now.isoformat()))
                if item["event"] in GENERAL_ADVISORY_EVENTS:
                    conn.execute("""INSERT INTO weather_advisory_cooldown (chat_id, event, sent_at)
                        VALUES (?, ?, ?) ON CONFLICT(chat_id, event) DO UPDATE SET
                        sent_at=excluded.sent_at""",
                        (chat_id, item["event"], now.isoformat()))
            await asyncio.sleep(0.07)


# V16: PRIVATE administrator monitoring of verified Samsara truck locations.
# This is INDEPENDENT from the old, hard-disabled driver weather delivery path.
# The private automatic monitor is opt-in via FadeHost; manual /weatheradminscan
# is also private, and can be tested before enabling scheduled scans.
WEATHER_ADMIN_MONITOR_ENABLED = os.getenv("WEATHER_ADMIN_MONITOR_ENABLED", "false").lower() == "true"
WEATHER_ADMIN_SCAN_SECONDS = max(300, int(os.getenv("WEATHER_ADMIN_SCAN_SECONDS", "600")))
WEATHER_ADMIN_MAX_GROUPS_PER_SCAN = max(1, min(100, int(os.getenv("WEATHER_ADMIN_MAX_GROUPS_PER_SCAN", "60"))))
# Admin-only GPS health alerts piggyback on verified weather scans (no extra polling).
GPS_HEALTH_ADMIN_ALERTS_ENABLED = os.getenv("GPS_HEALTH_ADMIN_ALERTS_ENABLED", "true").lower() == "true"
GPS_HEALTH_NOTIFY_AFTER_MINUTES = max(60, int(os.getenv("GPS_HEALTH_NOTIFY_AFTER_MINUTES", "60")))



def _recent_admin_alert(conn, chat_id, event, now):
    """Avoid noise from NWS refreshes with new IDs during an active episode.

    Different hazard events are independent. A 6-hour quiet window applies to
    non-emergency events. Critical warnings are never blocked by this cooldown;
    the exact alert ID is still deduplicated separately.
    """
    if event in {"Tornado Warning", "Flash Flood Warning", "Snow Squall Warning", "Extreme Wind Warning"}:
        return False
    row = conn.execute(
        """SELECT MAX(delivered_at) FROM weather_admin_notifications
           WHERE chat_id=? AND event=?""", (chat_id, event)
    ).fetchone()
    last = weather_parse_time(row[0]) if row and row[0] else None
    return bool(last and timedelta(0) <= now-last < timedelta(hours=6))


def gps_health_alert_due(record, issue_started_at, now):
    """Aged location (>60 min) or missing data observed continuously for 60 min.

    Do not classify missing coordinates, invalid/future timestamps or an
    unsupported area as stale hardware; those are only data-quality issues.
    """
    if not isinstance(record, dict):
        return False
    gps = record.get("gps")
    ts = weather_parse_time(gps.get("time")) if isinstance(gps, dict) else None
    if ts and timedelta(0) <= now - ts and now - ts > timedelta(minutes=GPS_HEALTH_NOTIFY_AFTER_MINUTES):
        return True
    first = weather_parse_time(issue_started_at)
    return bool(first and now - first >= timedelta(minutes=GPS_HEALTH_NOTIFY_AFTER_MINUTES))


async def gps_health_process(app, chat_id, vehicle_id, title, record, now, valid_gps):
    """Persist one GPS issue episode; send at most one alert and recovery per admin.

    Called only AFTER a live active group and its Samsara unit have matched.
    No driver-group operations occur here. One monitoring scan lock serializes calls.
    """
    outcome = {"gps_health_alerts": 0, "gps_health_recoveries": 0}
    if not GPS_HEALTH_ADMIN_ALERTS_ENABLED:
        return outcome
    now_str = now.isoformat()
    with get_connection() as conn:
        row = conn.execute(
            "SELECT vehicle_id, started_at, state FROM gps_health_episodes WHERE chat_id=?",
            (chat_id,),
        ).fetchone()
        # If a link was reassigned, never send alerts about the previous truck.
        if row and row[0] != str(vehicle_id):
            conn.execute("DELETE FROM gps_health_episodes WHERE chat_id=?", (chat_id,))
            row = None
        if valid_gps is None and row is None:
            conn.execute("""INSERT INTO gps_health_episodes
                (chat_id, vehicle_id, started_at, state, last_checked_at)
                VALUES (?,?,?,?,?)""", (chat_id, str(vehicle_id), now_str, "issue", now_str))
            row = (str(vehicle_id), now_str, "issue")
        elif row:
            state = "recovered" if valid_gps is not None else "issue"
            conn.execute(
                "UPDATE gps_health_episodes SET state=?,last_checked_at=? WHERE chat_id=?",
                (state, now_str, chat_id),
            )
    if row is None:
        return outcome
    _, episode, _ = row
    if valid_gps is None:
        if not gps_health_alert_due(record, episode, now):
            return outcome
        reason, stamp = gps_diagnostic_reason(record, now)
        message = (
            "🔒 BUK SPEAKER | GPS DATA NEEDS REVIEW\n\n"
            f"🚛 Unit: {vehicle_unit_from_name(record.get('name')) or 'Unknown'}\n"
            f"Group: {title}\n"
            f"Issue: {reason or 'GPS unavailable'}\n"
            f"Last GPS: {stamp or 'Unavailable'}\n\n"
            "This is a stale/missing location-data notice, not evidence of a device "
            "failure or an unsafe driver. Parked/offline vehicles may stop updating. "
            "Check Samsara status when practical. No alert was sent to drivers."
        )
        kind = "stale"
    else:
        # Only send recovery if an admin actually received a prior stale notice.
        message = (
            "✅ BUK SPEAKER | GPS DATA RECOVERED\n\n"
            f"🚛 Unit: {vehicle_unit_from_name(record.get('name')) or 'Unknown'}\n"
            f"Group: {title}\n"
            "A fresh usable GPS position is available again. Weather checks can resume. "
            "No message was sent to drivers."
        )
        kind = "recovered"
    for admin_id in sorted(ADMIN_USER_IDS):
        with get_connection() as conn:
            delivered = conn.execute(
                """SELECT 1 FROM gps_health_notifications
                WHERE chat_id=? AND episode_started_at=? AND admin_id=? AND kind=?""",
                (chat_id, episode, admin_id, kind),
            ).fetchone()
            had_stale = conn.execute(
                """SELECT 1 FROM gps_health_notifications
                WHERE chat_id=? AND episode_started_at=? AND admin_id=? AND kind='stale'""",
                (chat_id, episode, admin_id),
            ).fetchone()
        if delivered or (kind == "recovered" and not had_stale):
            continue
        try:
            await app.bot.send_message(chat_id=admin_id, text=message)
        except TelegramError as exc:
            logger.warning("Private GPS health notification failed (%s)", type(exc).__name__)
            continue
        with get_connection() as conn:
            conn.execute(
                """INSERT OR IGNORE INTO gps_health_notifications
                   (chat_id,episode_started_at,admin_id,kind,delivered_at)
                   VALUES (?,?,?,?,?)""",
                (chat_id, episode, admin_id, kind, now_str),
            )
        outcome["gps_health_alerts" if kind == "stale" else "gps_health_recoveries"] += 1
    if valid_gps is not None:
        # Once recovery has reached each previously-alerted admin, close episode.
        with get_connection() as conn:
            outstanding = conn.execute(
                """SELECT COUNT(*) FROM gps_health_notifications n
                   WHERE n.chat_id=? AND n.episode_started_at=? AND n.kind='stale'
                     AND NOT EXISTS (
                       SELECT 1 FROM gps_health_notifications r
                       WHERE r.chat_id=n.chat_id AND r.episode_started_at=n.episode_started_at
                         AND r.admin_id=n.admin_id AND r.kind='recovered')""",
                (chat_id, episode),
            ).fetchone()[0]
            if outstanding == 0:
                conn.execute("DELETE FROM gps_health_episodes WHERE chat_id=?", (chat_id,))
    return outcome


async def run_weather_admin_scan(app):
    """Observe only, NEVER post to a driver group. Fail closed on uncertain data."""
    summary = {"checked": 0, "fresh": 0, "stale": 0, "inactive": 0,
               "unverified": 0, "alerts": 0, "admin_messages": 0,
               "nws_failed": 0, "duplicate": 0, "error": None, "limited": 0,
               "gps_health_alerts": 0, "gps_health_recoveries": 0}
    lock = app.bot_data.setdefault("weather_admin_scan_lock", asyncio.Lock())
    if lock.locked():
        summary["error"] = "Scan already running"
        return summary
    async with lock:
        if not SAMSARA_API_TOKEN:
            summary["error"] = "Samsara token not configured"
            return summary
        try:
            vehicles = await asyncio.to_thread(samsara_get_gps_snapshot)
        except Exception as exc:
            logger.warning("Private NWS scan skipped: Samsara %s", type(exc).__name__)
            summary["error"] = "Samsara GPS snapshot unavailable"
            return summary
        with get_connection() as conn:
            links = conn.execute("SELECT chat_id, vehicle_id FROM samsara_group_links ORDER BY chat_id").fetchall()
        # Enforce pilot API budget. Don't imply that unscanned groups were checked.
        summary["limited"] = max(0, len(links)-WEATHER_ADMIN_MAX_GROUPS_PER_SCAN)
        links = links[:WEATHER_ADMIN_MAX_GROUPS_PER_SCAN]
        now = datetime.now(timezone.utc)
        for chat_id, vehicle_id in links:
            summary["checked"] += 1
            live = await get_live_group(chat_id, app)
            if not live or live.type not in (ChatType.GROUP, ChatType.SUPERGROUP) or not live.title:
                summary["unverified"] += 1
                continue
            if is_inactive_group_name(live.title):
                summary["inactive"] += 1
                continue
            record = vehicles.get(str(vehicle_id))
            if not record or group_unit_from_title(live.title) != vehicle_unit_from_name(record.get("name")):
                summary["unverified"] += 1
                continue
            valid_gps = samsara_valid_gps(record.get("gps"), now=now)
            health = await gps_health_process(app, chat_id, vehicle_id, live.title, record, now, valid_gps)
            for health_key, count in health.items():
                summary[health_key] += count
            if valid_gps is None:
                summary["stale"] += 1
                continue
            summary["fresh"] += 1
            lat, lon, _ = valid_gps
            try:
                features = await asyncio.to_thread(nws_get_active_point, lat, lon)
            except Exception as exc:
                logger.warning("Private NWS lookup unavailable (group %s; %s)", chat_id, type(exc).__name__)
                summary["nws_failed"] += 1
                continue
            warnings = [item for feature in features if (item := weather_active_record(feature, now=now)) is not None]
            for item in warnings:
                # Unique warning + exact vehicle/group + official NWS alert ID.
                # Per-event cooldown limits revised IDs and excessive repetition.
                with get_connection() as conn:
                    exact_seen = conn.execute("SELECT 1 FROM weather_admin_notifications WHERE chat_id=? AND alert_id=? LIMIT 1",
                                              (chat_id, item["id"])).fetchone()
                    suppressed = _recent_admin_alert(conn, chat_id, item["event"], now)
                if exact_seen or suppressed:
                    summary["duplicate"] += 1
                    continue
                summary["alerts"] += 1
                # Fetch a timezone only if there is a new qualifying warning.
                try:
                    tz = await asyncio.to_thread(nws_get_point_timezone, lat, lon)
                except Exception:
                    tz = None  # Explicit UTC fallback, never guess.
                messages = weather_message_parts(item, unit=record.get("name"), timezone_name=tz)
                heading = ("🔒 PRIVATE WEATHER MONITOR — NOT SENT TO DRIVERS\n"
                           f"Group: {html.escape(live.title)}\n"
                           f"Truck: {html.escape(str(record.get('name') or vehicle_id))}\n\n")
                for admin in sorted(ADMIN_USER_IDS):
                    # Don't re-send to an admin already notified, even on retries.
                    with get_connection() as conn:
                        already = conn.execute("SELECT 1 FROM weather_admin_notifications WHERE admin_id=? AND chat_id=? AND alert_id=?",
                                               (admin, chat_id, item["id"])).fetchone()
                    if already:
                        continue
                    try:
                        await app.bot.send_message(chat_id=admin, text=heading, parse_mode="HTML")
                        for part in messages:
                            await app.bot.send_message(chat_id=admin, text=part, parse_mode="HTML",
                                                       disable_web_page_preview=True)
                    except TelegramError as exc:
                        logger.warning("Weather private admin notification failed for admin %s (%s)", admin, type(exc).__name__)
                        continue
                    # Mark after successful delivery. No driver group messaging exists in this function.
                    with get_connection() as conn:
                        conn.execute("""INSERT OR IGNORE INTO weather_admin_notifications
                            (admin_id,chat_id,alert_id,event,delivered_at) VALUES (?,?,?,?,?)""",
                            (admin, chat_id, item["id"], item["event"], now.isoformat()))
                    summary["admin_messages"] += 1
            await asyncio.sleep(0.15)  # Conservative NWS request pacing.
        app.bot_data["weather_admin_last_scan"] = datetime.now(timezone.utc).isoformat()
        app.bot_data["weather_admin_last_result"] = summary.copy()
        return summary


async def weather_admin_loop(app):
    while True:
        try:
            await run_weather_admin_scan(app)
        except Exception:
            logger.exception("Private weather monitoring cycle failed; drivers unaffected")
        await asyncio.sleep(WEATHER_ADMIN_SCAN_SECONDS)


def gps_diagnostic_reason(record, now):
    """Explain why a Samsara GPS record is unusable, without estimating a position.

    Returns (reason, last_utc) or (None, last_utc) for fresh valid GPS.
    Uses the same freshness and geographic limits as samsara_valid_gps.
    """
    if not isinstance(record, dict):
        return "GPS data missing", None
    gps = record.get("gps")
    if not isinstance(gps, dict):
        return "GPS data missing", None
    timestamp = weather_parse_time(gps.get("time"))
    if timestamp is None:
        return "GPS timestamp missing or invalid", None
    stamp = timestamp.strftime("%Y-%m-%d %H:%M UTC")
    if timestamp > now + timedelta(minutes=1):
        return "GPS timestamp is in the future", stamp
    age = now - timestamp
    if age > timedelta(minutes=SAMSARA_MAX_GPS_AGE_MINUTES):
        mins = int(age.total_seconds() // 60)
        return f"GPS stale ({mins} minutes old; limit {SAMSARA_MAX_GPS_AGE_MINUTES})", stamp
    lat, lon = gps.get("latitude"), gps.get("longitude")
    if type(lat) not in (int, float) or type(lon) not in (int, float):
        return "GPS coordinates missing or invalid", stamp
    if not (18 <= lat <= 72 and -180 <= lon <= -60):
        return "GPS coordinates outside supported US area", stamp
    return None, stamp


async def weathergpsissues_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Private, read-only diagnostics for ALL linked groups. Never notify drivers."""
    if not samsara_private_admin(update):
        return
    await update.effective_message.reply_text(
        "Checking linked truck GPS freshness privately. No driver messages will be sent."
    )
    if not SAMSARA_API_TOKEN:
        await update.effective_message.reply_text("Samsara token missing; GPS diagnostics unavailable.")
        return
    try:
        vehicles = await asyncio.to_thread(samsara_get_gps_snapshot)
    except Exception as exc:
        logger.warning("GPS diagnostics snapshot unavailable (%s)", type(exc).__name__)
        await update.effective_message.reply_text("Samsara GPS snapshot unavailable. No changes made.")
        return
    with get_connection() as conn:
        linked = conn.execute(
            "SELECT chat_id, vehicle_id FROM samsara_group_links ORDER BY chat_id"
        ).fetchall()
    now = datetime.now(timezone.utc)
    fresh = 0
    issues = []
    skipped = []
    for chat_id, vehicle_id in linked:
        live = await get_live_group(chat_id, context)
        if not live or live.type not in (ChatType.GROUP, ChatType.SUPERGROUP) or not live.title:
            skipped.append(f"Group {chat_id}: Telegram group unverified")
            continue
        if is_inactive_group_name(live.title):
            skipped.append(f"{live.title}: INACTIVE (excluded)")
            continue
        record = vehicles.get(str(vehicle_id))
        title_unit = group_unit_from_title(live.title)
        vehicle_unit = vehicle_unit_from_name(record.get("name")) if isinstance(record, dict) else None
        if record is None:
            skipped.append(f"{live.title}: linked Samsara vehicle missing from snapshot")
            continue
        if title_unit is None or title_unit != vehicle_unit:
            skipped.append(f"{live.title}: unit mismatch (needs link review)")
            continue
        reason, stamp = gps_diagnostic_reason(record, now)
        if reason is None:
            fresh += 1
            continue
        issues.append(
            f"Unit {title_unit} | {live.title}\n"
            f"Problem: {reason}\n"
            f"Last GPS: {stamp or 'Unavailable'}"
        )
    header = (
        "BUK SPEAKER | PRIVATE GPS DIAGNOSTICS\n"
        f"Linked groups: {len(linked)} | Fresh GPS: {fresh} | GPS issues: {len(issues)}\n"
        f"Unverified/inactive/link review: {len(skipped)}\n"
        f"GPS freshness threshold: {SAMSARA_MAX_GPS_AGE_MINUTES} minutes\n"
        "GPS issues are not proof of equipment failure. Driver weather alerts remain OFF."
    )
    entries = issues + (["Other skipped groups:"] + skipped if skipped else [])
    pages = [header]
    for entry in entries:
        if len(pages[-1]) + len(entry) + 2 > 3500:
            pages.append("BUK SPEAKER | GPS DIAGNOSTICS (continued)")
        pages[-1] += "\n\n" + entry
    if not issues and not skipped:
        pages[0] += "\n\nAll linked active groups have fresh usable GPS."
    for page in pages:
        await update.effective_message.reply_text(page)


async def gpshealthstatus_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Private, read-only audit of GPS incidents and notification deduplication.

    No Samsara requests, database modifications, or driver-group messages.
    An incident may remain open while a private recovery notice is pending.
    """
    if not samsara_private_admin(update):
        return
    now = datetime.now(timezone.utc)
    with get_connection() as conn:
        episodes = conn.execute("""
            SELECT e.chat_id, e.vehicle_id, e.started_at, e.state,
                   e.last_checked_at, COALESCE(g.group_name, 'Unknown group'),
                   (SELECT COUNT(*) FROM gps_health_notifications n
                    WHERE n.chat_id=e.chat_id AND n.episode_started_at=e.started_at
                      AND n.kind='stale') AS stale_copies,
                   (SELECT COUNT(*) FROM gps_health_notifications n
                    WHERE n.chat_id=e.chat_id AND n.episode_started_at=e.started_at
                      AND n.kind='recovered') AS recovery_copies
            FROM gps_health_episodes e
            LEFT JOIN groups g ON g.chat_id=e.chat_id
            ORDER BY e.last_checked_at DESC, e.chat_id
        """).fetchall()
        notifications = conn.execute("""
            SELECT kind, COUNT(*) FROM gps_health_notifications GROUP BY kind
        """).fetchall()
    counts = dict(notifications)
    issue_count = sum(1 for e in episodes if e[3] == 'issue')
    recovery_pending = sum(1 for e in episodes if e[3] == 'recovered')
    header = (
        'BUK SPEAKER | GPS HEALTH INCIDENT STATUS\n'
        f'Open issues: {issue_count} | Pending recovery closure: {recovery_pending}\n'
        f'Private warning copies recorded: {counts.get("stale", 0)}\n'
        f'Private recovery copies recorded: {counts.get("recovered", 0)}\n'
        'Read-only saved incident data; not a live GPS scan.\n'
        'Driver weather delivery: OFF.'
    )
    pages = [header]
    for chat_id, vehicle_id, started, state, checked, title, stale_copies, recovered_copies in episodes:
        # An inactive group's historical incident may remain stored, but is never
        # an operational destination. Do not imply a cached title is live.
        if is_inactive_group_name(title):
            status = 'INACTIVE (cached; not monitored)'
        elif state == 'recovered':
            status = 'Recovered; private recovery delivery pending'
        else:
            status = 'GPS issue being monitored'
        since = weather_parse_time(started)
        age = f'{int((now - since).total_seconds() // 60)} min' if since and since <= now else 'Unknown'
        entry = (
            f'Group: {title}\n'
            f'Chat ID: {chat_id} | Vehicle ID: {vehicle_id}\n'
            f'Status: {status}\n'
            f'Incident age: {age} | Last checked: {checked}\n'
            f'Private notices recorded: issue {stale_copies}, recovery {recovered_copies}'
        )
        if len(pages[-1]) + len(entry) + 2 > 3500:
            pages.append('BUK SPEAKER | GPS HEALTH INCIDENTS (continued)')
        pages[-1] += '\n\n' + entry
    if not episodes:
        pages[0] += '\n\nNo open GPS health incidents recorded.'
    for page in pages:
        await update.effective_message.reply_text(page)


async def weatheradminstatus_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not samsara_private_admin(update):
        return
    with get_connection() as conn:
        linked = conn.execute("SELECT COUNT(*) FROM samsara_group_links").fetchone()[0]
        tracked = conn.execute("SELECT COUNT(*) FROM weather_admin_notifications").fetchone()[0]
    result = context.application.bot_data.get("weather_admin_last_result") or {}
    await update.effective_message.reply_text(
        f"Private weather monitoring: {'ON' if WEATHER_ADMIN_MONITOR_ENABLED else 'OFF (manual scan available)'}\n"
        f"Linked truck groups: {linked}\n"
        f"Scan interval: {WEATHER_ADMIN_SCAN_SECONDS//60} minutes\n"
        f"Pilot scan limit: {WEATHER_ADMIN_MAX_GROUPS_PER_SCAN} groups/cycle\n"
        f"Administrator alert records: {tracked}\n"
        f"Last scan: {context.application.bot_data.get('weather_admin_last_scan', 'none')}\n"
        f"Last result: {result or 'none'}\n"
        f"Private GPS health alerts: {'ON' if GPS_HEALTH_ADMIN_ALERTS_ENABLED else 'OFF'} "
        f"(after {GPS_HEALTH_NOTIFY_AFTER_MINUTES} minutes)\n"
        "Weather messages to driver groups: OFF (hard-disabled)."
    )


async def weatheradminscan_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not samsara_private_admin(update):
        return
    await update.effective_message.reply_text(
        "Starting private linked-truck weather scan. No messages to driver groups.")
    summary = await run_weather_admin_scan(context.application)
    await update.effective_message.reply_text(
        "PRIVATE WEATHER SCAN RESULT\n"
        f"Linked groups checked: {summary['checked']}\n"
        f"Fresh GPS: {summary['fresh']} | Stale/missing GPS: {summary['stale']}\n"
        f"Inactive skipped: {summary['inactive']} | Unverified: {summary['unverified']}\n"
        f"NWS request failures: {summary['nws_failed']}\n"
        f"New qualifying alerts: {summary['alerts']} | Duplicates suppressed: {summary['duplicate']}\n"
        f"Administrator copies delivered: {summary['admin_messages']}\n"
        f"Not scanned (pilot limit): {summary['limited']}\n"
        f"GPS health admin alerts: {summary['gps_health_alerts']} | "
        f"GPS recovery notices: {summary['gps_health_recoveries']}\n"
        f"Status: {summary['error'] or 'Complete'}\n"
        "Driver-group weather notifications: OFF."
    )


async def weather_loop(app):
    while True:
        try:
            await weather_scan(app)
        except Exception:
            logger.exception("Weather monitor failed; no unverified notices sent")
        await asyncio.sleep(WEATHER_POLL_SECONDS)


async def weather_post_init(app):
    if SAMSARA_AUTO_ONBOARD_ENABLED:
        app.bot_data["samsara_auto_task"] = asyncio.create_task(samsara_auto_loop(app))
        logger.info("Samsara automatic group matching enabled, interval %ss", SAMSARA_AUTO_SCAN_SECONDS)
    if WEATHER_ADMIN_MONITOR_ENABLED:
        app.bot_data["weather_admin_task"] = asyncio.create_task(weather_admin_loop(app))
        logger.info("Private Samsara/NWS monitor enabled; interval %s seconds", WEATHER_ADMIN_SCAN_SECONDS)
    if WEATHER_ENABLED:
        app.bot_data["weather_task"] = asyncio.create_task(weather_loop(app))
        logger.info("Official NWS monitoring enabled, interval %ss", WEATHER_POLL_SECONDS)
    else:
        logger.info("Official NWS monitoring OFF until explicitly enabled")


async def weather_post_shutdown(app):
    for name in ("samsara_auto_task", "weather_admin_task", "weather_task"):
        task = app.bot_data.get(name)
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass




# Fleet-report commands are read-only and ADMIN PRIVATE ONLY. No messages to groups.
async def fleet_group_report_rows(context, include_samsara=False):
    """Fast, read-only inventory from saved registrations; do not query Telegram for every group.

    Titles and active/inactive labels are CACHED, not authoritative. Sending and
    automatic matching still use live Telegram checks before acting.
    """
    with get_connection() as conn:
        groups = conn.execute(
            "SELECT chat_id, group_name FROM groups ORDER BY group_name, chat_id"
        ).fetchall()
        links = dict(conn.execute(
            "SELECT chat_id, vehicle_id FROM samsara_group_links"
        ).fetchall())

    vehicles, vehicle_error = None, None
    if include_samsara:
        try:
            vehicles = await asyncio.wait_for(
                asyncio.to_thread(samsara_get_gps_snapshot), timeout=40
            )
        except Exception as error:
            vehicle_error = type(error).__name__
            logger.warning("Fleet report Samsara lookup unavailable (%s)", vehicle_error)

    by_unit = {}
    if vehicles is not None:
        for vid, rec in vehicles.items():
            unit = vehicle_unit_from_name(rec.get("name"))
            if unit:
                by_unit.setdefault(unit, []).append(str(vid))

    rows = []
    for chat_id, stored_title in groups:
        title = stored_title or "Unnamed group"
        status = "INACTIVE (cached)" if is_inactive_group_name(title) else "ACTIVE (cached)"
        unit = group_unit_from_title(title) if not is_inactive_group_name(title) else None
        vid = links.get(chat_id)
        if is_inactive_group_name(title):
            detail = "Excluded based on saved title" + ("; old link retained" if vid else "")
        elif vid:
            linked_unit = (vehicle_unit_from_name(vehicles.get(vid, {}).get("name"))
                           if vehicles is not None else None)
            if vehicles is not None and (linked_unit is None or unit != linked_unit):
                detail = "LINK REVIEW NEEDED: saved group unit/vehicle mismatch"
            else:
                detail = "Linked" + (f" (vehicle {vid})" if not include_samsara else "")
        elif not unit:
            detail = "UNMATCHED: unit number not recognized in saved group title"
        elif vehicles is None:
            detail = "UNMATCHED: Samsara lookup unavailable" if include_samsara else "Unlinked"
        elif not by_unit.get(unit):
            detail = "UNMATCHED: unit not found in Samsara"
        elif len(by_unit[unit]) != 1:
            detail = "UNMATCHED: multiple Samsara vehicles use this unit"
        else:
            detail = "UNMATCHED: potential match requires review"
        rows.append({"id": chat_id, "title": title, "status": status,
                     "unit": unit, "vehicle": vid, "detail": detail})
    return rows, vehicle_error


async def fleet_report_send_pages(update, rows, title, summary):
    """Send bounded text pages, never exceeding Telegram message limits."""
    lines = [title, summary, ""]
    if not rows:
        lines.append("No groups to show.")
    for row in rows:
        unit = f"Unit {row['unit']} | " if row['unit'] else ""
        entry = (f"{unit}{row['title']}\n"
                 f"Group ID: {row['id']} | {row['status']}\n{row['detail']}")
        if len("\n\n".join(lines + [entry])) > 3400:
            await update.effective_message.reply_text("\n\n".join(lines))
            lines = [title + " (continued)", entry]
        else:
            lines.append(entry)
    await update.effective_message.reply_text("\n\n".join(lines))


async def groups_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not samsara_private_admin(update):
        return
    try:
        rows, _ = await fleet_group_report_rows(context, include_samsara=False)
        active = sum(r['status'].startswith('ACTIVE') for r in rows)
        inactive = sum(r['status'].startswith('INACTIVE') for r in rows)
        active_linked = sum(1 for r in rows if r['status'].startswith('ACTIVE') and r['vehicle'])
        summary = (f"Registered: {len(rows)} | Active (cached): {active} | "
                   f"Inactive (cached): {inactive}\n"
                   f"Linked active (cached): {active_linked} | "
                   f"Unlinked active (cached): {active-active_linked}\n"
                   "Group titles are SAVED copies, not live verification. "
                   "Broadcasts still check current titles. No driver messages sent.")
        await fleet_report_send_pages(update, rows, "BUK SPEAKER | REGISTERED GROUPS", summary)
    except Exception:
        logger.exception("Fleet inventory command failed")
        await update.effective_message.reply_text(
            "Group report failed. Check FadeHost logs for 'Fleet inventory command failed'.")


async def samsaraunmatched_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not samsara_private_admin(update):
        return
    await update.effective_message.reply_text(
        "Checking saved group registrations against Samsara. Report is private; no group messages sent.")
    try:
        rows, error = await fleet_group_report_rows(context, include_samsara=True)
        pending = [r for r in rows if r['status'].startswith('ACTIVE') and
                   (not r['vehicle'] or 'REVIEW NEEDED' in r['detail'])]
        unlinked = sum(r['status'].startswith('ACTIVE') and not r['vehicle'] for r in rows)
        review = sum('REVIEW NEEDED' in r['detail'] for r in rows)
        summary = (f"Active unlinked (cached): {unlinked} | Linked needing review: {review}\n"
                   + (f"Samsara unavailable ({error}); reasons incomplete.\n" if error else "")
                   + "Titles are saved copies. No groups linked or notified.")
        await fleet_report_send_pages(update, pending, "BUK SPEAKER | UNMATCHED GROUPS", summary)
    except Exception:
        logger.exception("Unmatched fleet report command failed")
        await update.effective_message.reply_text(
            "Unmatched report failed. Check FadeHost logs for 'Unmatched fleet report command failed'.")


SPEAKER_BUILD = "2026-11-21-live-nws-pilot-v24"


# V23: MANUAL, EXPLICITLY-APPROVED TEST-GROUP ONLY PILOT.
# Deliberately cannot send live NWS alerts or notify any production driver group.
# WEATHER_ENABLED stays hard-disabled. This pilot does not run on a schedule.
WEATHER_PILOT_TEST_CHAT_ID = os.getenv("WEATHER_PILOT_TEST_CHAT_ID", "").strip()
WEATHER_PILOT_LAST_SENT = None


def weather_pilot_allowed(chat_id, title):
    """Deny by default: exact allowlisted group ID AND clearly named TEST group."""
    return (WEATHER_PILOT_TEST_CHAT_ID.startswith("-")
            and WEATHER_PILOT_TEST_CHAT_ID[1:].isdecimal()
            and str(chat_id) == WEATHER_PILOT_TEST_CHAT_ID
            and bool(title)
            and not is_inactive_group_name(title)
            and bool(re.search(r"(?:^|[\s_-])TEST(?:$|[\s_-])", title, re.IGNORECASE)))


async def weatherpilotstatus_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Read-only private admin status; never sends to any group."""
    if not samsara_private_admin(update):
        return
    configured = bool(WEATHER_PILOT_TEST_CHAT_ID)
    await update.effective_message.reply_text(
        "BUK SPEAKER | WEATHER TEST PILOT\n"
        f"Test group configured: {'YES' if configured else 'NO'}\n"
        "Pilot type: simulated delivery or manual REAL NWS test mirror\n"
        "Group ID and live group title validated before each test\n"
        f"Production weather alerts: {'ON' if WEATHER_ENABLED else 'OFF'}\n"
        "No automatic pilot deliveries. Live NWS mirrors are TEST-only.\n"
        "Simulation: /weatherpilottest TEST_GROUP_CHAT_ID CONFIRM\n"
        "Live NWS: /weatherpilotlive SOURCE_DRIVER_GROUP_ID CONFIRM\n"
        "History: /weatherpilotlivehistory"
    )


async def weatherpilottest_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send exactly one prominently labelled SIMULATION to an allowlisted test group."""
    if not samsara_private_admin(update):
        return
    args = context.args or []
    if (len(args) != 2 or args[1] != "CONFIRM" or
            not re.fullmatch(r"-\d{5,20}", args[0])):
        await update.effective_message.reply_text(
            "Usage: /weatherpilottest NEGATIVE_TEST_GROUP_ID CONFIRM\n"
            "This sends ONE simulated weather message to the configured TEST group only."
        )
        return
    requested_id = args[0]
    # Validate allowlist BEFORE ANY group lookup or sending.
    if not WEATHER_PILOT_TEST_CHAT_ID or requested_id != WEATHER_PILOT_TEST_CHAT_ID:
        await update.effective_message.reply_text(
            "TEST DENIED: this ID is not the FadeHost WEATHER_PILOT_TEST_CHAT_ID allowlist."
        )
        return
    chat_id = int(requested_id)
    with get_connection() as conn:
        registered = conn.execute("SELECT 1 FROM groups WHERE chat_id=?", (chat_id,)).fetchone()
    if not registered:
        await update.effective_message.reply_text("TEST DENIED: group is not registered with BUK Speaker.")
        return
    live = await get_live_group(chat_id, context)
    if (not live or live.type not in (ChatType.GROUP, ChatType.SUPERGROUP)
            or not weather_pilot_allowed(chat_id, live.title)):
        await update.effective_message.reply_text(
            "TEST DENIED: live Telegram title must clearly contain TEST and must not be inactive."
        )
        return
    # Recheck the title immediately before sending; no group changes or GPS needed.
    latest = await get_live_group(chat_id, context)
    if (not latest or latest.type not in (ChatType.GROUP, ChatType.SUPERGROUP)
            or not weather_pilot_allowed(chat_id, latest.title)):
        await update.effective_message.reply_text("TEST DENIED: test group verification changed.")
        return
    simulation = (
        "🧪 <b>BUK SPEAKER — WEATHER DELIVERY TEST</b>\n\n"
        "<b>SIMULATION ONLY — NOT A LIVE WEATHER ALERT</b>\n\n"
        "🌨️ <b>Example hazard:</b> Winter Weather Advisory (TEST)\n\n"
        "🚛 <b>Example guidance:</b> Reduce speed and allow extra stopping distance "
        "when roads are slick. Stop safely if conditions become hazardous.\n\n"
        "This message tests Telegram delivery and formatting only. "
        "It does NOT indicate an actual warning or a truck's real location.\n\n"
        "🔒 Authorized BUK Speaker test group only."
    )
    try:
        sent = await context.bot.send_message(
            chat_id=chat_id, text=simulation, parse_mode="HTML",
            disable_web_page_preview=True,
        )
    except TelegramError as exc:
        logger.warning("Weather test pilot delivery failed (%s)", type(exc).__name__)
        await update.effective_message.reply_text("Test delivery failed. No other groups were contacted.")
        return
    context.application.bot_data["weather_pilot_last_sent"] = datetime.now(timezone.utc).isoformat()
    await update.effective_message.reply_text(
        f"TEST SENT to approved group: {latest.title}\n"
        f"Group ID: {chat_id} | Test message ID: {sent.message_id}\n"
        "This was a simulated message, not a real NWS alert.\n"
        "Production driver weather alerts remain OFF."
    )


# v24: A real NWS warning can be mirrored ONLY to the explicitly configured TEST
# group, under a manual private admin command. Never to a source driver group.
# No scheduled live pilot and no activation of WEATHER_ENABLED.
async def weatherpilotlive_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Mirror one real, qualifying point-based NWS warning into TEST, once."""
    if not samsara_private_admin(update):
        return
    args = context.args or []
    if (len(args) != 2 or args[1] != "CONFIRM" or
            not re.fullmatch(r"-\d{5,20}", args[0])):
        await update.effective_message.reply_text(
            "Usage: /weatherpilotlive SOURCE_DRIVER_GROUP_ID CONFIRM\n"
            "Live NWS data is sent ONLY to the configured non-driver TEST group."
        )
        return
    source_id = int(args[0])
    # The destination is never supplied in the command. It is a host allowlist.
    if not WEATHER_PILOT_TEST_CHAT_ID or not re.fullmatch(r"-\d{5,20}", WEATHER_PILOT_TEST_CHAT_ID):
        await update.effective_message.reply_text("DENIED: valid WEATHER_PILOT_TEST_CHAT_ID missing.")
        return
    dest_id = int(WEATHER_PILOT_TEST_CHAT_ID)
    if source_id == dest_id:
        await update.effective_message.reply_text("DENIED: the test group cannot be the source truck group.")
        return
    with get_connection() as conn:
        registered_dest = conn.execute("SELECT 1 FROM groups WHERE chat_id=?", (dest_id,)).fetchone()
        # A test destination must NEVER also be an operational Samsara-linked group.
        linked_dest = conn.execute("SELECT 1 FROM samsara_group_links WHERE chat_id=?", (dest_id,)).fetchone()
        source_link = conn.execute("SELECT vehicle_id FROM samsara_group_links WHERE chat_id=?", (source_id,)).fetchone()
    if not registered_dest or linked_dest or not source_link:
        await update.effective_message.reply_text(
            "DENIED: test group must be registered and NOT Samsara-linked; "
            "source must be a verified linked truck group. No messages sent.")
        return
    destination = await get_live_group(dest_id, context)
    source = await get_live_group(source_id, context)
    if (not destination or destination.type not in (ChatType.GROUP, ChatType.SUPERGROUP)
            or not weather_pilot_allowed(dest_id, destination.title)):
        await update.effective_message.reply_text("DENIED: test destination's live title is not approved TEST.")
        return
    if (not source or source.type not in (ChatType.GROUP, ChatType.SUPERGROUP)
            or not source.title or is_inactive_group_name(source.title)
            or not group_unit_from_title(source.title)):
        await update.effective_message.reply_text("DENIED: source driver group inactive or unverified.")
        return
    vehicles = await samsara_snapshot_or_error(update)
    if vehicles is None:
        return
    vehicle_id = str(source_link[0])
    record = vehicles.get(vehicle_id)
    unit = group_unit_from_title(source.title)
    if not record or vehicle_unit_from_name(record.get("name")) != unit:
        await update.effective_message.reply_text("DENIED: Samsara source unit/link mismatch.")
        return
    now = datetime.now(timezone.utc)
    gps = samsara_valid_gps(record.get("gps"), now=now)
    if gps is None:
        await update.effective_message.reply_text("No delivery: source truck GPS is stale/missing/invalid.")
        return
    lat, lon, timestamp = gps
    try:
        features = await asyncio.to_thread(nws_get_active_point, lat, lon)
    except Exception as exc:
        logger.warning("Live NWS test lookup failed (%s)", type(exc).__name__)
        await update.effective_message.reply_text("NWS lookup failed. No test or driver message sent.")
        return
    qualified = [item for feature in features
                 if (item := weather_active_record(feature, now=now)) is not None
                 and item["starts"] <= now]
    if not qualified:
        await update.effective_message.reply_text(
            f"Live NWS lookup completed for Unit {unit} (GPS {timestamp:%Y-%m-%d %H:%M} UTC).\n"
            "No qualifying active NWS warning. Nothing sent to any group.")
        return
    # Choose just one warning for this manual command. No scheduled deliveries.
    item = qualified[0]
    with get_connection() as conn:
        prior = conn.execute("SELECT status FROM weather_pilot_live_deliveries "
                             "WHERE source_chat_id=? AND alert_id=?", (source_id, item["id"])).fetchone()
    if prior:
        await update.effective_message.reply_text(
            f"DUPLICATE SUPPRESSED: Unit {unit}, {item['event']} (record: {prior[0]}). "
            "No additional test or driver message sent.")
        return
    # Verify identities and mapping again IMMEDIATELY before sending.
    destination2 = await get_live_group(dest_id, context)
    source2 = await get_live_group(source_id, context)
    with get_connection() as conn:
        link_now = conn.execute("SELECT vehicle_id FROM samsara_group_links WHERE chat_id=?", (source_id,)).fetchone()
        dest_link_now = conn.execute("SELECT 1 FROM samsara_group_links WHERE chat_id=?", (dest_id,)).fetchone()
    if (not destination2 or destination2.type not in (ChatType.GROUP, ChatType.SUPERGROUP)
            or not weather_pilot_allowed(dest_id, destination2.title)
            or not source2 or source2.type not in (ChatType.GROUP, ChatType.SUPERGROUP)
            or group_unit_from_title(source2.title) != unit or not link_now
            or str(link_now[0]) != vehicle_id or dest_link_now):
        await update.effective_message.reply_text("DENIED: group/link changed during test. No messages sent.")
        return
    # Reserve the real NWS alert ID before sending: fail-closed on retries and
    # crashes. A 'pending' record requires admin review instead of auto-resend.
    try:
        with get_connection() as conn:
            conn.execute("""INSERT INTO weather_pilot_live_deliveries
                (source_chat_id,alert_id,destination_chat_id,event,status,created_at)
                VALUES (?,?,?,?,?,?)""",
                (source_id, item["id"], dest_id, item["event"], "pending", now.isoformat()))
    except sqlite3.IntegrityError:
        await update.effective_message.reply_text("DUPLICATE SUPPRESSED. No messages sent.")
        return
    try:
        tz = await asyncio.to_thread(nws_get_point_timezone, lat, lon)
    except Exception:
        tz = None
    prefix = (
        "🧪 <b>BUK SPEAKER | LIVE NWS DATA — TEST GROUP ONLY</b>\n"
        "<b>NOT A WARNING FOR MEMBERS OF THIS TEST GROUP.</b>\n\n"
        f"<b>Source:</b> real NWS point lookup for Samsara Unit {html.escape(unit)}\n"
        f"<b>GPS timestamp:</b> {timestamp:%Y-%m-%d %H:%M} UTC\n"
        "<b>Destination:</b> authorized non-driver TEST group\n"
        "<b>Driver group delivery:</b> OFF\n\n"
    )
    parts = weather_message_parts(item, unit=unit, timezone_name=tz)
    try:
        await context.bot.send_message(
            chat_id=dest_id, text=prefix, parse_mode="HTML", disable_web_page_preview=True)
        for part in parts:
            await context.bot.send_message(
                chat_id=dest_id,
                text="🧪 <b>TEST GROUP COPY — NOT A WARNING FOR THIS GROUP</b>\n\n" + part,
                parse_mode="HTML", disable_web_page_preview=True)
    except TelegramError as exc:
        logger.warning("Live NWS pilot test delivery incomplete (%s)", type(exc).__name__)
        await update.effective_message.reply_text(
            "Test delivery failed or was partial. Record remains PENDING to prevent duplicate replay. "
            "Inspect SPEAKER TEST. Production driver messages were not sent.")
        return
    with get_connection() as conn:
        conn.execute("""UPDATE weather_pilot_live_deliveries SET status='sent',sent_at=?
            WHERE source_chat_id=? AND alert_id=?""",
            (datetime.now(timezone.utc).isoformat(), source_id, item["id"]))
    await update.effective_message.reply_text(
        f"LIVE NWS TEST SENT ONLY TO {destination2.title}.\n"
        f"Source: Unit {unit} | Hazard: {item['event']} | Parts: {len(parts)}\n"
        "Same source alert ID will not be sent again. Production driver alerts: OFF.")


async def weatherpilotlivehistory_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Read-only view of real-NWS test receipts, including partial pending sends."""
    if not samsara_private_admin(update):
        return
    with get_connection() as conn:
        rows = conn.execute("""SELECT source_chat_id,event,status,created_at
            FROM weather_pilot_live_deliveries ORDER BY created_at DESC LIMIT 12""").fetchall()
    lines = ["BUK SPEAKER | LIVE NWS TEST HISTORY", "Production driver alerts: OFF."]
    for source_id, event, status, created in rows:
        lines.append(f"Source {source_id} | {event} | {status} | {created}")
    if not rows:
        lines.append("No live NWS pilot deliveries recorded.")
    await update.effective_message.reply_text("\n".join(lines))


async def version_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Private admin-only deployment check; never broadcast this response."""
    if not is_admin(update) or update.effective_chat.type != ChatType.PRIVATE:
        return
    await update.effective_message.reply_text(
        f"BUK Speaker build: {SPEAKER_BUILD}\n"
        "Inactive-group check: enabled (live Telegram group titles)\n"
        "Media broadcasting: enabled\n"
        "Shared announcement history: enabled\n"
        "Automatic admin broadcast sharing: enabled\n"
        f"NWS weather monitoring: {'ON' if WEATHER_ENABLED else 'OFF (safe default)'}\n"
        f"Authorized admins: {len(ADMIN_USER_IDS)}\n"
        f"Samsara auto-onboarding: {'ON' if SAMSARA_AUTO_ONBOARD_ENABLED else 'OFF'}\n"
        f"Samsara private weather monitoring: {'ON' if WEATHER_ADMIN_MONITOR_ENABLED else 'OFF (manual scan available)'}\n"
        "Samsara weather delivery: PRIVATE PREVIEW ONLY"
    )


async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin-only text/media broadcast to verified ACTIVE groups exclusively."""
    if not is_admin(update):
        await update.effective_message.reply_text("⛔ You are not authorized to use this bot.")
        return
    if update.effective_chat.type != ChatType.PRIVATE:
        return

    command_message = update.effective_message
    source_message = command_message.reply_to_message
    media_message = bool(source_message and (
        source_message.photo or source_message.video
        or source_message.animation or source_message.document
    ))

    if media_message:
        broadcast_text = None
    else:
        broadcast_text = (command_message.text or "").partition(" ")[2].strip().replace("\\n", "\n")
        if not broadcast_text:
            await command_message.reply_text(
                "To broadcast a photo or video: send it to me with its caption, "
                "then reply to that media message with /broadcast.\n\n"
                "For a text-only announcement, use:\n"
                "/broadcast Your announcement here."
            )
            return

    with get_connection() as conn:
        groups = conn.execute(
            "SELECT chat_id, group_name FROM groups ORDER BY group_name"
        ).fetchall()

    if not groups:
        await command_message.reply_text("❌ No groups are registered yet.")
        return

    delivered = 0
    inactive = []
    unverified = []
    failed = []

    async def send_to_group(chat_id):
        if media_message:
            await context.bot.copy_message(
                chat_id=chat_id,
                from_chat_id=source_message.chat_id,
                message_id=source_message.message_id,
            )
        else:
            await context.bot.send_message(chat_id=chat_id, text=broadcast_text)

    for chat_id, stored_name in groups:
        # Fail closed if Telegram cannot confirm that the group is active.
        chat = await get_live_group(chat_id, context)
        if chat is None or chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP) or not chat.title:
            unverified.append(stored_name)
            continue

        current_name = chat.title
        with get_connection() as conn:
            conn.execute(
                "UPDATE groups SET group_name = ? WHERE chat_id = ?", (current_name, chat_id)
            )

        if is_inactive_group_name(current_name):
            inactive.append(current_name)
            continue

        try:
            await send_to_group(chat_id)
            delivered += 1
        except RetryAfter as error:
            await asyncio.sleep(float(error.retry_after) + 1)
            # A title might change during rate limiting. Check it again.
            refreshed = await get_live_group(chat_id, context)
            if refreshed is None or refreshed.type not in (ChatType.GROUP, ChatType.SUPERGROUP) or not refreshed.title:
                unverified.append(current_name)
                continue
            if is_inactive_group_name(refreshed.title):
                inactive.append(refreshed.title)
                continue
            try:
                await send_to_group(chat_id)
                delivered += 1
            except TelegramError:
                failed.append(current_name)
                logger.exception("Broadcast retry failed for %s (%s)", current_name, chat_id)
        except TelegramError:
            failed.append(current_name)
            logger.exception("Broadcast failed for %s (%s)", current_name, chat_id)
        await asyncio.sleep(0.05)

    content_type = "media announcement" if media_message else "text announcement"
    report = (
        f"📢 {content_type.capitalize()} broadcast completed!\n\n"
        f"📋 Registered groups: {len(groups)}\n"
        f"✅ Delivered to active groups: {delivered}\n"
        f"⏭️ Inactive groups skipped: {len(inactive)}\n"
        f"⚠️ Could not verify (skipped): {len(unverified)}\n"
        f"❌ Delivery failed: {len(failed)}"
    )
    if failed:
        report += "\n\nFailed groups: " + ", ".join(failed[:10])
    if unverified:
        report += "\n\nUnverified groups: " + ", ".join(unverified[:10])
    # Save finished broadcasts so every authorized admin can review future announcements.
    created_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    with get_connection() as conn:
        cursor = conn.execute(
            """INSERT INTO announcements
            (created_at, sender_id, kind, body, source_chat_id, source_message_id,
             delivered, inactive, unverified, failed, report)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                created_at, update.effective_user.id,
                "media" if media_message else "text",
                (source_message.caption if media_message else broadcast_text),
                source_message.chat_id if media_message else None,
                source_message.message_id if media_message else None,
                delivered, len(inactive), len(unverified), len(failed), report,
            ),
        )
        announcement_id = cursor.lastrowid

    await command_message.reply_text(f"Announcement #{announcement_id}\n\n{report}")

    # Share each announcement itself with every other admin immediately after
    # the broadcast finishes, followed by the full delivery report. They do
    # not have to request /history or /announcement for new broadcasts.
    # Telegram requires admins to have opened and started the bot first.
    for other_admin_id in sorted(ADMIN_USER_IDS - {update.effective_user.id}):
        try:
            await context.bot.send_message(
                chat_id=other_admin_id,
                text=(f"📢 BUK Speaker — announcement #{announcement_id}\n"
                      f"Sent by admin ID: {update.effective_user.id}\n"
                      f"Date: {created_at}\n"
                      "Announcement copy:"),
            )
            if media_message:
                await context.bot.copy_message(
                    chat_id=other_admin_id,
                    from_chat_id=source_message.chat_id,
                    message_id=source_message.message_id,
                )
            else:
                # Telegram limits sendMessage text to 4096 characters.
                for offset in range(0, len(broadcast_text), 3500):
                    await context.bot.send_message(
                        chat_id=other_admin_id,
                        text=broadcast_text[offset:offset + 3500],
                    )
            await context.bot.send_message(
                chat_id=other_admin_id,
                text=f"📊 Delivery results for announcement #{announcement_id}\n\n{report}"
            )
        except TelegramError:
            # An admin may have blocked the bot, not pressed Start yet, or a
            # copy may fail. Never change the driver delivery counts.
            logger.exception("Could not share announcement #%s with admin %s", announcement_id, other_admin_id)


async def history_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List the latest archived broadcasts in any authorized admin's private chat."""
    if not is_admin(update) or update.effective_chat.type != ChatType.PRIVATE:
        await update.effective_message.reply_text("⛔ Admin-only private command.")
        return
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT id, created_at, sender_id, kind, body, delivered, inactive
               FROM announcements ORDER BY id DESC LIMIT 15"""
        ).fetchall()
    if not rows:
        await update.effective_message.reply_text(
            "No archived broadcasts yet. History starts with this version; older Telegram messages are not imported."
        )
        return
    lines = ["📚 BUK Speaker — recent announcements (newest first)"]
    for item_id, created, sender, kind, body, delivered, inactive in rows:
        snippet = (body or "[media without caption]").replace("\n", " ")[:65]
        lines.append(
            f"\n#{item_id} • {created} • Admin {sender}\n"
            f"{kind}: {snippet}\n✅ {delivered} delivered, ⏭️ {inactive} inactive skipped"
        )
    lines.append("\nUse /announcement NUMBER to view the full message and report.")
    await update.effective_message.reply_text("\n".join(lines))


async def announcement_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Review any archived announcement, with media when Telegram permits copying."""
    if not is_admin(update) or update.effective_chat.type != ChatType.PRIVATE:
        await update.effective_message.reply_text("⛔ Admin-only private command.")
        return
    if len(context.args) != 1 or not context.args[0].isdecimal():
        await update.effective_message.reply_text("Usage: /announcement 12")
        return
    with get_connection() as conn:
        row = conn.execute(
            """SELECT created_at, sender_id, kind, body, source_chat_id,
                      source_message_id, report FROM announcements WHERE id=?""",
            (int(context.args[0]),),
        ).fetchone()
    if not row:
        await update.effective_message.reply_text("Announcement not found in the archive.")
        return
    created, sender, kind, body, source_chat, source_id, report = row
    header = f"📢 Announcement #{context.args[0]}\nDate: {created}\nAdmin ID: {sender}\n"
    if kind == "text":
        text = body or ""
        # Text messages can approach Telegram's 4096-character limit.
        await update.effective_message.reply_text(header + "\n" + report)
        for i in range(0, len(text), 3500):
            await update.effective_message.reply_text(text[i:i+3500])
    else:
        await update.effective_message.reply_text(header + "\n" + report)
        if source_chat is not None and source_id is not None:
            try:
                await context.bot.copy_message(
                    chat_id=update.effective_user.id,
                    from_chat_id=source_chat,
                    message_id=source_id,
                )
            except TelegramError:
                await update.effective_message.reply_text(
                    "This media is no longer available to copy from the original conversation."
                )
        else:
            await update.effective_message.reply_text(body or "[No media reference available]")


def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is missing. Set it in your cloud host's environment variables.")

    init_database()
    app = (Application.builder().token(BOT_TOKEN)
           .post_init(weather_post_init).post_shutdown(weather_post_shutdown).build())

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(interactive_menu))
    app.add_handler(CommandHandler("myid", myid))
    app.add_handler(CommandHandler("groupid", groupid))
    app.add_handler(CommandHandler("addgroup", addgroup))
    app.add_handler(CommandHandler("retrywelcome", retrywelcome))
    app.add_handler(CommandHandler("broadcast", broadcast))
    app.add_handler(CommandHandler("version", version_command))
    app.add_handler(CommandHandler("samsarastatus", samsarastatus_command))
    app.add_handler(CommandHandler("samsaraunits", samsaraunits_command))
    app.add_handler(CommandHandler("samsaraautostatus", samsaraautostatus_command))
    app.add_handler(CommandHandler("samsaraautoscan", samsaraautoscan_command))
    app.add_handler(CommandHandler("groups", groups_command))
    app.add_handler(CommandHandler("samsaraunmatched", samsaraunmatched_command))
    app.add_handler(CommandHandler("weatheradminstatus", weatheradminstatus_command))
    app.add_handler(CommandHandler("weatherpilotstatus", weatherpilotstatus_command))
    app.add_handler(CommandHandler("weatherpilottest", weatherpilottest_command))
    app.add_handler(CommandHandler("weatherpilotlive", weatherpilotlive_command))
    app.add_handler(CommandHandler("weatherpilotlivehistory", weatherpilotlivehistory_command))
    app.add_handler(CommandHandler("weathergpsissues", weathergpsissues_command))
    app.add_handler(CommandHandler("gpshealthstatus", gpshealthstatus_command))
    app.add_handler(CommandHandler("weatheradminscan", weatheradminscan_command))
    app.add_handler(CommandHandler("samsaramatchpreview", samsaramatchpreview_command))
    app.add_handler(CommandHandler("samsaramatchconfirm", samsaramatchconfirm_command))
    app.add_handler(CommandHandler("samsaralink", samsaralink_command))
    app.add_handler(CommandHandler("samsaraunlink", samsaraunlink_command))
    app.add_handler(CommandHandler("samsarapreview", samsarapreview_command))
    app.add_handler(CommandHandler("weatherpoint", weatherpoint_command))
    app.add_handler(CommandHandler("weatheroff", weatheroff_command))
    app.add_handler(CommandHandler("weatherstatus", weatherstatus_command))
    app.add_handler(CommandHandler("weatherpreview", weatherpreview_command))
    app.add_handler(CommandHandler("weathercheck", weathercheck_command))
    app.add_handler(CommandHandler("history", history_command))
    app.add_handler(CommandHandler("announcement", announcement_command))
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_TITLE, on_group_title_change))
    app.add_handler(
        ChatMemberHandler(on_bot_membership_change, ChatMemberHandler.MY_CHAT_MEMBER)
    )

    logger.info("BUK Speaker Bot running build %s with %d authorized admin(s)", SPEAKER_BUILD, len(ADMIN_USER_IDS))
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()

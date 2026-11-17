import asyncio
import logging
import html
import os
import re
import sqlite3
import json
import urllib.request
import urllib.parse
from datetime import timedelta
from datetime import datetime, timezone
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


# WEATHER PHASE 1: Official NWS point alerts only. No DOT, route, or Samsara
# claims are made here. Driver delivery is OFF until explicitly enabled.
# Two independent switches prevent accidental production notifications on upgrade.
WEATHER_REQUESTED = os.getenv("WEATHER_ALERTS_ENABLED", "false").lower() == "true"
WEATHER_TEMPLATES_APPROVED = os.getenv("WEATHER_TEMPLATES_APPROVED", "false").lower() == "true"
WEATHER_ENABLED = WEATHER_REQUESTED and WEATHER_TEMPLATES_APPROVED
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


def weather_message(info):
    """Only official, validated NWS fields; safe HTML formatting for Telegram."""
    event = info["event"]
    heading = weather_heading(event, event in GENERAL_ADVISORY_EVENTS)
    area = info["area"]
    if len(area) > 250:
        area = area[:245].rsplit(";", 1)[0] + "; ... (see NWS alert)"
    fmt = lambda dt: dt.strftime("%b %d, %I:%M %p UTC")
    lines = [f"<b>{heading}</b>",
             f"{WEATHER_EMOJI.get(event, '⚠️')} <b>Hazard:</b> {html.escape(event)}",
             f"📍 <b>Area:</b> {html.escape(area)}",
             f"🕒 <b>NWS Alert Expires:</b> {fmt(info['expires'])}"]
    action = EVENT_ACTIONS.get(event)
    if action:
        lines.append(f"🚛 <b>Driver Action:</b> {html.escape(action)}")
    lines.append("<i>Source: National Weather Service</i>")
    return "\n".join(lines)


def weather_preview_message(kind):
    """Sample text only, private to admins; never a live alert."""
    _, event, condition, action = PREVIEW_EXAMPLES[kind]
    heading = ("🚨 URGENT WEATHER ALERT" if kind == "tornado"
               else f"{PREVIEW_EMOJI[kind]} GENERAL WEATHER REMINDER")
    return (f"🧪 <b>SAMPLE - NOT A LIVE ALERT</b>\n"
            f"<b>{heading}</b>\n"
            f"{PREVIEW_EMOJI[kind]} <b>Hazard:</b> {html.escape(event)}\n"
            f"<b>Conditions:</b> {html.escape(condition)}\n"
            f"🚛 <b>Driver Action:</b> {html.escape(action)}\n"
            "<i>Preview for administrator approval only</i>")


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
    for item in items[:8]:
        await update.effective_message.reply_text(weather_message(item),
                                                 parse_mode="HTML", disable_web_page_preview=True)
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
                await context.bot.send_message(chat_id=chat_id, text=weather_message(item),
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


async def weather_loop(app):
    while True:
        try:
            await weather_scan(app)
        except Exception:
            logger.exception("Weather monitor failed; no unverified notices sent")
        await asyncio.sleep(WEATHER_POLL_SECONDS)


async def weather_post_init(app):
    if WEATHER_ENABLED:
        app.bot_data["weather_task"] = asyncio.create_task(weather_loop(app))
        logger.info("Official NWS monitoring enabled, interval %ss", WEATHER_POLL_SECONDS)
    else:
        logger.info("Official NWS monitoring OFF until explicitly enabled")


async def weather_post_shutdown(app):
    task = app.bot_data.get("weather_task")
    if task:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass



SPEAKER_BUILD = "2026-11-17-weather-formatted-preview-v9"


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
        f"Authorized admins: {len(ADMIN_USER_IDS)}"
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

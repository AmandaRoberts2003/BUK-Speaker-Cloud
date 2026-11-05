import asyncio
import logging
import os
import sqlite3
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
)

# Keep credentials in the cloud host's environment variables, never in source code.
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID", "488671902"))

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
            # Existing registered groups are treated as already introduced, so
            # upgrading will not unexpectedly send the welcome to old groups.
            conn.execute(
                "ALTER TABLE groups ADD COLUMN welcome_sent INTEGER NOT NULL DEFAULT 1"
            )


def is_admin(update: Update) -> bool:
    return (
        update.effective_user is not None
        and update.effective_user.id == ADMIN_USER_ID
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


async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin-only broadcast of text, or a replied-to photo/video as one message."""
    if not is_admin(update):
        await update.effective_message.reply_text("⛔ You are not authorized to use this bot.")
        return
    if update.effective_chat.type != ChatType.PRIVATE:
        return

    command_message = update.effective_message
    source_message = command_message.reply_to_message

    # Reply to a photo/video with /broadcast to copy the original media and
    # its caption together into every registered group.
    media_message = (
        source_message is not None
        and (
            source_message.photo
            or source_message.video
            or source_message.animation
            or source_message.document
        )
    )

    if media_message:
        broadcast_text = None
    else:
        broadcast_text = command_message.text.partition(" ")[2].strip().replace("\\n", "\n")
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

    successful = 0
    failed = []
    for chat_id, group_name in groups:
        try:
            if media_message:
                await context.bot.copy_message(
                    chat_id=chat_id,
                    from_chat_id=source_message.chat_id,
                    message_id=source_message.message_id,
                )
            else:
                await context.bot.send_message(chat_id=chat_id, text=broadcast_text)
            successful += 1
        except RetryAfter as error:
            await asyncio.sleep(float(error.retry_after) + 1)
            try:
                if media_message:
                    await context.bot.copy_message(
                        chat_id=chat_id,
                        from_chat_id=source_message.chat_id,
                        message_id=source_message.message_id,
                    )
                else:
                    await context.bot.send_message(chat_id=chat_id, text=broadcast_text)
                successful += 1
            except TelegramError:
                failed.append(group_name)
                logger.exception("Broadcast retry failed for %s (%s)", group_name, chat_id)
        except TelegramError:
            failed.append(group_name)
            logger.exception("Broadcast failed for %s (%s)", group_name, chat_id)
        await asyncio.sleep(0.05)

    content_type = "media announcement" if media_message else "text announcement"
    report = (
        f"📢 {content_type.capitalize()} broadcast completed!\n\n"
        f"✅ Delivered: {successful}/{len(groups)}\n"
        f"❌ Failed: {len(failed)}"
    )
    if failed:
        report += "\n\nFailed groups: " + ", ".join(failed[:10])
        if len(failed) > 10:
            report += f" (+{len(failed) - 10} more)"
    await command_message.reply_text(report)


def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is missing. Set it in your cloud host's environment variables.")

    init_database()
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(interactive_menu))
    app.add_handler(CommandHandler("myid", myid))
    app.add_handler(CommandHandler("groupid", groupid))
    app.add_handler(CommandHandler("addgroup", addgroup))
    app.add_handler(CommandHandler("retrywelcome", retrywelcome))
    app.add_handler(CommandHandler("broadcast", broadcast))
    app.add_handler(
        ChatMemberHandler(on_bot_membership_change, ChatMemberHandler.MY_CHAT_MEMBER)
    )

    logger.info("BUK Speaker Bot is running")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()

import asyncio
import logging
import os
import re
import sqlite3
from contextlib import contextmanager

from telegram import Update
from telegram.constants import ChatType
from telegram.error import RetryAfter, TelegramError
from telegram.ext import (
    Application,
    ChatMemberHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
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
    if not is_admin(update):
        await update.effective_message.reply_text("⛔ You are not authorized to use this bot.")
        return
    await update.effective_message.reply_text(
        "✅ BUK Speaker Bot is working!\n\nAdministrator access confirmed."
    )


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


# Match only the group-status label at the beginning of its name.
# Examples: "Inactive BUK 113 Jermall Dent", "[INACTIVE] BUK 113".
INACTIVE_PREFIX = re.compile(r"^\s*(?:\[\s*inactive\s*\]|inactive\b)", re.IGNORECASE)


def is_inactive_group_name(title: str) -> bool:
    return bool(INACTIVE_PREFIX.search(title or ""))


async def get_live_group(chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    """Read the latest title from Telegram; return None if verification fails.

    Never use an old cached title as a reason to send a broadcast.
    """
    try:
        return await context.bot.get_chat(chat_id)
    except RetryAfter as error:
        await asyncio.sleep(float(error.retry_after) + 1)
        try:
            return await context.bot.get_chat(chat_id)
        except TelegramError:
            logger.exception("Failed to verify group after rate-limit retry: %s", chat_id)
            return None
    except TelegramError:
        logger.exception("Failed to verify current group title: %s", chat_id)
        return None


async def on_group_title_change(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Keep the registered title reasonably current between broadcasts."""
    if not is_group_chat(update) or not update.effective_message:
        return
    new_title = update.effective_message.new_chat_title
    if not new_title:
        return
    with get_connection() as conn:
        conn.execute(
            "UPDATE groups SET group_name = ? WHERE chat_id = ?",
            (new_title, update.effective_chat.id),
        )


async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Broadcast only to registered groups whose CURRENT names are active."""
    if not is_admin(update):
        await update.effective_message.reply_text("⛔ You are not authorized to use this bot.")
        return
    if update.effective_chat.type != ChatType.PRIVATE:
        return

    message = " ".join(context.args).strip()
    if not message:
        await update.effective_message.reply_text(
            "Please provide a message after the command.\n"
            "Example: /broadcast Office will be closed tomorrow."
        )
        return

    with get_connection() as conn:
        groups = conn.execute(
            "SELECT chat_id, group_name FROM groups ORDER BY group_name"
        ).fetchall()

    if not groups:
        await update.effective_message.reply_text("❌ No groups are registered yet.")
        return

    successful = 0
    inactive = []
    unverified = []
    failed = []

    for chat_id, stored_name in groups:
        # Check the live title immediately before sending. If Telegram cannot
        # verify the title, do not send to this group (fail closed).
        live_chat = await get_live_group(chat_id, context)
        if live_chat is None or live_chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
            unverified.append(stored_name)
            continue

        current_name = live_chat.title or ""
        if current_name:
            with get_connection() as conn:
                conn.execute(
                    "UPDATE groups SET group_name = ? WHERE chat_id = ?",
                    (current_name, chat_id),
                )
        else:
            unverified.append(stored_name)
            continue

        if is_inactive_group_name(current_name):
            inactive.append(current_name)
            continue

        try:
            await context.bot.send_message(chat_id=chat_id, text=message)
            successful += 1
        except RetryAfter as error:
            await asyncio.sleep(float(error.retry_after) + 1)
            # Recheck after waiting: group may have been renamed inactive.
            refreshed = await get_live_group(chat_id, context)
            if refreshed is None or not refreshed.title:
                unverified.append(current_name)
                continue
            if is_inactive_group_name(refreshed.title):
                inactive.append(refreshed.title)
                continue
            try:
                await context.bot.send_message(chat_id=chat_id, text=message)
                successful += 1
            except TelegramError:
                logger.exception("Could not deliver to %s (%s)", current_name, chat_id)
                failed.append(current_name)
        except TelegramError:
            logger.exception("Could not deliver to %s (%s)", current_name, chat_id)
            failed.append(current_name)
        await asyncio.sleep(0.05)

    report = (
        "📢 Broadcast completed!\n\n"
        f"📋 Registered groups: {len(groups)}\n"
        f"✅ Delivered to active groups: {successful}\n"
        f"⏭️ Inactive groups skipped: {len(inactive)}\n"
        f"⚠️ Could not verify (skipped): {len(unverified)}\n"
        f"❌ Delivery failed: {len(failed)}"
    )
    if failed:
        report += "\n\nFailed groups: " + ", ".join(failed[:10])
    if unverified:
        report += "\n\nUnverified groups: " + ", ".join(unverified[:10])
    await update.effective_message.reply_text(report)


def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is missing. Set it in your cloud host's environment variables.")

    init_database()
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("myid", myid))
    app.add_handler(CommandHandler("groupid", groupid))
    app.add_handler(CommandHandler("addgroup", addgroup))
    app.add_handler(CommandHandler("retrywelcome", retrywelcome))
    app.add_handler(CommandHandler("broadcast", broadcast))
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_TITLE, on_group_title_change))
    app.add_handler(
        ChatMemberHandler(on_bot_membership_change, ChatMemberHandler.MY_CHAT_MEMBER)
    )

    logger.info("BUK Speaker Bot is running")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()

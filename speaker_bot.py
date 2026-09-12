import asyncio
import os
import sqlite3

from telegram import Update
from telegram.error import RetryAfter, TelegramError
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# ============================================================
# BUK SPEAKER CONFIGURATION
# ============================================================

# LOCAL USE ONLY:
# If you run the bot only on your own computer, you may replace
# PASTE_YOUR_BOT_TOKEN_HERE with your real BotFather token.
#
# RAILWAY / GITHUB:
# LEAVE THIS PLACEHOLDER UNCHANGED.
# Put your real token in Railway -> Variables as:
# BOT_TOKEN = your_real_token
LOCAL_BOT_TOKEN = "PASTE_YOUR_BOT_TOKEN_HERE"

# Railway BOT_TOKEN overrides the local placeholder automatically.
BOT_TOKEN = os.getenv("BOT_TOKEN", LOCAL_BOT_TOKEN).strip()

if not BOT_TOKEN or BOT_TOKEN == "PASTE_YOUR_BOT_TOKEN_HERE":
    raise RuntimeError(
        "BOT_TOKEN is missing. For Railway, add BOT_TOKEN in Railway Variables. "
        "For local use only, replace PASTE_YOUR_BOT_TOKEN_HERE in speaker_bot.py."
    )

# Your Telegram administrator user ID.
# You can also override this in Railway with ADMIN_USER_ID.
ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID", "488671902"))

# Railway persistent volume path if attached; otherwise use current folder locally.
DATA_DIR = os.getenv("RAILWAY_VOLUME_MOUNT_PATH", ".")
DB_FILE = os.path.join(DATA_DIR, "speaker_groups.db")


# ============================================================
# DATABASE
# ============================================================

def init_database():
    os.makedirs(DATA_DIR, exist_ok=True)

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS groups (
            chat_id INTEGER PRIMARY KEY,
            group_name TEXT NOT NULL
        )
        """
    )

    conn.commit()
    conn.close()


def get_registered_groups():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    cursor.execute(
        "SELECT chat_id, group_name FROM groups ORDER BY group_name"
    )

    groups = cursor.fetchall()
    conn.close()

    return groups


# ============================================================
# SECURITY
# ============================================================

def is_admin(update: Update):
    return (
        update.effective_user is not None
        and update.effective_user.id == ADMIN_USER_ID
    )


# ============================================================
# COMMANDS
# ============================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        await update.message.reply_text(
            "⛔ You are not authorized to use this bot."
        )
        return

    await update.message.reply_text(
        "📢 BUK Speaker is online.\n\n"
        "🔐 Administrator access confirmed.\n"
        "Send a normal text message here to broadcast it "
        "to all registered groups."
    )


async def myid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    await update.message.reply_text(
        f"Your Telegram User ID is:\n{user_id}"
    )


async def groupid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return

    chat_id = update.effective_chat.id
    chat_name = update.effective_chat.title or "Private Chat"

    await update.message.reply_text(
        f"📢 Group Name: {chat_name}\n"
        f"Group ID: {chat_id}"
    )


async def addgroup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        await update.message.reply_text(
            "⛔ You are not authorized to use this bot."
        )
        return

    if update.effective_chat.type == "private":
        await update.message.reply_text(
            "❌ Use /addgroup inside the Telegram group "
            "you want to register."
        )
        return

    chat_id = update.effective_chat.id
    group_name = update.effective_chat.title or "Unnamed Group"

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    cursor.execute(
        """
        INSERT OR REPLACE INTO groups (chat_id, group_name)
        VALUES (?, ?)
        """,
        (chat_id, group_name),
    )

    conn.commit()
    conn.close()

    await update.message.reply_text(
        "✅ Group registered successfully!\n\n"
        f"📢 Group: {group_name}\n"
        f"Group ID: {chat_id}"
    )


async def removegroup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        await update.message.reply_text(
            "⛔ You are not authorized to use this bot."
        )
        return

    if update.effective_chat.type == "private":
        await update.message.reply_text(
            "❌ Use /removegroup inside the group "
            "you want to remove."
        )
        return

    chat_id = update.effective_chat.id
    group_name = update.effective_chat.title or "Unnamed Group"

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    cursor.execute(
        "DELETE FROM groups WHERE chat_id = ?",
        (chat_id,),
    )

    removed = cursor.rowcount
    conn.commit()
    conn.close()

    if removed:
        await update.message.reply_text(
            f"✅ {group_name} was removed from BUK Speaker."
        )
    else:
        await update.message.reply_text(
            "ℹ️ This group was not registered."
        )


async def groups_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        await update.message.reply_text(
            "⛔ You are not authorized to use this bot."
        )
        return

    groups = get_registered_groups()

    if not groups:
        await update.message.reply_text(
            "📭 No groups are registered yet."
        )
        return

    preview_limit = 50
    lines = [
        f"{index}. {group_name}"
        for index, (_, group_name) in enumerate(
            groups[:preview_limit],
            start=1,
        )
    ]

    text = (
        f"📢 Registered groups: {len(groups)}\n\n"
        + "\n".join(lines)
    )

    if len(groups) > preview_limit:
        text += (
            f"\n\n…and {len(groups) - preview_limit} more groups."
        )

    await update.message.reply_text(text)


# ============================================================
# BROADCAST
# ============================================================

def _retry_seconds(retry_after):
    if hasattr(retry_after, "total_seconds"):
        return float(retry_after.total_seconds())

    return float(retry_after)


async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        await update.message.reply_text(
            "⛔ You are not authorized to use this bot."
        )
        return

    if update.effective_chat.type != "private":
        return

    message = update.message.text
    groups = get_registered_groups()

    if not groups:
        await update.message.reply_text(
            "❌ No groups are registered yet.\n\n"
            "Add BUK Speaker to a group and use /addgroup there."
        )
        return

    successful = 0
    failed = []

    for chat_id, group_name in groups:
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text=message,
            )
            successful += 1

        except RetryAfter as exc:
            delay = _retry_seconds(exc.retry_after) + 1
            await asyncio.sleep(delay)

            try:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=message,
                )
                successful += 1
            except TelegramError:
                failed.append(group_name)

        except TelegramError:
            failed.append(group_name)

        await asyncio.sleep(0.05)

    report = (
        "📢 Broadcast completed!\n\n"
        f"✅ Delivered: {successful}/{len(groups)}\n"
        f"❌ Failed: {len(failed)}"
    )

    if failed:
        preview = ", ".join(failed[:10])
        report += f"\n\nFailed groups: {preview}"

        if len(failed) > 10:
            report += f" (+{len(failed) - 10} more)"

    await update.message.reply_text(report)


# ============================================================
# START BOT
# ============================================================

def main():
    init_database()

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("myid", myid))
    app.add_handler(CommandHandler("groupid", groupid))
    app.add_handler(CommandHandler("addgroup", addgroup))
    app.add_handler(CommandHandler("removegroup", removegroup))
    app.add_handler(CommandHandler("groups", groups_command))

    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            broadcast,
        )
    )

    print(
        "BUK Speaker Bot is running - "
        "ADMIN PROTECTION ENABLED"
    )

    app.run_polling()


if __name__ == "__main__":
    main()

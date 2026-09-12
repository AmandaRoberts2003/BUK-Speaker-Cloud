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

BOT_TOKEN = os.getenv("8985960287:AAF2MSCFXeBG_JEiZu0eiPUY8tCReYuPs2A")
ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID", "488671902"))

DATA_DIR = os.getenv("RAILWAY_VOLUME_MOUNT_PATH", ".")
DB_FILE = os.path.join(DATA_DIR, "speaker_groups.db")


def init_database():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS groups (
            chat_id INTEGER PRIMARY KEY,
            group_name TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()


def is_admin(update: Update):
    return (
        update.effective_user is not None
        and update.effective_user.id == ADMIN_USER_ID
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        await update.message.reply_text("⛔ You are not authorized to use this bot.")
        return

    await update.message.reply_text(
        "✅ BUK Speaker Bot is working!\n\n"
        "Administrator access confirmed."
    )


async def myid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    await update.message.reply_text(f"Your Telegram User ID is:\n{user_id}")


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
        await update.message.reply_text("⛔ You are not authorized to use this bot.")
        return

    if update.effective_chat.type == "private":
        await update.message.reply_text(
            "❌ Please use /addgroup inside the Telegram group you want to register."
        )
        return

    chat_id = update.effective_chat.id
    group_name = update.effective_chat.title or "Unnamed Group"

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT OR REPLACE INTO groups (chat_id, group_name) VALUES (?, ?)",
        (chat_id, group_name),
    )
    conn.commit()
    conn.close()

    await update.message.reply_text(
        f"✅ Group registered successfully!\n\n"
        f"📢 Group: {group_name}\n"
        f"Group ID: {chat_id}"
    )


async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        await update.message.reply_text("⛔ You are not authorized to use this bot.")
        return

    if update.effective_chat.type != "private":
        return

    message = update.message.text

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT chat_id, group_name FROM groups")
    groups = cursor.fetchall()
    conn.close()

    if not groups:
        await update.message.reply_text("❌ No groups are registered yet.")
        return

    successful = 0
    failed = []

    for chat_id, group_name in groups:
        try:
            await context.bot.send_message(chat_id=chat_id, text=message)
            successful += 1

        except RetryAfter as e:
            await asyncio.sleep(float(e.retry_after) + 1)
            try:
                await context.bot.send_message(chat_id=chat_id, text=message)
                successful += 1
            except TelegramError:
                failed.append(group_name)

        except TelegramError:
            failed.append(group_name)

        # Pace broadcasts to reduce Telegram flood-limit errors at larger scale.
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


def main():
    init_database()

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("myid", myid))
    app.add_handler(CommandHandler("groupid", groupid))
    app.add_handler(CommandHandler("addgroup", addgroup))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, broadcast))

    print("BUK Speaker Bot is running - CLOUD MODE")
    app.run_polling()


if __name__ == "__main__":
    main()

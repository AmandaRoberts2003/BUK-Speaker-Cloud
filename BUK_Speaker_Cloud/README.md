# BUK Speaker Cloud

Cloud-ready version of BUK Speaker.

Railway configuration:
- Start command: `python speaker_bot.py`
- Variable: `BOT_TOKEN` = your private BotFather token
- Optional variable: `ADMIN_USER_ID` = your Telegram numeric user ID
- Attach a persistent volume. The bot automatically stores `speaker_groups.db`
  in Railway's volume mount path.

Do not put the real Telegram bot token in this repository.

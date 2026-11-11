from telegram import Bot

from config import get_settings


async def send_alert(message: str) -> None:
    settings = get_settings()
    if settings.telegram_bot_token is None or settings.telegram_chat_id is None:
        raise RuntimeError("Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID")

    chat_id = settings.telegram_chat_id
    async with Bot(settings.telegram_bot_token.get_secret_value()) as bot:
        await bot.send_message(chat_id=chat_id if chat_id.startswith("@") else int(chat_id), text=message)

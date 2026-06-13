import logging

from telegram.ext import Application, CommandHandler

from bot.config import settings
from bot.handlers.start import start_command
from bot.handlers.whoami import whoami_command

logging.basicConfig(
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    level=settings.log_level,
)

logging.getLogger("httpx").setLevel(logging.WARNING)


def main() -> None:
    """Build the Telegram bot Application and run it."""
    app = Application.builder().token(settings.telegram_bot_token).build()

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("whoami", whoami_command))

    # Long-polling: dial out to Telegram, ask for updates in a loop.
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
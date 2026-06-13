import logging

from telegram.ext import Application, CommandHandler

from bot.config import settings
from bot.handlers.start import start_command


logging.basicConfig(
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    level=settings.log_level,
)
# PTB's HTTPX is chatty at INFO; quiet it down for our log level.
logging.getLogger("httpx").setLevel(logging.WARNING)


def main() -> None:
    """Build the Telegram bot Application and run it."""
    app = Application.builder().token(settings.telegram_bot_token).build()

    app.add_handler(CommandHandler("start", start_command))

    # Long-polling: dial out to Telegram, ask for updates in a loop.
    # PTB owns the event loop from here until Ctrl+C.
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
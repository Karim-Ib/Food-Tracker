import logging

from telegram.ext import Application, CommandHandler
from telegram import BotCommand

from bot.config import settings
from bot.handlers.start import start_command
from bot.handlers.whoami import whoami_command
from bot.handlers.log import build_log_handler
from bot.handlers.logmeal import build_logmeal_handler
from bot.handlers.today import today_command
from bot.handlers.weight import weight_command
from bot.handlers.status import status_command, week_command
from bot.handlers.goal import goal_command
from bot.handlers.help import help_command

logging.basicConfig(
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    level=settings.log_level,
)

logging.getLogger("httpx").setLevel(logging.WARNING)

COMMANDS = [
    BotCommand("log", "Log a meal (text, or send a barcode photo)"),
    BotCommand("logmeal", "Log a full meal by description (AI estimate)"),
    BotCommand("today", "Today's meals and totals"),
    BotCommand("status", "Today's macros vs goals"),
    BotCommand("week", "This week so far"),
    BotCommand("weight", "Log or view body weight"),
    BotCommand("goal", "Set or view daily targets"),
    BotCommand("whoami", "Your profile"),
    BotCommand("help", "Show all commands"),
]


async def _post_init(application) -> None:
    """Runs once after the bot is initialized, before polling starts."""
    await application.bot.set_my_commands(COMMANDS)



def main() -> None:
    """Build the Telegram bot Application and run it."""
    app = (
        Application.builder()
        .token(settings.telegram_bot_token)
        .post_init(_post_init)
        .build()
    )



    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("whoami", whoami_command))
    app.add_handler(build_log_handler())
    app.add_handler(build_logmeal_handler())
    app.add_handler(CommandHandler("today", today_command))
    app.add_handler(CommandHandler("weight", weight_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("week", week_command))
    app.add_handler(CommandHandler("goal", goal_command))
    app.add_handler(CommandHandler("help", help_command))
    # Long-polling: dial out to Telegram, ask for updates in a loop.
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
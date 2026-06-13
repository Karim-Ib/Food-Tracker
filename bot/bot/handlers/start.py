import logging

from telegram import Update
from telegram.ext import ContextTypes

from bot.api_client import client

log = logging.getLogger(__name__)


async def start_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Greet the user. Also pings the API as a connectivity smoke test."""
    user = update.effective_user

    # Sanity-check the backend is reachable before promising anything.
    try:
        await client.health()
    except Exception as exc:
        log.warning("API health check failed: %s", exc)
        await update.message.reply_text(
            "Hi! I'm running, but I can't reach my backend right now. "
            "Try again in a moment, or tell the admin if it persists."
        )
        return

    await update.message.reply_text(
        f"Hi {user.first_name}! I'm FoodBot.\n\n"
        f"I'll help you track what you eat. More commands coming soon."
    )
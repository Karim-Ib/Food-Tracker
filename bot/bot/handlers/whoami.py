from telegram import Update
from telegram.ext import ContextTypes

from bot.api_client import client


async def whoami_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Show the user their stored profile. Creates one on first use."""
    tg_user = update.effective_user

    # Try existing profile first
    profile = await client.get_user_by_telegram_id(tg_user.id)

    # First-time user: create on the fly
    if profile is None:
        profile = await client.create_user(
            telegram_id=tg_user.id,
            display_name=tg_user.first_name or "Anonymous",
        )
        intro = "Welcome! I just created your profile.\n\n"
    else:
        intro = ""

    status = (
        "active" if profile["is_active"]
        else "awaiting admin approval"
    )

    await update.message.reply_text(
        f"{intro}"
        f"Name: {profile['display_name']}\n"
        f"Telegram ID: {profile['telegram_id']}\n"
        f"Timezone: {profile['timezone']}\n"
        f"Status: {status}"
    )
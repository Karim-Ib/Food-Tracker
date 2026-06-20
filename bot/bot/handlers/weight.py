"""/weight — log body weight, or show recent entries when called with no args."""
from datetime import datetime
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

import httpx
from telegram import Update
from telegram.ext import ContextTypes

from bot.api_client import client


async def weight_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    tg_id = update.effective_user.id

    try:
        user = await client.get_user_by_telegram_id(tg_id)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            await update.message.reply_text("I don't know you yet — run /whoami first.")
            return
        raise

    if not user["is_active"]:
        await update.message.reply_text("Your account is awaiting admin approval.")
        return

    # No args → recent entries view
    if not context.args:
        await _show_recent(update, user)
        return

    # First arg is weight; remaining args are an optional note
    raw = context.args[0].replace(",", ".")
    try:
        weight = Decimal(raw)
    except InvalidOperation:
        await update.message.reply_text(
            "Not a valid weight. Try `/weight 80.5` or `/weight` to see history.",
            parse_mode="Markdown",
        )
        return

    if weight <= 0 or weight > 500:
        await update.message.reply_text("Weight must be between 0 and 500 kg.")
        return

    notes = " ".join(context.args[1:]) or None

    try:
        await client.create_body_metric(
            user_id=user["id"],
            weight_kg=float(weight),
            notes=notes,
        )
    except Exception:
        await update.message.reply_text("Couldn't save. Try again in a moment.")
        return

    await update.message.reply_text(f"Logged {weight} kg.")


async def _show_recent(update: Update, user: dict) -> None:
    entries = await client.get_recent_body_metrics(user["id"], limit=10)
    if not entries:
        await update.message.reply_text("No weight entries yet. Try `/weight 80.5`.", parse_mode="Markdown")
        return

    user_tz = ZoneInfo(user["timezone"])
    lines = ["Recent weight:", ""]
    for e in entries:
        if e.get("weight_kg") is None:
            continue
        local = datetime.fromisoformat(e["recorded_at"]).astimezone(user_tz)
        when = local.strftime("%Y-%m-%d %H:%M")
        lines.append(f"{when}  {Decimal(e['weight_kg']):.1f} kg")

    await update.message.reply_text("\n".join(lines))
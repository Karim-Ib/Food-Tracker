"""/goal — set or view daily macro targets."""
from decimal import Decimal, InvalidOperation

import httpx
from telegram import Update
from telegram.ext import ContextTypes

from bot.api_client import client
from bot.handlers.status import GOAL_FIELDS, _resolve_active_user


def _format_current(targets: dict) -> str:
    def show(key: str, unit: str) -> str:
        v = targets.get(key)
        return f"{Decimal(v):.0f} {unit}" if v is not None else f"— {unit}"
    # Weight is shown but not settable here — it's an endpoint, not a daily
    # quota, and /weight_model owns it so there's one place that sets it.
    weight = targets.get("goal_weight_kg")
    weight_line = f"{Decimal(weight):.1f} kg" if weight is not None else "— (/weight_model goal 86)"
    return (
        "Current goals:\n"
        f"  kcal:    {show('daily_kcal_target', 'kcal')}\n"
        f"  protein: {show('daily_protein_target_g', 'g')}\n"
        f"  fat:     {show('daily_fat_target_g', 'g')}\n"
        f"  carbs:   {show('daily_carbs_target_g', 'g')}\n"
        f"  weight:  {weight_line}"
    )


async def goal_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = await _resolve_active_user(update)
    if user is None:
        return

    # Bare /goal -> show current targets.
    if not context.args:
        status = await client.get_status(user["id"])
        await update.message.reply_text(
            f"{_format_current(status['targets'])}\n\n"
            "Set one with `/goal kcal 2300` (fields: kcal, protein, fat, carbs).",
            parse_mode="Markdown",
        )
        return

    if len(context.args) != 2:
        await update.message.reply_text(
            "Usage: `/goal <field> <value>` — e.g. `/goal protein 180`.\n"
            "Fields: kcal, protein, fat, carbs.",
            parse_mode="Markdown",
        )
        return

    token, raw = context.args[0].lower(), context.args[1].replace(",", ".")
    if token not in GOAL_FIELDS:
        await update.message.reply_text(
            f"Unknown field '{token}'. Use: kcal, protein, fat, carbs."
        )
        return

    try:
        value = Decimal(raw)
    except InvalidOperation:
        await update.message.reply_text(f"'{raw}' isn't a number.")
        return
    if value < 0:
        await update.message.reply_text("Goal can't be negative.")
        return

    label, api_field = GOAL_FIELDS[token]
    try:
        await client.update_goal(user["id"], api_field, float(value))
    except Exception:
        await update.message.reply_text("Couldn't save the goal. Try again.")
        return

    await update.message.reply_text(f"Set {label} goal to {value:.0f}.")
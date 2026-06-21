"""/status and /week — read-side macro summaries."""
from datetime import date
from decimal import Decimal

import httpx
from telegram import Update
from telegram.ext import ContextTypes

from bot.api_client import client

# user-facing token -> (label, API field name)
GOAL_FIELDS = {
    "kcal": ("kcal", "daily_kcal_target"),
    "protein": ("protein", "daily_protein_target_g"),
    "fat": ("fat", "daily_fat_target_g"),
    "carbs": ("carbs", "daily_carbs_target_g"),
}


async def _resolve_active_user(update: Update) -> dict | None:
    """Shared preamble: fetch user, gate on 404 and is_active. None means stop."""
    tg_id = update.effective_user.id
    try:
        user = await client.get_user_by_telegram_id(tg_id)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            await update.message.reply_text("I don't know you yet — run /whoami first.")
            return None
        raise
    if not user["is_active"]:
        await update.message.reply_text("Your account is awaiting admin approval.")
        return None
    return user


def _fmt_target(total: str, target: str | None, unit: str) -> str:
    """Render 'value / target unit' or 'value unit (no goal set)'."""
    t = Decimal(total)
    if target is None:
        return f"{t:.0f} {unit} (no goal set)"
    return f"{t:.0f} / {Decimal(target):.0f} {unit}"


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = await _resolve_active_user(update)
    if user is None:
        return

    data = await client.get_status(user["id"])
    totals, targets = data["totals"], data["targets"]

    lines = [
        f"Today ({data['day']})",
        "",
        _fmt_target(totals["kcal"], targets["daily_kcal_target"], "kcal"),
        _fmt_target(totals["protein"], targets["daily_protein_target_g"], "g protein"),
        _fmt_target(totals["fat"], targets["daily_fat_target_g"], "g fat"),
        _fmt_target(totals["carbs"], targets["daily_carbs_target_g"], "g carbs"),
    ]
    await update.message.reply_text("\n".join(lines))


async def week_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = await _resolve_active_user(update)
    if user is None:
        return

    data = await client.get_week(user["id"])
    days = data["days"]

    if not any(Decimal(d["kcal"]) > 0 for d in days):
        await update.message.reply_text(
            f"No meals logged yet this week (since {data['week_start']})."
        )
        return

    lines = [f"This week (from {data['week_start']})", ""]
    for d in days:
        day_label = date.fromisoformat(d["day"]).strftime("%a %d")  # "Wed 18"
        kcal = Decimal(d["kcal"])
        protein = Decimal(d["protein"])
        lines.append(f"{day_label}  {kcal:>5.0f} kcal · {protein:.0f}g P")

    t = data["totals"]
    lines.append("")
    lines.append(
        f"Total  {Decimal(t['kcal']):.0f} kcal · "
        f"P {Decimal(t['protein']):.0f}g · "
        f"F {Decimal(t['fat']):.0f}g · "
        f"C {Decimal(t['carbs']):.0f}g"
    )
    await update.message.reply_text("\n".join(lines))
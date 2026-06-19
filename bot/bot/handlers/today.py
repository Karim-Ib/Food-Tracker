"""/today — show today's meal log and macro totals."""
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import httpx
from telegram import Update
from telegram.ext import ContextTypes

from bot.api_client import client


async def today_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    tg_id = update.effective_user.id

    try:
        user = await client.get_user_by_telegram_id(tg_id)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            await update.message.reply_text(
                "I don't know you yet — run /whoami first."
            )
            return
        raise

    if not user["is_active"]:
        await update.message.reply_text(
            "Your account is awaiting admin approval."
        )
        return

    payload = await client.get_today(user["id"])

    entries = payload["entries"]
    totals = payload["totals"]
    day = payload["day"]

    if not entries:
        await update.message.reply_text(f"No meals logged yet on {day}.")
        return

    user_tz = ZoneInfo(user["timezone"])

    lines = [f"📅 {day}", ""]
    for e in entries:
        local_time = datetime.fromisoformat(e["consumed_at"]).astimezone(user_tz)
        time_str = local_time.strftime("%H:%M")
        kcal = int(Decimal(e["kcal"]))
        weight = int(Decimal(e["weight_g"]))
        lines.append(f"{time_str}  {e['food_name']} — {weight}g, {kcal} kcal")

    lines.append("")
    lines.append(
        f"Totals: {int(Decimal(totals['kcal']))} kcal · "
        f"P {Decimal(totals['protein']):.0f}g · "
        f"F {Decimal(totals['fat']):.0f}g · "
        f"C {Decimal(totals['carbs']):.0f}g"
    )

    await update.message.reply_text("\n".join(lines))
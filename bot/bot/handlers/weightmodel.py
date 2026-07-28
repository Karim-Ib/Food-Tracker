"""/weight_model — OLS weight trend chart, optionally projected N months out.

    /weight_model      -> fit over every logged weight, default 152-day projection
    /weight_model 6    -> same, projection extended to ~6 months

The chart is rendered server-side and arrives as a PNG; the caption carries the
fit, the 14-day step-down trigger, and the crossing dates.

The dashed line and those dates are a CONSTANT-RATE COUNTERFACTUAL — an
optimistic upper bound, not a forecast. Real loss decelerates, so true dates
land later. The caption says so every time; that wording is deliberate and
should not be trimmed for brevity.
"""
import asyncio
import logging
from datetime import date

import httpx
from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import ContextTypes

from bot.api_client import client

log = logging.getLogger(__name__)

# Mirrors the API's own default horizon (weight_trend.DEFAULT_HORIZON_DAYS).
DEFAULT_HORIZON_DAYS = 152

_MEAN_MONTH_DAYS = 30.44
_MAX_MONTHS = 36  # the API caps horizon_days at 1096


async def weight_model_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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

    horizon_days = _parse_horizon(context.args)
    if horizon_days is None:
        await update.message.reply_text(
            "Usage: /weight_model  or  /weight_model <months>\n"
            f"e.g. /weight_model 6 — project 6 months out (max {_MAX_MONTHS})."
        )
        return

    await context.bot.send_chat_action(
        chat_id=update.effective_chat.id, action=ChatAction.UPLOAD_PHOTO
    )

    try:
        png, summary = await asyncio.gather(
            client.get_weight_model_chart(user["id"], horizon_days),
            client.get_weight_model(user["id"], horizon_days),
        )
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 422:
            await update.message.reply_text(
                "Not enough measured weigh-ins yet — I need at least 3 to fit a "
                "trend. Log a few more with /weight 80.5."
            )
        else:
            log.warning("weight model request failed: %s", exc)
            await update.message.reply_text("Couldn't build the model. Try again in a moment.")
        return
    except Exception:
        log.exception("weight model unexpected error")
        await update.message.reply_text("Couldn't build the model. Try again later.")
        return

    await update.message.reply_photo(photo=png, caption=_caption(summary))


def _parse_horizon(args: list[str]) -> int | None:
    """Args -> projection horizon in days. None means the input was unusable."""
    if not args:
        return DEFAULT_HORIZON_DAYS

    try:
        months = float(args[0].replace(",", "."))
    except ValueError:
        return None
    if months < 0 or months > _MAX_MONTHS:
        return None

    return round(months * _MEAN_MONTH_DAYS)


def _caption(summary: dict) -> str:
    """Fit + trigger + crossings, with the upper-bound framing attached."""
    fit = summary["fit"]

    lines = [
        f"{fit['slope_per_week']:+.2f} kg/wk "
        f"(95% CI {fit['ci_low_per_week']:+.2f} to {fit['ci_high_per_week']:+.2f}) · "
        f"r²={fit['r2']:.2f} · n={fit['n']}",
    ]
    if summary["seed_count"]:
        lines.append(f"{summary['seed_count']} seed row(s) excluded from the fit.")

    trigger = summary["trigger"]
    lines.append("")
    if trigger["rolling_slope"] is None:
        lines.append(f"{trigger['window_days']}d rolling: not enough points in the window.")
    else:
        lines.append(
            f"{trigger['window_days']}d rolling: {trigger['rolling_slope']:+.2f} kg/wk "
            f"— {trigger['reason']}."
        )

    upcoming = [
        c for c in summary["projection"]
        if not c["already_passed"] and c["within_horizon"]
    ]
    if upcoming:
        upcoming.sort(key=lambda c: c["crossing_date"])
        lines.append("")
        lines.append("At this exact rate (upper bound — you'd hit these no sooner):")
        for c in upcoming:
            when = date.fromisoformat(c["crossing_date"]).strftime("%d %b %Y")
            lines.append(f"  {c['target_kg']:.0f} kg — {when}")

    lines.append("")
    lines.append(summary["projection_disclaimer"])

    return "\n".join(lines)

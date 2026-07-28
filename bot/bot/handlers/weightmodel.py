"""/weight_model — OLS weight trend chart, optionally projected N months out.

    /weight_model            -> fit over every logged weight, default projection
    /weight_model 6          -> same, projection extended to ~6 months
    /weight_model goal 86    -> set the target weight, then chart
    /weight_model 6 goal 86  -> both, in either order

The goal persists on the user, so later bare calls reuse it. Target lines on the
chart are generated between the goal and the highest measured weight, so the
chart centres itself on whatever range that user actually occupies.

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

_USAGE = (
    "Usage:\n"
    "  /weight_model — chart with your current goal\n"
    "  /weight_model <months> — project that far out\n"
    "  /weight_model goal <kg> — set your target weight\n"
    "  /weight_model <months> goal <kg> — both\n\n"
    f"e.g. /weight_model 6 goal 86  (max {_MAX_MONTHS} months)"
)


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

    parsed = _parse_args(context.args)
    if parsed is None:
        await update.message.reply_text(_USAGE)
        return
    horizon_days, goal_kg = parsed

    # Persist the goal before rendering, so the chart and the summary both read
    # it from the same place rather than the bot passing it around.
    if goal_kg is not None:
        try:
            await client.update_goal(user["id"], "goal_weight_kg", goal_kg)
        except Exception:
            log.exception("saving weight goal failed")
            await update.message.reply_text("Couldn't save that goal. Try again.")
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


def _parse_args(args: list[str]) -> tuple[int, float | None] | None:
    """Args -> (horizon_days, goal_kg). None means the input was unusable.

    Both options are optional and order-independent: a bare number is months,
    and `goal` consumes the token after it. A goal of None means "leave whatever
    is stored alone" — not "clear it".
    """
    months: float | None = None
    goal: float | None = None

    i = 0
    while i < len(args):
        token = args[i].lower()

        if token == "goal":
            if goal is not None or i + 1 >= len(args):
                return None
            try:
                goal = float(args[i + 1].replace(",", "."))
            except ValueError:
                return None
            if not 0 < goal <= 500:
                return None
            i += 2
            continue

        if months is not None:
            return None
        try:
            months = float(token.replace(",", "."))
        except ValueError:
            return None
        if months < 0 or months > _MAX_MONTHS:
            return None
        i += 1

    horizon = (
        DEFAULT_HORIZON_DAYS if months is None else round(months * _MEAN_MONTH_DAYS)
    )
    return horizon, goal


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

    if summary.get("goal_weight_kg") is None:
        lines.append("")
        lines.append("No goal weight set — /weight_model goal 86 to add target lines.")
        return "\n".join(lines)

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
            marker = "  ← goal" if c["is_goal"] else ""
            lines.append(f"  {c['target_kg']:g} kg — {when}{marker}")

    lines.append("")
    lines.append(summary["projection_disclaimer"])

    return "\n".join(lines)

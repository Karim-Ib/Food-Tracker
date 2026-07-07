"""/logmeal — log a whole meal from a free-text description.

Unlike /log (which resolves a food, then asks for grams), this path hands the
entire description to the LLM meal estimator, which returns per-100g macros plus
a portion size (stated in the description, or estimated). There is no grams
question: the confirm card already shows the meal's totals.

Flow:
    /logmeal <describe the meal>
        -> LLM estimate                     (CONFIRMING_MEAL)
        Save  -> persist food + one entry, done
        Cancel -> abort

The estimate is intentionally conservative on fat/carbs (buffered server-side),
because verbal descriptions of takeout/restaurant food hide oil, butter, and
sugar. A precise description is what makes the numbers meaningful — hence the
disclaimer up front.
"""
import logging

import httpx
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
)

from bot.api_client import client

log = logging.getLogger(__name__)

CONFIRMING_MEAL = 0

KEY_MEAL = "logmeal_parsed"
KEY_USER_ID = "user_id"  # shared cache key with the /log flow

# Only these keys go to POST /foods — portion/confidence are used bot-side.
_FOOD_FIELDS = (
    "name", "brand", "kcal_100g", "protein_100g", "fat_100g", "carbs_100g",
    "fiber_100g", "sugar_100g", "sat_fat_100g",
)

DISCLAIMER = (
    "⚠️ This estimates a whole meal from your words alone, so it's only as good "
    "as the detail you give it. Name the components and a portion size, e.g.\n"
    "`/logmeal chicken shawarma wrap with garlic sauce and fries, ~450g`\n"
    "If you don't state a portion, I'll estimate one. Fat and carbs are "
    "deliberately rounded up to stay on the safe side for takeout-style food."
)


async def logmeal_start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    raw = " ".join(context.args).strip()
    if not raw:
        await update.message.reply_text(
            "Usage: `/logmeal <describe the whole meal>`\n\n" + DISCLAIMER,
            parse_mode="Markdown",
        )
        return ConversationHandler.END

    await context.bot.send_chat_action(
        chat_id=update.effective_chat.id, action=ChatAction.TYPING
    )

    try:
        meal = await client.parse_meal(raw)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 422:
            await update.message.reply_text(
                "I couldn't make sense of that as a meal. "
                "Try describing it more precisely."
            )
        else:
            log.warning("parse_meal failed: %s", exc)
            await update.message.reply_text(
                "Couldn't estimate that right now. Try again in a moment."
            )
        return ConversationHandler.END
    except Exception:
        log.exception("parse_meal unexpected error")
        await update.message.reply_text("Couldn't estimate that. Try again later.")
        return ConversationHandler.END

    context.user_data[KEY_MEAL] = meal

    keyboard = [[
        InlineKeyboardButton("Save", callback_data="save"),
        InlineKeyboardButton("Cancel", callback_data="cancel"),
    ]]
    await update.message.reply_text(
        _summary(meal),
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )
    return CONFIRMING_MEAL


async def logmeal_confirm(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    query = update.callback_query
    await query.answer()

    if query.data == "cancel":
        await query.edit_message_text("Cancelled.")
        return ConversationHandler.END

    user_id = await _resolve_user_id(update, context)
    if user_id is None:
        await query.edit_message_text("You don't have a profile yet. Send /whoami first.")
        return ConversationHandler.END

    meal = context.user_data[KEY_MEAL]
    portion = float(meal["portion_weight_g"])

    # Persist honestly per-100g (buffer + kcal recompute already happened in the
    # API), then log a single entry at the portion weight.
    food_payload = {k: meal[k] for k in _FOOD_FIELDS if k in meal}
    try:
        saved = await client.create_food_from_parse(food_payload, user_id)
    except Exception:
        log.exception("create_food_from_parse (meal) failed")
        await query.edit_message_text("Couldn't save the meal. Try again.")
        return ConversationHandler.END

    try:
        await client.create_meal_entry(
            user_id=user_id,
            food_id=saved["id"],
            weight_g=portion,
        )
    except Exception:
        log.exception("create_meal_entry (meal) failed")
        await query.edit_message_text(
            "Saved the food but couldn't log it. Add it with /log."
        )
        return ConversationHandler.END

    await query.edit_message_text(f"Logged {portion:.0f}g of {saved['name']}.")
    return ConversationHandler.END


def _summary(meal: dict) -> str:
    """Confirm card: dish name, portion, and the resulting meal totals."""
    portion = float(meal["portion_weight_g"])
    factor = portion / 100
    kcal = float(meal["kcal_100g"]) * factor
    protein = float(meal["protein_100g"]) * factor
    fat = float(meal["fat_100g"]) * factor
    carbs = float(meal["carbs_100g"]) * factor

    title = f"*{meal['name']}*"
    if meal.get("brand"):
        title += f" — {meal['brand']}"
    portion_note = "estimated" if meal.get("portion_estimated") else "as described"

    lines = [
        title,
        f"Portion: {portion:.0f}g ({portion_note})",
        "",
        f"  {kcal:.0f} kcal",
        f"  {protein:.1f}g protein",
        f"  {fat:.1f}g fat",
        f"  {carbs:.1f}g carbs",
    ]

    conf = meal.get("confidence") or {}
    if conf:
        lines += [
            "",
            f"_Confidence — protein: {conf.get('protein', '?')}, "
            f"fat: {conf.get('fat', '?')}, carbs: {conf.get('carbs', '?')}. "
            f"Fat/carbs rounded up for safety._",
        ]

    return "\n".join(lines)


async def _resolve_user_id(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int | None:
    """Resolve and cache the internal user id. None if no profile exists."""
    user_id = context.user_data.get(KEY_USER_ID)
    if user_id is not None:
        return user_id
    profile = await client.get_user_by_telegram_id(update.effective_user.id)
    if profile is None:
        return None
    user_id = profile["id"]
    context.user_data[KEY_USER_ID] = user_id
    return user_id


async def cancel_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    await update.message.reply_text("Cancelled.")
    return ConversationHandler.END


def build_logmeal_handler() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CommandHandler("logmeal", logmeal_start)],
        states={
            CONFIRMING_MEAL: [CallbackQueryHandler(logmeal_confirm)],
        },
        fallbacks=[CommandHandler("cancel", cancel_command)],
    )

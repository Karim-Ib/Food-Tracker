"""
/log command — multi-step conversation for logging a meal entry.

Flow (DB hit):
    /log <query>     -->  search foods, inline keyboard            (CHOOSING_FOOD)
    [tap a food]          ask for weight                           (ENTERING_WEIGHT)
    [type a number]       show macros + Confirm/Cancel             (CONFIRMING)
    [tap Confirm]         POST meal entry, reply summary           (END)

Flow (DB miss, LLM fallback):
    /log <query>     -->  DB empty
                          [typing...] silently call /foods/parse
                          show parsed nutrition + Save/Cancel      (CONFIRMING_PARSED_FOOD)
    [tap Save]            POST /foods, merge into weight-entry     (ENTERING_WEIGHT)
    [tap Cancel]                                                   (END)
    [parse fails]         show "couldn't parse" message            (END)
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
    MessageHandler,
    filters,
)

from bot.api_client import client

log = logging.getLogger(__name__)

# Conversation states.
CHOOSING_FOOD, ENTERING_WEIGHT, CONFIRMING, CONFIRMING_PARSED_FOOD = range(4)

# context.user_data keys.
KEY_FOODS = "log_foods"
KEY_FOOD = "log_food"
KEY_WEIGHT = "log_weight"
KEY_USER_ID = "user_id"
KEY_PARSED = "log_parsed"


async def log_start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    """Entry point: /log <query>. Search DB; on miss, fall through to LLM."""
    query = " ".join(context.args).strip()
    if not query:
        await update.message.reply_text(
            "Usage: `/log <food name>`", parse_mode="Markdown"
        )
        return ConversationHandler.END

    try:
        foods = await client.search_foods(query=query, limit=8)
    except Exception as exc:
        log.warning("search_foods failed: %s", exc)
        await update.message.reply_text(
            "Couldn't reach the backend. Try again in a moment."
        )
        return ConversationHandler.END

    if not foods:
        return await _parse_via_llm(update, context, query)

    context.user_data[KEY_FOODS] = {f["id"]: f for f in foods}

    keyboard = [
        [InlineKeyboardButton(f["name"], callback_data=f"food:{f['id']}")]
        for f in foods
    ]
    keyboard.append([InlineKeyboardButton("Cancel", callback_data="cancel")])

    await update.message.reply_text(
        f"Found {len(foods)} matches for '{query}'. Pick one:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return CHOOSING_FOOD


async def _parse_via_llm(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    description: str,
) -> int:
    """DB returned empty. Silently call /foods/parse and show the result."""
    await context.bot.send_chat_action(
        chat_id=update.effective_chat.id,
        action=ChatAction.TYPING,
    )

    try:
        parsed = await client.parse_food(description)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 422:
            await update.message.reply_text(
                f"I couldn't recognize '{description}'. Try describing it differently."
            )
        else:
            log.warning("parse_food failed: %s", exc)
            await update.message.reply_text(
                "Couldn't parse that right now. Try again in a moment."
            )
        return ConversationHandler.END
    except Exception:
        log.exception("parse_food unexpected error")
        await update.message.reply_text("Couldn't parse that. Try again later.")
        return ConversationHandler.END

    context.user_data[KEY_PARSED] = parsed

    title = f"*{parsed['name']}*"
    if parsed.get("brand"):
        title += f" — {parsed['brand']}"

    msg = (
        f"{title}\n"
        f"Per 100g:\n"
        f"  {parsed['kcal_100g']:.0f} kcal\n"
        f"  {parsed['protein_100g']:.1f}g protein\n"
        f"  {parsed['fat_100g']:.1f}g fat\n"
        f"  {parsed['carbs_100g']:.1f}g carbs"
    )

    keyboard = [[
        InlineKeyboardButton("Save", callback_data="save"),
        InlineKeyboardButton("Cancel", callback_data="cancel"),
    ]]

    await update.message.reply_text(
        msg,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )
    return CONFIRMING_PARSED_FOOD


async def confirm_parsed_food(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    """User tapped Save or Cancel on the LLM-parsed food."""
    query = update.callback_query
    await query.answer()

    if query.data == "cancel":
        await query.edit_message_text("Cancelled.")
        return ConversationHandler.END

    user_id = context.user_data.get(KEY_USER_ID)
    if user_id is None:
        profile = await client.get_user_by_telegram_id(update.effective_user.id)
        if profile is None:
            await query.edit_message_text(
                "You don't have a profile yet. Send /whoami first."
            )
            return ConversationHandler.END
        user_id = profile["id"]
        context.user_data[KEY_USER_ID] = user_id

    parsed = context.user_data[KEY_PARSED]

    try:
        saved_food = await client.create_food_from_parse(parsed, user_id)
    except Exception:
        log.exception("create_food_from_parse failed")
        await query.edit_message_text("Couldn't save the food. Try /log again.")
        return ConversationHandler.END

    # Merge into the existing weight-entry flow.
    context.user_data[KEY_FOOD] = saved_food

    keyboard = [[InlineKeyboardButton("Cancel", callback_data="cancel")]]
    await query.edit_message_text(
        f"Saved *{saved_food['name']}*.\n\nHow many grams did you eat?",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )
    return ENTERING_WEIGHT


async def food_chosen(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    """User tapped a food button from the DB-match keyboard."""
    query = update.callback_query
    await query.answer()

    if query.data == "cancel":
        await query.edit_message_text("Cancelled.")
        return ConversationHandler.END

    food_id = int(query.data.split(":", 1)[1])
    food = context.user_data[KEY_FOODS].get(food_id)
    if food is None:
        await query.edit_message_text(
            "That food selection has expired. Try /log again."
        )
        return ConversationHandler.END

    context.user_data[KEY_FOOD] = food

    keyboard = [[InlineKeyboardButton("Cancel", callback_data="cancel")]]
    await query.edit_message_text(
        f"Selected *{food['name']}*\n"
        f"({food['kcal_100g']} kcal per 100g)\n\n"
        f"How many grams?",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )
    return ENTERING_WEIGHT


async def weight_entered(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    """User typed a number. Show macro breakdown + Confirm/Cancel."""
    text = update.message.text.strip().replace(",", ".")
    try:
        weight = float(text)
    except ValueError:
        await update.message.reply_text(
            "That's not a number. Try again, e.g. `150`."
        )
        return ENTERING_WEIGHT
    if weight <= 0:
        await update.message.reply_text("Weight must be positive. Try again.")
        return ENTERING_WEIGHT

    food = context.user_data[KEY_FOOD]
    context.user_data[KEY_WEIGHT] = weight

    factor = weight / 100
    kcal = float(food["kcal_100g"]) * factor
    protein = float(food["protein_100g"]) * factor
    fat = float(food["fat_100g"]) * factor
    carbs = float(food["carbs_100g"]) * factor

    keyboard = [[
        InlineKeyboardButton("Confirm", callback_data="confirm"),
        InlineKeyboardButton("Cancel", callback_data="cancel"),
    ]]

    await update.message.reply_text(
        f"*{weight:.0f}g {food['name']}*\n"
        f"  {kcal:.0f} kcal\n"
        f"  {protein:.1f}g protein\n"
        f"  {fat:.1f}g fat\n"
        f"  {carbs:.1f}g carbs",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )
    return CONFIRMING


async def confirm(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    """Final confirmation on meal entry."""
    query = update.callback_query
    await query.answer()

    if query.data == "cancel":
        await query.edit_message_text("Cancelled.")
        return ConversationHandler.END

    user_id = context.user_data.get(KEY_USER_ID)
    if user_id is None:
        profile = await client.get_user_by_telegram_id(update.effective_user.id)
        if profile is None:
            await query.edit_message_text(
                "You don't have a profile yet. Send /whoami first."
            )
            return ConversationHandler.END
        user_id = profile["id"]
        context.user_data[KEY_USER_ID] = user_id

    food = context.user_data[KEY_FOOD]
    weight = context.user_data[KEY_WEIGHT]

    try:
        await client.create_meal_entry(
            user_id=user_id,
            food_id=food["id"],
            weight_g=weight,
        )
    except Exception:
        log.exception("create_meal_entry failed")
        await query.edit_message_text("Couldn't save. Try /log again.")
        return ConversationHandler.END

    await query.edit_message_text(f"Logged {weight:.0f}g of {food['name']}.")
    return ConversationHandler.END


async def cancel_via_button(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    """Cancel button tap for states without other callback handlers."""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("Cancelled.")
    return ConversationHandler.END


async def cancel_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    """/cancel works at any state."""
    await update.message.reply_text("Cancelled.")
    return ConversationHandler.END


def build_log_handler() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CommandHandler("log", log_start)],
        states={
            CHOOSING_FOOD: [CallbackQueryHandler(food_chosen)],
            ENTERING_WEIGHT: [
                CallbackQueryHandler(cancel_via_button),
                MessageHandler(filters.TEXT & ~filters.COMMAND, weight_entered),
            ],
            CONFIRMING: [CallbackQueryHandler(confirm)],
            CONFIRMING_PARSED_FOOD: [CallbackQueryHandler(confirm_parsed_food)],
        },
        fallbacks=[CommandHandler("cancel", cancel_command)],
    )
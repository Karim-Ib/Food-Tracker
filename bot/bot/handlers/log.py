"""
/log command and photo barcode handler — multi-step conversation for logging.

Flows:

    /log <text>:
        search foods                       (CHOOSING_FOOD)
        on miss: typing -> LLM             (CONFIRMING_PARSED_FOOD)
        select food -> grams               (ENTERING_WEIGHT)
        macros -> confirm                  (CONFIRMING)

    [photo]:
        decode barcode -> OFF lookup
        on hit -> grams                    (ENTERING_WEIGHT, same as DB hit)
        on miss -> describe                (AWAITING_DESCRIPTION)
        description -> LLM                 (CONFIRMING_PARSED_FOOD)

All entry paths converge on ENTERING_WEIGHT once a food is identified.
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
from bot.barcode import decode_barcodes

log = logging.getLogger(__name__)

# Conversation states.
(
    CHOOSING_FOOD,
    ENTERING_WEIGHT,
    CONFIRMING,
    CONFIRMING_PARSED_FOOD,
    AWAITING_DESCRIPTION,
) = range(5)

# context.user_data keys.
KEY_FOODS = "log_foods"
KEY_FOOD = "log_food"
KEY_WEIGHT = "log_weight"
KEY_USER_ID = "user_id"
KEY_PARSED = "log_parsed"


# ---------- Entry: /log <text> ----------

async def log_start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
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


# ---------- Entry: photo -> barcode ----------

async def photo_entry(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    """User sent a photo. Decode any barcode and look it up."""
    # Telegram sends multiple photo sizes; take the highest resolution.
    photo = update.message.photo[-1]
    try:
        file = await photo.get_file()
        image_bytes = bytes(await file.download_as_bytearray())
    except Exception:
        log.exception("Failed to download photo")
        await update.message.reply_text(
            "Couldn't download that photo. Try again?"
        )
        return ConversationHandler.END

    barcodes = decode_barcodes(image_bytes)
    if not barcodes:
        await update.message.reply_text(
            "Couldn't find a barcode in that image. "
            "Try a clearer photo, or use `/log <description>` instead.",
            parse_mode="Markdown",
        )
        return ConversationHandler.END

    barcode = barcodes[0]

    await context.bot.send_chat_action(
        chat_id=update.effective_chat.id,
        action=ChatAction.TYPING,
    )

    try:
        food = await client.get_food_by_barcode(barcode)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            context.user_data["pending_barcode"] = barcode
            await update.message.reply_text(
                f"Found barcode `{barcode}`, but it's not in our database or "
                f"OpenFoodFacts.\n\n"
                f"Describe the product (e.g. 'Spar protein bar, 380kcal per 100g, "
                f"22g protein'):",
                parse_mode="Markdown",
            )
            return AWAITING_DESCRIPTION
        log.warning("get_food_by_barcode failed: %s", exc)
        await update.message.reply_text(
            "Couldn't look that up. Try again in a moment."
        )
        return ConversationHandler.END
    except Exception:
        log.exception("get_food_by_barcode unexpected error")
        await update.message.reply_text("Couldn't look that up. Try again later.")
        return ConversationHandler.END

    # OFF/DB hit — same shape as a food selected from the keyboard
    context.user_data[KEY_FOOD] = food

    keyboard = [[InlineKeyboardButton("Cancel", callback_data="cancel")]]
    title = f"*{food['name']}*"
    if food.get("brand"):
        title += f" — {food['brand']}"

    await update.message.reply_text(
        f"{title}\n"
        f"({food['kcal_100g']} kcal per 100g)\n\n"
        f"How many grams?",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )
    return ENTERING_WEIGHT


# ---------- AWAITING_DESCRIPTION: user types after barcode miss ----------

async def description_received(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    """User typed a description after we couldn't resolve their barcode."""
    description = update.message.text.strip()
    if not description:
        await update.message.reply_text("Need a description to look up. Try again.")
        return AWAITING_DESCRIPTION

    return await _parse_via_llm(update, context, description)


# ---------- LLM fallback (shared by text path and barcode-miss path) ----------

async def _parse_via_llm(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    description: str,
) -> int:
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

    # If we arrived here via a barcode miss, attach the barcode for the save step
    if barcode := context.user_data.pop("pending_barcode", None):
        parsed["barcode"] = barcode

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


# ---------- Existing handlers (unchanged from Phase 5) ----------

async def confirm_parsed_food(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
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
        await query.edit_message_text("Couldn't save the food. Try again.")
        return ConversationHandler.END

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
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("Cancelled.")
    return ConversationHandler.END


async def cancel_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    await update.message.reply_text("Cancelled.")
    return ConversationHandler.END


def build_log_handler() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[
            CommandHandler("log", log_start),
            MessageHandler(filters.PHOTO, photo_entry),
        ],
        states={
            CHOOSING_FOOD: [CallbackQueryHandler(food_chosen)],
            ENTERING_WEIGHT: [
                CallbackQueryHandler(cancel_via_button),
                MessageHandler(filters.TEXT & ~filters.COMMAND, weight_entered),
            ],
            CONFIRMING: [CallbackQueryHandler(confirm)],
            CONFIRMING_PARSED_FOOD: [CallbackQueryHandler(confirm_parsed_food)],
            AWAITING_DESCRIPTION: [
                CallbackQueryHandler(cancel_via_button),
                MessageHandler(filters.TEXT & ~filters.COMMAND, description_received),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel_command)],
    )
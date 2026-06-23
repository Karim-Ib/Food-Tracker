"""
/log command and photo barcode handler — multi-step conversation for logging.

Flows:

    /log <text>  (single item):
        search foods                       (CHOOSING_FOOD)
        on miss: typing -> LLM             (CONFIRMING_PARSED_FOOD)
        select food -> grams               (ENTERING_WEIGHT)
        macros -> confirm                  (CONFIRMING)

    /log a, b, c  (multi item):
        split on commas, parse inline weights, resolve each food
        (DB hit or LLM parse) concurrently
        walk through items missing a weight     (MULTI_WEIGHT_WALK)
        one combined confirmation               (MULTI_CONFIRM)
        confirm -> atomic batch write

    [photo]:
        decode barcode -> OFF lookup
        on hit -> grams                    (ENTERING_WEIGHT, same as DB hit)
        on miss -> describe                (AWAITING_DESCRIPTION)
        description -> LLM                 (CONFIRMING_PARSED_FOOD)

Single-item paths converge on ENTERING_WEIGHT once a food is identified.
The multi-item path is a parallel pipeline that ends in an atomic batch write.
"""
import asyncio
import logging
import re

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
    MULTI_WEIGHT_WALK,      # prompting for each missing weight
    MULTI_CONFIRM,          # the combined confirmation
) = range(7)

# context.user_data keys.
KEY_FOODS = "log_foods"
KEY_FOOD = "log_food"
KEY_WEIGHT = "log_weight"
KEY_USER_ID = "user_id"
KEY_PARSED = "log_parsed"
KEY_MULTI_ITEMS = "log_multi_items"   # list of resolved item dicts
KEY_MULTI_IDX = "log_multi_idx"       # index of item currently being weighed


# ======================================================================
# Entry: /log <text>
# ======================================================================

async def log_start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    raw = " ".join(context.args).strip()
    if not raw:
        await update.message.reply_text(
            "Usage:\n"
            "• `/log <food>` — log one item\n"
            "• `/log eggs, oats, banana` — log several at once "
            "(separate foods with commas)\n"
            "• add a weight inline to skip the question: "
            "`/log 120g eggs, 80g oats`",
            parse_mode="Markdown",
        )
        return ConversationHandler.END

    # A comma means "several foods" → multi-item path.
    if "," in raw:
        return await _multi_start(update, context, raw)

    return await _single_start(update, context, raw)


async def _single_start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    query: str,
) -> int:
    """Single-item path: DB fuzzy search, then keyboard or LLM fallback."""
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


# ======================================================================
# Multi-item path
# ======================================================================

# Leading inline weight: "120g", "80 g", "200grams". A bare number (no g
# suffix) is NOT treated as grams — "1 banana" is a count, which the LLM
# converts to grams. Only an explicit g/gram(s) suffix counts as inline.
_INLINE_WEIGHT = re.compile(r"^\s*(\d+(?:[.,]\d+)?)\s*g(?:ram)?s?\b\s*", re.I)


def _split_segment(segment: str) -> tuple[str, float | None]:
    """One comma-separated segment → (food_text, inline_weight_or_None)."""
    m = _INLINE_WEIGHT.match(segment)
    if m:
        weight = float(m.group(1).replace(",", "."))
        return segment[m.end():].strip(), weight
    return segment.strip(), None


async def _resolve_one(food_text: str) -> dict | None:
    """Resolve one segment to a food dict: DB hit, else LLM parse, else None.

    DB rows have an 'id'; LLM parses don't (they're saved later, only if the
    user confirms the whole batch). None means the segment was unrecognizable.
    """
    try:
        foods = await client.search_foods(query=food_text, limit=1)
    except Exception:
        foods = []
    if foods:
        return foods[0]
    try:
        return await client.parse_food(food_text)
    except Exception:
        return None


async def _multi_start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    raw: str,
) -> int:
    """Comma-separated /log. Resolve every food, then walk weights."""
    segments = [s for s in (seg.strip() for seg in raw.split(",")) if s]

    # A stray trailing comma ("/log eggs,") collapses to a single item.
    if len(segments) < 2:
        single = segments[0] if segments else ""
        if not single:
            await update.message.reply_text("Nothing to log.")
            return ConversationHandler.END
        return await _single_start(update, context, single)

    await context.bot.send_chat_action(
        chat_id=update.effective_chat.id, action=ChatAction.TYPING
    )

    parsed = [_split_segment(s) for s in segments]
    # Resolve every food concurrently — one round of latency, not N.
    resolved = await asyncio.gather(*(_resolve_one(text) for text, _ in parsed))

    items: list[dict] = []
    unresolved: list[str] = []
    for (text, weight), food in zip(parsed, resolved):
        if food is None:
            unresolved.append(text)
            continue
        items.append({
            "food": food,                   # DB row OR unsaved LLM parse
            "is_parsed": "id" not in food,  # LLM parses have no id yet
            "weight": weight,               # None if not given inline
            "label": food.get("name", text),
        })

    if not items:
        await update.message.reply_text(
            "I couldn't recognize any of those. Try logging them one at a time."
        )
        return ConversationHandler.END

    context.user_data[KEY_MULTI_ITEMS] = items

    summary = "Got these:\n" + "\n".join(f"• {it['label']}" for it in items)
    if unresolved:
        summary += "\n\nCouldn't recognize (skipped):\n" + "\n".join(
            f"• {u}" for u in unresolved
        )
    await update.message.reply_text(summary)

    return await _multi_advance_weight(update, context)


async def _multi_advance_weight(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    """Prompt for the next item missing a weight; if none remain, confirm."""
    items = context.user_data[KEY_MULTI_ITEMS]
    for idx, it in enumerate(items):
        if it["weight"] is None:
            context.user_data[KEY_MULTI_IDX] = idx
            msg = update.message or update.callback_query.message
            await msg.reply_text(f"How many grams of {it['label']}?")
            return MULTI_WEIGHT_WALK
    return await _multi_show_confirm(update, context)


async def multi_weight_received(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    """User typed a weight for the current item in the walk."""
    text = update.message.text.strip().replace(",", ".")
    try:
        weight = float(text)
    except ValueError:
        await update.message.reply_text("That's not a number. Try again, e.g. `120`.")
        return MULTI_WEIGHT_WALK
    if weight <= 0:
        await update.message.reply_text("Weight must be positive. Try again.")
        return MULTI_WEIGHT_WALK

    idx = context.user_data[KEY_MULTI_IDX]
    context.user_data[KEY_MULTI_ITEMS][idx]["weight"] = weight
    return await _multi_advance_weight(update, context)


def _scaled(food: dict, weight: float) -> tuple[float, float, float, float]:
    """Per-100g macros scaled to an actual weight. Returns (kcal, p, f, c)."""
    factor = weight / 100
    return (
        float(food["kcal_100g"]) * factor,
        float(food["protein_100g"]) * factor,
        float(food["fat_100g"]) * factor,
        float(food["carbs_100g"]) * factor,
    )


async def _multi_show_confirm(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    """Render the combined per-item + totals card with one Confirm button."""
    items = context.user_data[KEY_MULTI_ITEMS]

    lines = ["Logging all of this:", ""]
    total_k = total_p = total_f = total_c = 0.0
    for it in items:
        k, p, f, c = _scaled(it["food"], it["weight"])
        total_k += k
        total_p += p
        total_f += f
        total_c += c
        lines.append(f"• {it['weight']:.0f}g {it['label']} — {k:.0f} kcal")

    lines += [
        "",
        f"Total: {total_k:.0f} kcal · {total_p:.0f}g P · "
        f"{total_f:.0f}g F · {total_c:.0f}g C",
    ]

    keyboard = [[
        InlineKeyboardButton("Confirm all", callback_data="confirm"),
        InlineKeyboardButton("Cancel", callback_data="cancel"),
    ]]
    # Plain text: labels may contain '_' or '%' that would break Markdown.
    msg = update.message or update.callback_query.message
    await msg.reply_text(
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return MULTI_CONFIRM


async def multi_confirm(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    """Save any new foods, then write all meal entries atomically."""
    query = update.callback_query
    await query.answer()

    if query.data == "cancel":
        await query.edit_message_text("Cancelled.")
        return ConversationHandler.END

    user_id = await _resolve_user_id(update, context)
    if user_id is None:
        await query.edit_message_text("You don't have a profile yet. Send /whoami first.")
        return ConversationHandler.END

    items = context.user_data[KEY_MULTI_ITEMS]

    # LLM-parsed items aren't in the DB yet — persist them first to get ids.
    try:
        for it in items:
            if it["is_parsed"]:
                saved = await client.create_food_from_parse(it["food"], user_id)
                it["food"] = saved
                it["is_parsed"] = False
    except Exception:
        log.exception("saving parsed food in batch failed")
        await query.edit_message_text("Couldn't save one of the new foods. Try again.")
        return ConversationHandler.END

    entries = [
        {
            "user_id": user_id,
            "source_type": "food",
            "food_id": it["food"]["id"],
            "weight_g": it["weight"],
        }
        for it in items
    ]

    try:
        await client.create_meal_entries(entries)
    except Exception:
        log.exception("create_meal_entries batch failed")
        await query.edit_message_text("Couldn't log the meal. Try again.")
        return ConversationHandler.END

    await query.edit_message_text(f"Logged {len(entries)} items.")
    return ConversationHandler.END


# ======================================================================
# Entry: photo -> barcode
# ======================================================================

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
        await update.message.reply_text("Couldn't download that photo. Try again?")
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
        await update.message.reply_text("Couldn't look that up. Try again in a moment.")
        return ConversationHandler.END
    except Exception:
        log.exception("get_food_by_barcode unexpected error")
        await update.message.reply_text("Couldn't look that up. Try again later.")
        return ConversationHandler.END

    # OFF/DB hit — same shape as a food selected from the keyboard.
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


# ======================================================================
# AWAITING_DESCRIPTION: user types after a barcode miss
# ======================================================================

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


# ======================================================================
# LLM fallback (shared by single-item text path and barcode-miss path)
# ======================================================================

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

    # If we arrived via a barcode miss, attach the barcode for the save step.
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


# ======================================================================
# Single-item downstream handlers
# ======================================================================

async def confirm_parsed_food(
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
        await query.edit_message_text("That food selection has expired. Try /log again.")
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
        await update.message.reply_text("That's not a number. Try again, e.g. `150`.")
        return ENTERING_WEIGHT
    if weight <= 0:
        await update.message.reply_text("Weight must be positive. Try again.")
        return ENTERING_WEIGHT

    food = context.user_data[KEY_FOOD]
    context.user_data[KEY_WEIGHT] = weight

    kcal, protein, fat, carbs = _scaled(food, weight)

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

    user_id = await _resolve_user_id(update, context)
    if user_id is None:
        await query.edit_message_text("You don't have a profile yet. Send /whoami first.")
        return ConversationHandler.END

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


# ======================================================================
# Shared helpers
# ======================================================================

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


# ======================================================================
# Handler factory
# ======================================================================

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
            MULTI_WEIGHT_WALK: [
                CallbackQueryHandler(cancel_via_button),
                MessageHandler(filters.TEXT & ~filters.COMMAND, multi_weight_received),
            ],
            MULTI_CONFIRM: [CallbackQueryHandler(multi_confirm)],
        },
        fallbacks=[CommandHandler("cancel", cancel_command)],
    )
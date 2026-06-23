"""
/log command and photo barcode handler — multi-step conversation for logging.

Flows:

    /log <text>  (single item):
        if the text carries macros ("150kcal, 10g protein") -> treat as a
            description, skip the DB search, go straight to the LLM
        else fuzzy-search foods                 (CHOOSING_FOOD)
            keyboard also offers "describe it instead" as an escape
        on DB miss: typing -> LLM               (CONFIRMING_PARSED_FOOD)
        select food -> grams                    (ENTERING_WEIGHT)
        macros -> confirm                       (CONFIRMING)

    /log a, b, c  (multi item):
        split on commas, parse inline weights, resolve each food to a list of
        candidates (top DB matches, or an LLM parse, or unresolved)
        REVIEW: card showing each item's current pick + a change button
                                                (MULTI_REVIEW)
            change an item -> pick from alternatives or describe
                                                (MULTI_PICK_ITEM)
            describe an item -> LLM parse just that one
                                                (MULTI_DESCRIBE_ITEM)
        looks right -> walk items missing a weight
                                                (MULTI_WEIGHT_WALK)
        combined totals confirm -> atomic batch write
                                                (MULTI_CONFIRM)

    [photo]:
        decode barcode -> OFF lookup
        on hit -> grams                    (ENTERING_WEIGHT, same as DB hit)
        on miss -> describe                (AWAITING_DESCRIPTION)
        description -> LLM                 (CONFIRMING_PARSED_FOOD)

Single-item paths converge on ENTERING_WEIGHT once a food is identified.
The multi-item path is a parallel pipeline ending in an atomic batch write.
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
    MULTI_REVIEW,           # editable card: each item + a change button
    MULTI_PICK_ITEM,        # per-item alternatives + describe option
    MULTI_DESCRIBE_ITEM,    # typed override for one item -> LLM
    MULTI_WEIGHT_WALK,      # prompting for each missing weight
    MULTI_CONFIRM,          # combined totals confirmation
) = range(10)

# context.user_data keys.
KEY_FOODS = "log_foods"
KEY_FOOD = "log_food"
KEY_WEIGHT = "log_weight"
KEY_USER_ID = "user_id"
KEY_PARSED = "log_parsed"
KEY_SINGLE_QUERY = "log_single_query"   # original query, for the describe escape
KEY_MULTI_ITEMS = "log_multi_items"     # list of item dicts (see _multi_start)
KEY_MULTI_IDX = "log_multi_idx"         # item currently being weighed / edited


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
            "• `/log <food>, 150kcal per 100g, 10g protein` — describe macros "
            "to skip the database\n"
            "• `/log eggs, oats, banana` — log several at once "
            "(separate foods with commas)\n"
            "• add a weight inline to skip the question: "
            "`/log 120g eggs, 80g oats`",
            parse_mode="Markdown",
        )
        return ConversationHandler.END

    if "," in raw:
        return await _multi_start(update, context, raw)

    return await _single_start(update, context, raw)


# Signals that an input is a *description with macros*, not a name to search.
# Matches a number next to an energy/macro unit, or a bare macro keyword.
_HAS_MACROS = re.compile(
    r"\d\s*(kcal|cal|calorie|kj)\b"           # "150kcal", "150 cal"
    r"|\d\s*g\b\s*(protein|fat|carb|carbs)"   # "10g protein"
    r"|\b(protein|carbs?|kcal|calories)\b",   # bare macro words
    re.I,
)


def _looks_like_macro_description(text: str) -> bool:
    return bool(_HAS_MACROS.search(text))


async def _single_start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    query: str,
) -> int:
    """Single-item path: macro-aware routing, then DB search or LLM."""
    # Input carrying macros is a description, not a search query — skip the DB.
    if _looks_like_macro_description(query):
        return await _parse_via_llm(update, context, query)

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
    context.user_data[KEY_SINGLE_QUERY] = query

    keyboard = [
        [InlineKeyboardButton(f["name"], callback_data=f"food:{f['id']}")]
        for f in foods
    ]
    keyboard.append(
        [InlineKeyboardButton("✏️ None of these — describe it", callback_data="describe_single")]
    )
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


async def _resolve_candidates(food_text: str) -> dict:
    """Resolve one segment to a structured result.

    Returns a dict with:
      - "text":       the original segment text (for re-describe / labels)
      - "candidates": list of DB food dicts (top matches, may be empty)
      - "food":       the current pick (top candidate, or an LLM parse, or None)
      - "is_parsed":  True if "food" is an unsaved LLM parse

    A segment carrying macros skips the DB and goes straight to the LLM, same
    rule as the single-item path.
    """
    if _looks_like_macro_description(food_text):
        try:
            parsed = await client.parse_food(food_text)
        except Exception:
            parsed = None
        return {
            "text": food_text,
            "candidates": [],
            "food": parsed,
            "is_parsed": parsed is not None,
        }

    try:
        foods = await client.search_foods(query=food_text, limit=5)
    except Exception:
        foods = []

    if foods:
        return {
            "text": food_text,
            "candidates": foods,
            "food": foods[0],
            "is_parsed": False,
        }

    # No DB matches — try the LLM as the current pick, keep candidates empty.
    try:
        parsed = await client.parse_food(food_text)
    except Exception:
        parsed = None

    return {
        "text": food_text,
        "candidates": [],
        "food": parsed,
        "is_parsed": parsed is not None,
    }


async def _multi_start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    raw: str,
) -> int:
    """Comma-separated /log. Resolve every food, then show the review card."""
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
    resolved = await asyncio.gather(
        *(_resolve_candidates(text) for text, _ in parsed)
    )

    items: list[dict] = []
    unresolved: list[str] = []
    for (text, weight), res in zip(parsed, resolved):
        if res["food"] is None:
            unresolved.append(text)
            continue
        items.append({
            "text": res["text"],
            "candidates": res["candidates"],
            "food": res["food"],
            "is_parsed": res["is_parsed"],
            "weight": weight,
            "label": res["food"].get("name", text),
        })

    if not items:
        await update.message.reply_text(
            "I couldn't recognize any of those. Try logging them one at a time."
        )
        return ConversationHandler.END

    context.user_data[KEY_MULTI_ITEMS] = items

    if unresolved:
        await update.message.reply_text(
            "Couldn't recognize (skipped):\n"
            + "\n".join(f"• {u}" for u in unresolved)
        )

    return await _multi_show_review(update, context)


async def _multi_show_review(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    edit: bool = False,
) -> int:
    """Show the editable review card: each item + a 'change' button.

    edit=True edits the existing message in place (after returning from a
    picker); edit=False sends a fresh message (first entry to the card).
    """
    items = context.user_data[KEY_MULTI_ITEMS]

    lines = ["Here's what I'll log — tap any item to change it:", ""]
    for i, it in enumerate(items, start=1):
        src = " (AI estimate)" if it["is_parsed"] else ""
        lines.append(f"{i}. {it['label']}{src}")

    keyboard = [
        [InlineKeyboardButton(f"Change {i}. {it['label']}", callback_data=f"edit:{i-1}")]
        for i, it in enumerate(items, start=1)
    ]
    keyboard.append([
        InlineKeyboardButton("Looks right →", callback_data="review_ok"),
        InlineKeyboardButton("Cancel", callback_data="cancel"),
    ])

    text = "\n".join(lines)
    markup = InlineKeyboardMarkup(keyboard)

    if edit:
        await update.callback_query.edit_message_text(text, reply_markup=markup)
    else:
        await update.message.reply_text(text, reply_markup=markup)
    return MULTI_REVIEW


async def multi_review_action(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    """Tap on the review card: change an item, accept all, or cancel."""
    query = update.callback_query
    await query.answer()

    data = query.data
    if data == "cancel":
        await query.edit_message_text("Cancelled.")
        return ConversationHandler.END

    if data == "review_ok":
        await query.edit_message_text("Great — now the amounts.")
        return await _multi_advance_weight(update, context)

    if data.startswith("edit:"):
        idx = int(data.split(":", 1)[1])
        context.user_data[KEY_MULTI_IDX] = idx
        return await _multi_show_picker(update, context, idx)

    return MULTI_REVIEW


async def _multi_show_picker(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    idx: int,
) -> int:
    """Show alternatives for one item: its DB candidates + 'describe'."""
    it = context.user_data[KEY_MULTI_ITEMS][idx]
    candidates = it["candidates"]

    keyboard = [
        [InlineKeyboardButton(c["name"], callback_data=f"pick:{c['id']}")]
        for c in candidates
    ]
    keyboard.append([InlineKeyboardButton("✏️ Describe it instead", callback_data="describe")])
    keyboard.append([InlineKeyboardButton("← Back", callback_data="back")])

    if candidates:
        prompt = f"Other matches for '{it['text']}':"
    else:
        prompt = (
            f"No database matches for '{it['text']}'. "
            f"Describe it and I'll estimate the macros."
        )

    await update.callback_query.edit_message_text(
        prompt, reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return MULTI_PICK_ITEM


async def multi_pick_action(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    """Tap in the per-item picker: choose an alternative, describe, or back."""
    query = update.callback_query
    await query.answer()

    data = query.data
    idx = context.user_data[KEY_MULTI_IDX]
    items = context.user_data[KEY_MULTI_ITEMS]

    if data == "back":
        return await _multi_show_review(update, context, edit=True)

    if data == "describe":
        await query.edit_message_text(
            f"Describe '{items[idx]['text']}' "
            f"(e.g. 'Greek salad, 120kcal per 100g, 4g protein'):"
        )
        return MULTI_DESCRIBE_ITEM

    if data.startswith("pick:"):
        food_id = int(data.split(":", 1)[1])
        chosen = next((c for c in items[idx]["candidates"] if c["id"] == food_id), None)
        if chosen is not None:
            items[idx]["food"] = chosen
            items[idx]["is_parsed"] = False
            items[idx]["label"] = chosen["name"]
        return await _multi_show_review(update, context, edit=True)

    return MULTI_PICK_ITEM


async def multi_describe_received(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    """User typed a description to override one item; LLM-parse just that one."""
    description = update.message.text.strip()
    if not description:
        await update.message.reply_text("Need a description. Try again.")
        return MULTI_DESCRIBE_ITEM

    await context.bot.send_chat_action(
        chat_id=update.effective_chat.id, action=ChatAction.TYPING
    )

    try:
        parsed = await client.parse_food(description)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 422:
            await update.message.reply_text(
                "I couldn't recognize that. Try describing it differently."
            )
        else:
            await update.message.reply_text("Couldn't parse that. Try again.")
        return MULTI_DESCRIBE_ITEM
    except Exception:
        log.exception("multi describe parse failed")
        await update.message.reply_text("Couldn't parse that. Try again.")
        return MULTI_DESCRIBE_ITEM

    idx = context.user_data[KEY_MULTI_IDX]
    it = context.user_data[KEY_MULTI_ITEMS][idx]
    it["food"] = parsed
    it["is_parsed"] = True
    it["label"] = parsed.get("name", it["text"])

    # Back to the review card. We can't edit the user's text message, so send fresh.
    return await _multi_show_review(update, context)


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
# LLM fallback (shared by single-item text path, barcode-miss path, and
# the single-item "describe instead" escape button)
# ======================================================================

async def _parse_via_llm(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    description: str,
) -> int:
    """Parse free text into a food and show Save/Cancel.

    Works whether invoked from a text message (update.message) or a button
    tap (update.callback_query); it resolves the right message object once.
    """
    msg = update.message or update.callback_query.message

    await context.bot.send_chat_action(
        chat_id=update.effective_chat.id,
        action=ChatAction.TYPING,
    )

    try:
        parsed = await client.parse_food(description)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 422:
            await msg.reply_text(
                f"I couldn't recognize '{description}'. Try describing it differently."
            )
        else:
            log.warning("parse_food failed: %s", exc)
            await msg.reply_text(
                "Couldn't parse that right now. Try again in a moment."
            )
        return ConversationHandler.END
    except Exception:
        log.exception("parse_food unexpected error")
        await msg.reply_text("Couldn't parse that. Try again later.")
        return ConversationHandler.END

    if barcode := context.user_data.pop("pending_barcode", None):
        parsed["barcode"] = barcode

    context.user_data[KEY_PARSED] = parsed

    title = f"*{parsed['name']}*"
    if parsed.get("brand"):
        title += f" — {parsed['brand']}"

    body = (
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

    await msg.reply_text(
        body,
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

    if query.data == "describe_single":
        # User rejected all DB matches — re-run the original query through the
        # LLM as a description.
        original = context.user_data.get(KEY_SINGLE_QUERY, "")
        await query.edit_message_text("OK — describing it instead.")
        if not original:
            await query.message.reply_text(
                "Send the description, e.g. `/log <food>, 150kcal per 100g, 10g protein`.",
                parse_mode="Markdown",
            )
            return ConversationHandler.END
        return await _parse_via_llm(update, context, original)

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

def _scaled(food: dict, weight: float) -> tuple[float, float, float, float]:
    """Per-100g macros scaled to an actual weight. Returns (kcal, p, f, c)."""
    factor = weight / 100
    return (
        float(food["kcal_100g"]) * factor,
        float(food["protein_100g"]) * factor,
        float(food["fat_100g"]) * factor,
        float(food["carbs_100g"]) * factor,
    )


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
            MULTI_REVIEW: [CallbackQueryHandler(multi_review_action)],
            MULTI_PICK_ITEM: [CallbackQueryHandler(multi_pick_action)],
            MULTI_DESCRIBE_ITEM: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, multi_describe_received),
            ],
            MULTI_WEIGHT_WALK: [
                CallbackQueryHandler(cancel_via_button),
                MessageHandler(filters.TEXT & ~filters.COMMAND, multi_weight_received),
            ],
            MULTI_CONFIRM: [CallbackQueryHandler(multi_confirm)],
        },
        fallbacks=[CommandHandler("cancel", cancel_command)],
    )
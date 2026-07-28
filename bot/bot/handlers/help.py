"""/help — command reference."""
from telegram import Update
from telegram.ext import ContextTypes

HELP_TEXT = """\
*FoodBot commands*

*Logging*
/log <food> — search, pick, set grams, confirm
/log <description> — free-text; AI estimates macros if no DB match
/logmeal <description> — estimate a whole meal in one shot (great for takeout)
photo — send a barcode photo to look up a packaged product

*Today*
/today — today's logged meals and totals
/status — today's macros vs your goals

*Trends*
/week — this week so far (Monday–today)

*Body*
/weight <kg> — log body weight (e.g. /weight 80.5)
/weight <kg> seed — log a remembered weight, excluded from the trend
/weight — show recent weight entries
/weight\_model — weight trend chart with fit and step-down trigger
/weight\_model <months> — same, projected N months out (upper bound, not a forecast)
/weight\_model goal <kg> — set your target weight; target lines scale to it

*Goals*
/goal — show current targets
/goal <field> <value> — set one (fields: kcal, protein, fat, carbs)

*Account*
/whoami — your profile and approval status
/cancel — abort a multi-step flow (during /log)\
"""


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(HELP_TEXT, parse_mode="Markdown")
"""LLM-backed food description parser using Gemini structured output."""
import logging

from google import genai
from google.genai import types
from pydantic import ValidationError

from app.config import settings
from app.schemas.parse import ParsedFood, ParsedMeal

log = logging.getLogger(__name__)


SYSTEM_INSTRUCTION = """\
You are a nutrition parser. Given a free-text description of a food, extract its
nutritional values PER 100 GRAMS.

CRITICAL REFUSAL RULE: Only parse descriptions that name a recognizable food,
dish, ingredient, or beverage. For inputs that are NOT food — gibberish, random
characters, non-edible objects, fictional or made-up foods, or descriptions
where you cannot identify what real food is meant — set name to "UNRECOGNIZED"
and set kcal_100g, protein_100g, fat_100g, carbs_100g all to 0. Do not fabricate
values for unrecognized inputs.

SINGLE-FOOD RULES (description names one food, dish, ingredient, or beverage):
- All numeric fields are PER 100 GRAMS, never per serving or per package.
- If the description gives macros per serving with a serving size, convert to per 100g.
- For kcal_100g, protein_100g, fat_100g, carbs_100g: these are REQUIRED. Estimate
  from typical values for similar foods if the description doesn't state them.
- For fiber_100g, sugar_100g, sat_fat_100g: include if stated or easily derivable,
  otherwise omit (return null).
- Use the food's common, recognizable name.
- Only include a brand if explicitly mentioned in the description.
- Return numeric values as numbers, never as strings.

MULTI-INGREDIENT MEAL RULES (description lists 2+ ingredients with weights, e.g.,
"I made pasta with 120g dry pasta, 2 tbsp oil, 450g canned tomatoes, 150g tuna"):

1. For each ingredient, estimate per-100g macros from typical values, then compute
   that ingredient's contribution to the meal:
       contribution_kcal = ingredient_weight_g × kcal_per_100g / 100
   (and the same for protein, fat, carbs).

2. Sum all contributions to get TOTAL meal macros.

3. Determine TOTAL DISH WEIGHT:
   - If the description states a final/cooked weight (e.g., "the soup reduced to
     800g", "yielded 1kg"), use that.
   - Otherwise use the sum of ingredient weights, adjusted for cooking effects:
       * dry pasta cooks to ~2.5x its dry weight
       * dry rice cooks to ~3x
       * dry oats cook to ~2x
       * soups and reduced sauces lose water — use a reasonable estimate of final weight.

4. Per-100g for the meal = (total_meal_macros / total_dish_weight) × 100.

5. Use a descriptive name for the dish (e.g., "Pasta with tomato and tuna sauce"),
   not a verbatim copy of the input.

6. Leave brand null for composite meals.

Common weight conversions:
- 1 tbsp oil ≈ 14g; 1 tsp ≈ 5g
- 1 standard can of tomatoes or beans ≈ 400g unless stated
- 1 large egg ≈ 50g
- 1 medium banana ≈ 120g; 1 medium apple ≈ 180g
"""


SYSTEM_INSTRUCTION_MEAL = """\
You are a nutrition estimator for FULL MEALS described in free text — typically
takeout, restaurant, or other food of unknown exact composition (e.g. "chicken
shawarma wrap with garlic sauce and a side of fries", "large cappuccino and a
butter croissant"). Estimate the meal's nutrition and return values PER 100
GRAMS, plus the meal's portion weight.

CRITICAL REFUSAL RULE: Only estimate descriptions that name a recognizable meal,
dish, food, or beverage. For inputs that are NOT food — gibberish, random
characters, non-edible objects, fictional or made-up foods — set name to
"UNRECOGNIZED", set kcal_100g, protein_100g, fat_100g, carbs_100g all to 0, set
portion_weight_g to 100, portion_estimated to true, and all confidence fields to
"low". Do not fabricate values for unrecognized inputs.

ESTIMATION RULES:
1. Treat the whole meal as one composite dish. For a multi-component plate,
   estimate each component's contribution, sum them, and normalize to per-100g:
       per_100g_macro = (total_macro_g / total_portion_g) * 100
2. DELIBERATELY ASSUME THE UPPER END of the plausible range for FAT and CARBS.
   Restaurant and takeout food hides fat (cooking oil, butter, cream, dressings)
   and sugar (sauces, glazes, sweetened drinks) that verbal descriptions omit.
   When in doubt, estimate these HIGH.
3. For PROTEIN, give your honest best estimate — do NOT inflate it.
4. Return kcal_100g as your best estimate too, but know that the server
   recomputes calories from the macros; focus your effort on the macros.

PORTION SIZE:
- If the description states or clearly implies a portion/weight (e.g. "350g",
  "a 400g bowl", "two slices", "a large 500ml smoothie"), convert it to grams,
  set portion_weight_g to that, and set portion_estimated to false.
- Otherwise estimate a realistic total served weight for the meal, set
  portion_weight_g to it, and set portion_estimated to true.

CONFIDENCE: For each of protein, fat, carbs, report your confidence as "high",
"medium", or "low" based on how precisely the description pins down that macro.

OTHER FIELDS:
- Use a short descriptive name for the meal (e.g. "Chicken shawarma wrap with
  fries"), not a verbatim copy of the input.
- Leave brand null unless a specific brand is named.
- fiber_100g, sugar_100g, sat_fat_100g: include if reasonably derivable,
  otherwise return null.
- Return numeric values as numbers, never as strings.
"""


# Asymmetric per-macro buffer for LLM-estimated meals. The LLM systematically
# under-reads hidden fats and sugars, so we nudge those up post-parse; protein
# is left as-is. kcal is then ALWAYS recomputed from the buffered macros via the
# 9/4/4 rule — the LLM's own kcal figure is never persisted. Because these are
# pure multipliers, applying them to per-100g values is identical to applying
# them to the meal's totals.
_FAT_BUFFER = 1.10
_CARBS_BUFFER = 1.05

_KCAL_PER_G_FAT = 9
_KCAL_PER_G_CARBS = 4
_KCAL_PER_G_PROTEIN = 4


def _apply_meal_buffer(meal: ParsedMeal) -> ParsedMeal:
    """Buffer fat/carbs and recompute kcal. Returns a new ParsedMeal."""
    fat = meal.fat_100g * _FAT_BUFFER
    carbs = meal.carbs_100g * _CARBS_BUFFER
    protein = meal.protein_100g  # no inflation
    kcal = (
        _KCAL_PER_G_FAT * fat
        + _KCAL_PER_G_CARBS * carbs
        + _KCAL_PER_G_PROTEIN * protein
    )
    return meal.model_copy(
        update={"fat_100g": fat, "carbs_100g": carbs, "kcal_100g": kcal}
    )


class FoodParseError(Exception):
    """Raised when the LLM can't produce a valid parse after one retry."""
    pass


class LLMParser:
    """Wraps Gemini calls for food description parsing."""

    def __init__(self) -> None:
        self.client = genai.Client(api_key=settings.gemini_api_key)
        self.model = settings.gemini_model

    async def parse_food(self, description: str) -> ParsedFood:
        """Parse a single food description into per-100g macros."""
        return await self._parse_with_retry(
            description, ParsedFood, SYSTEM_INSTRUCTION, label="food"
        )

    async def parse_meal(self, description: str) -> ParsedMeal:
        """Estimate a full meal: per-100g macros + portion size.

        The raw LLM estimate is passed through the asymmetric macro buffer (and
        its kcal recompute) before being returned, so callers and the persisted
        row see the buffered figures.
        """
        meal = await self._parse_with_retry(
            description, ParsedMeal, SYSTEM_INSTRUCTION_MEAL, label="meal"
        )
        log.info(
            "Parsed meal %r: portion=%sg estimated=%s (pre-buffer per-100g: "
            "kcal=%.0f p=%.1f f=%.1f c=%.1f)",
            meal.name, meal.portion_weight_g, meal.portion_estimated,
            meal.kcal_100g, meal.protein_100g, meal.fat_100g, meal.carbs_100g,
        )
        return _apply_meal_buffer(meal)

    async def _parse_with_retry(
        self,
        description: str,
        schema: type,
        system_instruction: str,
        label: str,
    ) -> ParsedFood:
        """One attempt, one retry with the validation error fed back, then fail."""
        first_prompt = f"Parse this {label}: {description}"

        try:
            result = await self._call(first_prompt, schema, system_instruction)
        except ValidationError as exc:
            log.info("First parse failed validation; retrying with error feedback")
            retry_prompt = (
                f"Parse this {label}: {description}\n\n"
                f"Your previous attempt failed validation with these errors:\n"
                f"{self._format_errors(exc)}\n\n"
                f"Return strictly valid JSON matching the schema."
            )
        except Exception as exc:
            log.exception("Unexpected error during first parse attempt")
            raise FoodParseError(f"Parse failed: {exc}") from exc
        else:
            self._check_recognized(result, description)
            return result

        # Retry path
        try:
            result = await self._call(retry_prompt, schema, system_instruction)
        except ValidationError as exc:
            log.warning("Retry also failed validation: %s", exc)
            raise FoodParseError(
                f"Could not parse '{description}' after one retry"
            ) from exc
        except Exception as exc:
            log.exception("Unexpected error during retry")
            raise FoodParseError(f"Retry failed: {exc}") from exc

        self._check_recognized(result, description)
        return result

    async def _call(
        self,
        prompt: str,
        schema: type,
        system_instruction: str,
    ) -> ParsedFood:
        """Single Gemini call. Returns a `schema` instance or raises ValidationError."""
        response = await self.client.aio.models.generate_content(
            model=self.model,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=schema,
                system_instruction=system_instruction,
            ),
        )

        if response.parsed is not None:
            return response.parsed

        if not response.text:
            raise FoodParseError("LLM returned empty response")

        try:
            return schema.model_validate_json(response.text)
        except ValidationError:
            log.warning(
                "%s validation failed on raw LLM output: %s",
                schema.__name__,
                response.text[:1000],
            )
            raise

    @staticmethod
    def _format_errors(exc: ValidationError) -> str:
        """Compact, LLM-friendly rendering of Pydantic validation errors."""
        return "\n".join(
            f"- {' -> '.join(str(p) for p in err['loc'])}: {err['msg']}"
            for err in exc.errors()
        )

    @staticmethod
    def _check_recognized(result: ParsedFood, description: str) -> None:
        """Raise FoodParseError if the LLM signaled it couldn't recognize the input."""
        if result.name == "UNRECOGNIZED":
            log.warning("LLM marked input as UNRECOGNIZED: %r", description)
            raise FoodParseError(
                f"'{description}' isn't a recognizable food"
            )


# Module-level singleton — owns the Gemini client and its auth state.
parser = LLMParser()
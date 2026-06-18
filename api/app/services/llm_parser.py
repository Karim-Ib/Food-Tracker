"""LLM-backed food description parser using Gemini structured output."""
import logging

from google import genai
from google.genai import types
from pydantic import ValidationError

from app.config import settings
from app.schemas.parse import ParsedFood

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


class FoodParseError(Exception):
    """Raised when the LLM can't produce a valid parse after one retry."""
    pass


class LLMParser:
    """Wraps Gemini calls for food description parsing."""

    def __init__(self) -> None:
        self.client = genai.Client(api_key=settings.gemini_api_key)
        self.model = settings.gemini_model

    async def parse_food(self, description: str) -> ParsedFood:
        """Parse a food description. One attempt, one retry with feedback, then fail."""
        first_prompt = f"Parse this food: {description}"

        try:
            result = await self._call(first_prompt)
        except ValidationError as exc:
            log.info("First parse failed validation; retrying with error feedback")
            retry_prompt = (
                f"Parse this food: {description}\n\n"
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
            result = await self._call(retry_prompt)
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

    async def _call(self, prompt: str) -> ParsedFood:
        """Single Gemini call. Returns ParsedFood or raises ValidationError."""
        response = await self.client.aio.models.generate_content(
            model=self.model,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=ParsedFood,
                system_instruction=SYSTEM_INSTRUCTION,
            ),
        )

        if response.parsed is not None:
            return response.parsed

        if not response.text:
            raise FoodParseError("LLM returned empty response")

        return ParsedFood.model_validate_json(response.text)

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
            raise FoodParseError(
                f"'{description}' isn't a recognizable food"
            )


# Module-level singleton — owns the Gemini client and its auth state.
parser = LLMParser()
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

Rules (when the description IS a recognizable food):
- All numeric fields are PER 100 GRAMS, never per serving or per package.
- If the description gives macros per serving with a serving size, convert to per 100g.
- For kcal_100g, protein_100g, fat_100g, carbs_100g: these are REQUIRED. Estimate
  from typical values for similar foods if the description doesn't state them.
- For fiber_100g, sugar_100g, sat_fat_100g: include if stated or easily derivable,
  otherwise omit (return null).
- Use the food's common, recognizable name.
- Only include a brand if explicitly mentioned in the description.
- Return numeric values as numbers, never as strings.
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
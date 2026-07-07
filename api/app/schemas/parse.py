"""Schemas for the LLM food-parsing flow."""
from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator


class FoodParseRequest(BaseModel):
    """Input shape for POST /foods/parse and POST /foods/parse-meal.

    max_length is generous because a full-meal description (/parse-meal) can
    enumerate many components; single-food descriptions stay well under it.
    """
    description: str = Field(..., min_length=3, max_length=1000)


class ParsedFood(BaseModel):
    """The shape the LLM must satisfy — and the response shape of /foods/parse.

    Used in two roles:
      1. Passed as `response_schema` to Gemini's structured-output mode.
         The SDK generates JSON Schema from this and Gemini is constrained to
         emit JSON that matches.
      2. Returned to the caller (the bot) after server-side validation.

    Float (not Decimal) for macros: see notes below.
    """
    name: str = Field(..., min_length=1, max_length=200)
    brand: Optional[str] = Field(None, max_length=200)

    kcal_100g: float = Field(..., ge=0)
    protein_100g: float = Field(..., ge=0)
    fat_100g: float = Field(..., ge=0)
    carbs_100g: float = Field(..., ge=0)
    fiber_100g: Optional[float] = Field(None, ge=0)
    sugar_100g: Optional[float] = Field(None, ge=0)
    sat_fat_100g: Optional[float] = Field(None, ge=0)


Confidence = Literal["high", "medium", "low"]


class MacroConfidence(BaseModel):
    """The LLM's self-reported confidence for each macro of a meal estimate."""
    protein: Confidence
    fat: Confidence
    carbs: Confidence


class ParsedMeal(ParsedFood):
    """The shape the LLM must satisfy for the full-meal path — and the response
    shape of /foods/parse-meal.

    Extends ParsedFood (per-100g macros, so the row persists like any other
    food) with a portion size and per-macro confidence.

    Two derived guarantees are applied SERVER-SIDE before this leaves the API,
    never by the LLM (see services/llm_parser._apply_meal_buffer):
      1. fat_100g and carbs_100g are nudged up by a fixed buffer to absorb the
         LLM's systematic under-estimation of hidden fats/sugars.
      2. kcal_100g is always recomputed from the (buffered) macros via 9/4/4 —
         the LLM's own kcal figure is never persisted.
    """
    # ge=0 (not gt=0): gt emits JSON-Schema `exclusiveMinimum`, which the Gemini
    # SDK's Schema type rejects at request-build time. The ">0" rule is enforced
    # by the validator below instead, which the SDK's schema builder ignores.
    portion_weight_g: float = Field(..., ge=0)
    portion_estimated: bool = Field(
        ...,
        description=(
            "True if the LLM estimated the portion from the description, "
            "False if the description stated an explicit weight."
        ),
    )
    confidence: MacroConfidence

    @model_validator(mode="after")
    def _portion_must_be_positive(self) -> "ParsedMeal":
        if self.portion_weight_g <= 0:
            raise ValueError("portion_weight_g must be greater than 0")
        return self
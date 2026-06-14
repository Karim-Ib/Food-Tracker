"""Schemas for the LLM food-parsing flow."""
from typing import Optional

from pydantic import BaseModel, Field


class FoodParseRequest(BaseModel):
    """Input shape for POST /foods/parse."""
    description: str = Field(..., min_length=3, max_length=500)


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
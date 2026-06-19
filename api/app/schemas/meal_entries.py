from datetime import datetime, date
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.db.enums import EntrySource


class MealEntryBase(BaseModel):
    """Fields shared between create input and read output."""
    consumed_at: Optional[datetime] = None
    source_type: EntrySource
    food_id: Optional[int] = None
    recipe_id: Optional[int] = None
    weight_g: Decimal = Field(..., gt=0)
    notes: Optional[str] = Field(None, max_length=500)


class MealEntryCreate(MealEntryBase):
    """Input shape for POST /meal-entries."""
    user_id: int = Field(..., gt=0)

    @model_validator(mode="after")
    def check_source_consistency(self) -> "MealEntryCreate":
        """Enforce the same XOR rule the DB has as a CHECK constraint."""
        if self.source_type == EntrySource.FOOD:
            if self.food_id is None or self.recipe_id is not None:
                raise ValueError(
                    "source_type='food' requires food_id and forbids recipe_id"
                )
        else:  # EntrySource.RECIPE
            if self.recipe_id is None or self.food_id is not None:
                raise ValueError(
                    "source_type='recipe' requires recipe_id and forbids food_id"
                )
        return self


class MealEntryRead(MealEntryBase):
    """Output shape returned by every meal-entry endpoint."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    consumed_at: datetime
    created_at: datetime


class DailyTotals(BaseModel):
    """Aggregated macros for a single day."""
    kcal: Decimal
    protein: Decimal
    fat: Decimal
    carbs: Decimal


class MealEntryTodayItem(BaseModel):
    """One meal entry in the /today response, denormalized with food info."""
    id: int
    consumed_at: datetime
    weight_g: Decimal
    food_name: str
    kcal: Decimal
    protein: Decimal
    fat: Decimal
    carbs: Decimal


class TodayResponse(BaseModel):
    """GET /meal-entries/today response shape."""
    day: date
    entries: list[MealEntryTodayItem]
    totals: DailyTotals
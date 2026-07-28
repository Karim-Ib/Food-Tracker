from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class GoalUpdate(BaseModel):
    """Single-field target update. Exactly one field is set per request."""
    daily_kcal_target: Optional[Decimal] = Field(default=None, ge=0, le=20000)
    daily_protein_target_g: Optional[Decimal] = Field(default=None, ge=0, le=2000)
    daily_fat_target_g: Optional[Decimal] = Field(default=None, ge=0, le=2000)
    daily_carbs_target_g: Optional[Decimal] = Field(default=None, ge=0, le=2000)
    # Bounded like body_metrics.weight_kg so a fat-fingered goal is rejected at
    # the boundary rather than producing an absurd chart.
    goal_weight_kg: Optional[Decimal] = Field(default=None, gt=0, le=500)


class GoalRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    daily_kcal_target: Optional[Decimal] = None
    daily_protein_target_g: Optional[Decimal] = None
    daily_fat_target_g: Optional[Decimal] = None
    daily_carbs_target_g: Optional[Decimal] = None
    goal_weight_kg: Optional[Decimal] = None
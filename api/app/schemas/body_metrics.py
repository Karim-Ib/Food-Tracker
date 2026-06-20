from datetime import datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


class BodyMetricBase(BaseModel):
    recorded_at: Optional[datetime] = None
    weight_kg: Optional[Decimal] = Field(default=None, gt=0, le=500)
    body_fat_pct: Optional[Decimal] = Field(default=None, ge=0, le=70)
    notes: Optional[str] = None


class BodyMetricCreate(BodyMetricBase):
    user_id: int = Field(gt=0)

    @model_validator(mode="after")
    def at_least_one_measurement(self) -> "BodyMetricCreate":
        if self.weight_kg is None and self.body_fat_pct is None:
            raise ValueError("at least one of weight_kg or body_fat_pct is required")
        return self


class BodyMetricRead(BodyMetricBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    recorded_at: datetime
    created_at: datetime
from datetime import datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from app.db.enums import FoodSource, Visibility


class FoodBase(BaseModel):
    """Fields shared between FoodCreate (input) and FoodRead (output)."""
    name: str = Field(..., min_length=1, max_length=200)
    brand: Optional[str] = Field(None, max_length=200)
    barcode: Optional[str] = Field(None, max_length=50)

    kcal_100g: Decimal = Field(..., ge=0)
    protein_100g: Decimal = Field(..., ge=0)
    fat_100g: Decimal = Field(..., ge=0)
    carbs_100g: Decimal = Field(..., ge=0)
    fiber_100g: Optional[Decimal] = Field(None, ge=0)
    sugar_100g: Optional[Decimal] = Field(None, ge=0)
    sat_fat_100g: Optional[Decimal] = Field(None, ge=0)


class FoodCreate(FoodBase):
    """Input shape for creating a food."""
    source: FoodSource
    visibility: Visibility = Visibility.PRIVATE
    created_by_user_id: Optional[int] = None
    openfoodfacts_id: Optional[str] = None


class FoodRead(FoodBase):
    """Output shape returned by every foods endpoint."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    source: FoodSource
    visibility: Visibility
    created_by_user_id: Optional[int]
    openfoodfacts_id: Optional[str]
    created_at: datetime
    updated_at: datetime
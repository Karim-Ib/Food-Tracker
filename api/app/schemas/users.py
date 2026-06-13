from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class UserCreate(BaseModel):
    """Payload for POST /users."""
    telegram_id: int = Field(gt=0)
    display_name: str = Field(min_length=1, max_length=200)
    timezone: str = Field(default="Europe/Vienna", max_length=64)


class UserRead(BaseModel):
    """Response payload — what we return to clients."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    telegram_id: int
    display_name: str
    timezone: str
    is_active: bool
    is_admin: bool
    created_at: datetime
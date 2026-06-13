from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import MealEntry
from app.db.session import get_session
from app.schemas.meal_entries import MealEntryCreate, MealEntryRead
from app.services.meal_entries import MealEntryService


router = APIRouter(prefix="/meal-entries", tags=["meal-entries"])


@router.post("", response_model=MealEntryRead, status_code=201)
async def create_meal_entry(
    data: MealEntryCreate,
    session: AsyncSession = Depends(get_session),
) -> MealEntry:
    service = MealEntryService(session)
    return await service.create_entry(data)
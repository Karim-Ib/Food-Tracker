from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import MealEntry
from app.db.session import get_session
from app.schemas.meal_entries import MealEntryCreate, MealEntryRead, TodayResponse, MealEntryBatchCreate
from app.services.meal_entries import MealEntryService
from app.services.users import UserNotFound
from app.schemas.meal_entries import DailyStatus, WeekResponse


router = APIRouter(prefix="/meal-entries", tags=["meal-entries"])

@router.post("/batch", response_model=list[MealEntryRead], status_code=201)
async def create_meal_entries(
    data: MealEntryBatchCreate,
    session: AsyncSession = Depends(get_session),
) -> list[MealEntry]:
    service = MealEntryService(session)
    return await service.create_entries(data.entries)

@router.post("", response_model=MealEntryRead, status_code=201)
async def create_meal_entry(
    data: MealEntryCreate,
    session: AsyncSession = Depends(get_session),
) -> MealEntry:
    service = MealEntryService(session)
    return await service.create_entry(data)

@router.get(
    "/today",
    response_model=TodayResponse,
    responses={404: {"description": "User not found"}},
)
async def get_today(
    user_id: int = Query(..., gt=0),
    session: AsyncSession = Depends(get_session),
) -> TodayResponse:
    """Today's meal entries for the user, with computed daily totals."""
    service = MealEntryService(session)
    try:
        return await service.list_for_today(user_id)
    except UserNotFound:
        raise HTTPException(status_code=404, detail="User not found")

@router.get("/week", response_model=WeekResponse)
async def get_week(
    user_id: int = Query(gt=0),
    session: AsyncSession = Depends(get_session),
) -> WeekResponse:
    service = MealEntryService(session)
    try:
        return await service.list_for_week(user_id)
    except UserNotFound:
        raise HTTPException(status_code=404, detail="User not found")


@router.get("/status", response_model=DailyStatus)
async def get_status(
    user_id: int = Query(gt=0),
    session: AsyncSession = Depends(get_session),
) -> DailyStatus:
    service = MealEntryService(session)
    try:
        return await service.status_for_today(user_id)
    except UserNotFound:
        raise HTTPException(status_code=404, detail="User not found")
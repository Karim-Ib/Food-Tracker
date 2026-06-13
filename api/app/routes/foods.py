from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Food
from app.db.session import get_session
from app.schemas.foods import FoodCreate, FoodRead
from app.services.foods import (
    DuplicateBarcode,
    FoodNotFound,
    FoodService,
)


router = APIRouter(prefix="/foods", tags=["foods"])


@router.post(
    "",
    response_model=FoodRead,
    status_code=201,
    responses={409: {"description": "Barcode already exists"}},
)
async def create_food(
    data: FoodCreate,
    session: AsyncSession = Depends(get_session),
) -> Food:
    service = FoodService(session)
    try:
        return await service.create_food(data)
    except DuplicateBarcode as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.get("/search", response_model=list[FoodRead])
async def search_foods(
    q: str = Query(..., min_length=1, max_length=200),
    limit: int = Query(10, ge=1, le=50),
    session: AsyncSession = Depends(get_session),
) -> list[Food]:
    service = FoodService(session)
    return await service.search_foods(query=q, limit=limit)


@router.get(
    "/{food_id}",
    response_model=FoodRead,
    responses={404: {"description": "Food not found"}},
)
async def get_food(
    food_id: int,
    session: AsyncSession = Depends(get_session),
) -> Food:
    service = FoodService(session)
    try:
        return await service.get_food(food_id)
    except FoodNotFound:
        raise HTTPException(status_code=404, detail="Food not found")
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.parse import FoodParseRequest, ParsedFood
from app.services.llm_parser import FoodParseError, parser

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

@router.post(
    "/parse",
    response_model=ParsedFood,
    responses={422: {"description": "Could not parse food description"}},
)
async def parse_food(data: FoodParseRequest) -> ParsedFood:
    """Parse a free-text food description into structured nutritional data.

    Stateless — does not write to the foods table. The caller (bot) reviews
    the parsed output with the user and then POSTs to /foods to persist.
    """
    try:
        return await parser.parse_food(data.description)
    except FoodParseError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

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

@router.get(
    "/by-barcode/{barcode}",
    response_model=FoodRead,
    responses={404: {"description": "Barcode not found in DB or OpenFoodFacts"}},
)
async def get_food_by_barcode(
    barcode: str,
    session: AsyncSession = Depends(get_session),
) -> Food:
    """Look up a food by exact barcode. Uses tiered DB cache + OFF."""
    service = FoodService(session)
    try:
        return await service.lookup_by_barcode(barcode)
    except FoodNotFound:
        raise HTTPException(
            status_code=404,
            detail="Barcode not found",
        )
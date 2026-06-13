from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.schemas.users import UserCreate, UserRead
from app.services.users import UserAlreadyExists, UserService, UserNotFound

router = APIRouter(prefix="/users", tags=["users"])


@router.post(
    "",
    response_model=UserRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_user(
    data: UserCreate,
    session: AsyncSession = Depends(get_session),
) -> UserRead:
    service = UserService(session)
    try:
        user = await service.create_user(data)
    except UserAlreadyExists as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        )
    return UserRead.model_validate(user)


@router.get("/{user_id}", response_model=UserRead)
async def get_user(
    user_id: int,
    session: AsyncSession = Depends(get_session),
) -> UserRead:
    service = UserService(session)
    user = await service.get_user(user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User {user_id} not found",
        )
    return UserRead.model_validate(user)


@router.get(
    "/by-telegram-id/{telegram_id}",
    response_model=UserRead,
    responses={404: {"description": "User not found"}},
)
async def get_user_by_telegram_id(
    telegram_id: int,
    session: AsyncSession = Depends(get_session),
) -> User:
    service = UserService(session)
    try:
        return await service.get_by_telegram_id(telegram_id)
    except UserNotFound:
        raise HTTPException(status_code=404, detail="User not found")
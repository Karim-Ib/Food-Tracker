from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import User
from app.repositories.users import UserRepository
from app.schemas.users import UserCreate


class UserAlreadyExists(Exception):
    """Raised when trying to create a user whose telegram_id is already taken."""


class UserService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.users = UserRepository(session)

    async def create_user(self, data: UserCreate) -> User:
        existing = await self.users.get_by_telegram_id(data.telegram_id)
        if existing is not None:
            raise UserAlreadyExists(
                f"User with telegram_id {data.telegram_id} already exists"
            )

        user = await self.users.create(
            telegram_id=data.telegram_id,
            display_name=data.display_name,
            timezone=data.timezone,
        )
        await self.session.commit()
        return user

    async def get_user(self, user_id: int) -> User | None:
        return await self.users.get_by_id(user_id)
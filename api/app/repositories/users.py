from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import User


class UserRepository:
    """All SQL for the User entity lives here."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_id(self, user_id: int) -> User | None:
        result = await self.session.execute(
            select(User).where(User.id == user_id)
        )
        return result.scalar_one_or_none()

    async def get_by_telegram_id(self, telegram_id: int) -> User | None:
        result = await self.session.execute(
            select(User).where(User.telegram_id == telegram_id)
        )
        return result.scalar_one_or_none()

    async def create(
        self,
        *,
        telegram_id: int,
        display_name: str,
        timezone: str,
    ) -> User:
        user = User(
            telegram_id=telegram_id,
            display_name=display_name,
            timezone=timezone,
        )
        self.session.add(user)
        await self.session.flush()  # populates user.id without committing
        return user
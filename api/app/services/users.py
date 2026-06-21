from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import User
from app.repositories.users import UserRepository
from app.schemas.users import UserCreate
from app.schemas.goals import GoalRead, GoalUpdate


class UserAlreadyExists(Exception):
    """Raised when trying to create a user whose telegram_id is already taken."""

class UserNotFound(Exception):
    """Raised when a user lookup returns nothing."""
    pass

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

    async def get_by_telegram_id(self, telegram_id: int) -> User:
        """Fetch a user by their Telegram ID. Raises UserNotFound if absent."""
        user = await self.users.get_by_telegram_id(telegram_id)
        if user is None:
            raise UserNotFound(
                f"No user with telegram_id={telegram_id}"
            )
        return user

    async def update_goal(self, user_id: int, payload: GoalUpdate) -> GoalRead:
        user = await self.users.get_by_id(user_id)
        if user is None:
            raise UserNotFound(f"No user with id={user_id}")

        # Apply only the fields actually provided (exclude_unset drops the Nones
        # the bot didn't send, so a kcal-only update leaves protein/fat/carbs intact).
        for field, value in payload.model_dump(exclude_unset=True).items():
            setattr(user, field, value)

        await self.session.commit()
        return GoalRead.model_validate(user)
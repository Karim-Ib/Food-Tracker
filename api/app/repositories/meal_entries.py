from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import MealEntry


class MealEntryRepository:
    """Data access for the meal_entries table.

    Methods return ORM objects. No commits or rollbacks here.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_id(self, entry_id: int) -> Optional[MealEntry]:
        result = await self.session.execute(
            select(MealEntry).where(MealEntry.id == entry_id)
        )
        return result.scalar_one_or_none()

    async def create(self, **fields) -> MealEntry:
        entry = MealEntry(**fields)
        self.session.add(entry)
        await self.session.flush()
        return entry
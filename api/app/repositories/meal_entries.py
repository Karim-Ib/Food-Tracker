from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import MealEntry
from datetime import datetime, time, timedelta
from datetime import date as DateType  # rename to avoid conflict with sql.date
from zoneinfo import ZoneInfo

from sqlalchemy.orm import selectinload

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

    async def list_for_user_on_date(
        self,
        user_id: int,
        target_date: DateType,
        timezone_name: str,
    ) -> list[MealEntry]:
        """Entries for a user on a given LOCAL date in their timezone, food eager-loaded."""
        tz = ZoneInfo(timezone_name)
        local_start = datetime.combine(target_date, time.min, tzinfo=tz)
        local_end = local_start + timedelta(days=1)
        utc_start = local_start.astimezone(ZoneInfo("UTC"))
        utc_end = local_end.astimezone(ZoneInfo("UTC"))

        stmt = (
            select(MealEntry)
            .options(selectinload(MealEntry.food))
            .where(
                MealEntry.user_id == user_id,
                MealEntry.consumed_at >= utc_start,
                MealEntry.consumed_at < utc_end,
            )
            .order_by(MealEntry.consumed_at)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def list_for_range(
            self,
            user_id: int,
            start_utc: datetime,
            end_utc: datetime,
    ) -> list[MealEntry]:
        """All entries for a user within a UTC half-open interval [start, end)."""
        result = await self.session.execute(
            select(MealEntry)
            .where(
                MealEntry.user_id == user_id,
                MealEntry.consumed_at >= start_utc,
                MealEntry.consumed_at < end_utc,
            )
            .order_by(MealEntry.consumed_at)
            .options(selectinload(MealEntry.food))
        )
        return list(result.scalars().all())

    async def create_many(self, entries: list[dict]) -> list[MealEntry]:
        """Insert several entries in the current transaction. Caller commits."""
        objs = [MealEntry(**fields) for fields in entries]
        self.session.add_all(objs)
        await self.session.flush()
        return objs
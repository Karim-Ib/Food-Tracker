from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import BodyMetric

class BodyMetricRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, **fields) -> BodyMetric:
        entry = BodyMetric(**fields)
        self.session.add(entry)
        await self.session.flush()
        await self.session.refresh(entry)
        return entry

    async def list_recent(self, user_id: int, limit: int = 10) -> list[BodyMetric]:
        result = await self.session.execute(
            select(BodyMetric)
            .where(BodyMetric.user_id == user_id)
            .order_by(BodyMetric.recorded_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def list_all_weights(self, user_id: int) -> list[BodyMetric]:
        """Every weight-bearing row for a user, oldest first. No limit.

        Rows that only carry a body-fat reading are dropped here — the trend
        model fits kg, so a null weight has nothing to contribute. Seed rows
        ARE returned: the model needs them to draw the excluded marker, and it
        does its own filtering on `is_seed`.
        """
        result = await self.session.execute(
            select(BodyMetric)
            .where(
                BodyMetric.user_id == user_id,
                BodyMetric.weight_kg.is_not(None),
            )
            .order_by(BodyMetric.recorded_at)
        )
        return list(result.scalars().all())
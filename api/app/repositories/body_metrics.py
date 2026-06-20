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
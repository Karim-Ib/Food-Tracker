from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import BodyMetric
from app.repositories.body_metrics import BodyMetricRepository
from app.schemas.body_metrics import BodyMetricCreate


class BodyMetricService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.body_metrics = BodyMetricRepository(session)

    async def create(self, payload: BodyMetricCreate) -> BodyMetric:
        fields = payload.model_dump()
        if fields["recorded_at"] is None:
            fields["recorded_at"] = datetime.now(timezone.utc)
        entry = await self.body_metrics.create(**fields)
        await self.session.commit()
        return entry

    async def list_recent(self, user_id: int, limit: int = 10) -> list[BodyMetric]:
        return await self.body_metrics.list_recent(user_id, limit)
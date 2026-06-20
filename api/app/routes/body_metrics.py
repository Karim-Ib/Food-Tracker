from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import BodyMetric
from app.db.session import get_session
from app.schemas.body_metrics import BodyMetricCreate, BodyMetricRead
from app.services.body_metrics import BodyMetricService

router = APIRouter(prefix="/body-metrics", tags=["body_metrics"])


@router.post("", response_model=BodyMetricRead, status_code=201)
async def create_body_metric(
    payload: BodyMetricCreate,
    session: AsyncSession = Depends(get_session),
) -> BodyMetric:
    service = BodyMetricService(session)
    return await service.create(payload)


@router.get("/recent", response_model=list[BodyMetricRead])
async def get_recent(
    user_id: int = Query(gt=0),
    limit: int = Query(default=10, ge=1, le=50),
    session: AsyncSession = Depends(get_session),
) -> list[BodyMetric]:
    service = BodyMetricService(session)
    return await service.list_recent(user_id, limit)
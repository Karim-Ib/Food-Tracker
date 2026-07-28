from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import BodyMetric
from app.db.session import get_session
from app.schemas.body_metrics import BodyMetricCreate, BodyMetricRead
from app.schemas.weight_model import WeightModelSummary
from app.services.body_metrics import BodyMetricService
from app.services.users import UserNotFound
from app.services.weight_trend import (
    DEFAULT_HORIZON_DAYS,
    InsufficientWeightData,
    WeightTrendService,
)

router = APIRouter(prefix="/body-metrics", tags=["body_metrics"])

# Projection horizon bounds. 0 means "fit only, no dashed line"; the ceiling
# keeps a stray /weight_model 999 from rendering a decade of counterfactual.
_MIN_HORIZON_DAYS = 0
_MAX_HORIZON_DAYS = 1096  # ~3 years


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


@router.get(
    "/weight-model",
    response_model=WeightModelSummary,
    responses={
        404: {"description": "User not found"},
        422: {"description": "Not enough measured weigh-ins to fit a trend"},
    },
)
async def get_weight_model(
    user_id: int = Query(gt=0),
    horizon_days: int = Query(
        default=DEFAULT_HORIZON_DAYS,
        ge=_MIN_HORIZON_DAYS,
        le=_MAX_HORIZON_DAYS,
    ),
    session: AsyncSession = Depends(get_session),
) -> WeightModelSummary:
    """OLS weight trend: fit, step-down trigger, and target crossings.

    The crossing dates are a constant-rate counterfactual — an optimistic upper
    bound, never a forecast. Clients must render them as such; the response
    carries the disclaimer to ship alongside them.
    """
    service = WeightTrendService(session)
    try:
        return await service.summary(user_id, horizon_days=horizon_days)
    except UserNotFound:
        raise HTTPException(status_code=404, detail="User not found")
    except InsufficientWeightData as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.get(
    "/weight-model/chart.png",
    response_class=Response,
    responses={
        200: {"content": {"image/png": {}}, "description": "Trend chart"},
        404: {"description": "User not found"},
        422: {"description": "Not enough measured weigh-ins to fit a trend"},
    },
)
async def get_weight_model_chart(
    user_id: int = Query(gt=0),
    horizon_days: int = Query(
        default=DEFAULT_HORIZON_DAYS,
        ge=_MIN_HORIZON_DAYS,
        le=_MAX_HORIZON_DAYS,
    ),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """The trend chart as a PNG, rendered server-side.

    Rendered here rather than in the bot so the bot stays a thin client — and so
    a future dashboard gets the same image from the same place.
    """
    service = WeightTrendService(session)
    try:
        png = await service.chart_png(user_id, horizon_days=horizon_days)
    except UserNotFound:
        raise HTTPException(status_code=404, detail="User not found")
    except InsufficientWeightData as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    return Response(content=png, media_type="image/png")
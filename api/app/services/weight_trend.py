"""Weight-trend service: the DB adapter around the reference model.

This is the entire integration surface. `weight_model.py` and `weight_plot.py`
are framework-free and stay that way — everything that knows about SQLAlchemy,
users, or timezones lives here.
"""
import asyncio
import logging
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import BodyMetric, User
from app.repositories.body_metrics import BodyMetricRepository
from app.schemas.weight_model import (
    TargetCrossing,
    TrendFit,
    TriggerState,
    WeightModelSummary,
)
from app.services.users import UserNotFound
from app.services.weight_model import (
    WeighIn,
    fit_trend,
    projection,
    trigger_state,
)
from app.services.weight_plot import (
    _DEFAULT_TARGETS,
    figure_to_png_bytes,
    render_trend_figure,
)

log = logging.getLogger(__name__)

# Default projection horizon — the reference figure's own default.
DEFAULT_HORIZON_DAYS = 152

# The trailing window the step-down trigger reads. Kept here (not inlined at the
# call site) so the number the API reports is the number the model used.
TRIGGER_WINDOW_DAYS = 14

# matplotlib's pyplot carries global figure state and is not thread-safe, so
# renders are serialized. They still run off the event loop — a ~1s blocking
# render on the loop would stall every other request, which is the whole reason
# this API is async.
_render_lock = asyncio.Lock()


class InsufficientWeightData(Exception):
    """Raised when there aren't enough measured weigh-ins to fit a trend."""


def _safe_projection(fit, targets: list[float], horizon_days: float) -> dict:
    """projection(), one target at a time, dropping ones that can't be dated.

    A near-flat trend divides by a slope close to zero, so the crossing lands
    millions of days out — or at infinity if the slope is exactly zero — and
    building that date raises OverflowError. That is a real state (weight held
    steady for a fortnight), not a bug, and the honest answer is "this line
    never gets there", so the target is simply omitted. The chart already
    behaves this way: its `0 <= d <= end_day` check skips the marker.
    """
    out: dict = {}
    for kg in targets:
        try:
            out.update(projection(fit, [kg], horizon_days=horizon_days))
        except (OverflowError, ZeroDivisionError, ValueError):
            log.debug("no representable crossing date for %s kg — omitted", kg)
    return out


class WeightTrendService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.body_metrics = BodyMetricRepository(session)

    async def _load(self, user_id: int) -> tuple[list[WeighIn], User]:
        """Fetch the user's weigh-ins as WeighIn objects. The whole adapter.

        Timestamps are converted to the user's local wall-clock and stripped of
        tzinfo. Fitting on naive local time is what makes the day axis read
        correctly: a 00:30 Vienna weigh-in is the previous day in UTC, and both
        the day-index arithmetic and the chart's date labels would file it under
        the wrong date. Same local-date reasoning as /today and /week.
        """
        user = await self.session.get(User, user_id)
        if user is None:
            raise UserNotFound(f"No user with id={user_id}")

        tz = ZoneInfo(user.timezone)
        rows: list[BodyMetric] = await self.body_metrics.list_all_weights(user_id)

        data = [
            WeighIn(
                ts=row.recorded_at.astimezone(tz).replace(tzinfo=None),
                kg=float(row.weight_kg),
                is_seed=row.is_seed,
            )
            for row in rows
        ]

        measured = sum(1 for w in data if not w.is_seed)
        if measured < 3:
            raise InsufficientWeightData(
                f"need at least 3 measured weigh-ins to fit a trend, have {measured}"
            )
        return data, user

    async def summary(
        self,
        user_id: int,
        horizon_days: int = DEFAULT_HORIZON_DAYS,
    ) -> WeightModelSummary:
        data, _ = await self._load(user_id)

        try:
            fit = fit_trend(data)
        except ValueError as exc:  # <3 points after duplicate collapse
            raise InsufficientWeightData(str(exc)) from exc

        trigger = trigger_state(data, window_days=TRIGGER_WINDOW_DAYS)

        measured = [w for w in data if not w.is_seed]
        last_day = (measured[-1].ts - fit.t0).total_seconds() / 86400.0

        targets = [kg for kg, _label in _DEFAULT_TARGETS]
        # projection() counts its horizon from t0, but the chart draws the dashed
        # line to last_day + horizon_days. Passing the chart's endpoint keeps
        # within_horizon meaning "a marker exists for this on the image" — without
        # it the caption silently drops crossings the user can plainly see.
        crossings = _safe_projection(fit, targets, last_day + horizon_days)

        ci_low, ci_high = fit.ci_per_week()

        return WeightModelSummary(
            fit=TrendFit(
                slope_per_week=fit.slope_per_week,
                se_per_week=fit.se_per_week,
                ci_low_per_week=ci_low,
                ci_high_per_week=ci_high,
                r2=fit.r2,
                resid_sd=fit.resid_sd,
                n=fit.n,
                first_measured_at=fit.t0,
                last_measured_at=measured[-1].ts,
            ),
            trigger=TriggerState(
                fired=trigger["fired"],
                rolling_slope=trigger["rolling_slope"],
                threshold=trigger.get("threshold"),
                window_days=TRIGGER_WINDOW_DAYS,
                reason=trigger["reason"],
            ),
            projection=[
                TargetCrossing(
                    target_kg=float(kg),
                    crossing_date=info["date"].date(),
                    days_from_start=info["days_from_t0"],
                    within_horizon=info["within_horizon"],
                    already_passed=info["days_from_t0"] <= last_day,
                )
                for kg, info in crossings.items()
            ],
            horizon_days=horizon_days,
            seed_count=sum(1 for w in data if w.is_seed),
        )

    async def chart_png(
        self,
        user_id: int,
        horizon_days: int = DEFAULT_HORIZON_DAYS,
    ) -> bytes:
        data, _ = await self._load(user_id)

        def _render() -> bytes:
            fig, _fit = render_trend_figure(data, horizon_days=horizon_days)
            return figure_to_png_bytes(fig)

        async with _render_lock:
            try:
                return await asyncio.to_thread(_render)
            except ValueError as exc:
                raise InsufficientWeightData(str(exc)) from exc


def months_to_days(months: float) -> int:
    """Calendar months → days, using the mean Gregorian month (30.44 days)."""
    return round(months * 30.44)

"""Response schemas for the weight-trend model.

The framing in these docstrings is not decoration — it is the contract. The
projection is a constant-rate counterfactual, so every field derived from it
travels with a label saying so, and any client rendering them must repeat it.
"""
from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, Field


class TrendFit(BaseModel):
    """OLS of kg on day-index, over measured (non-seed) points only."""
    slope_per_week: float
    se_per_week: float
    ci_low_per_week: float = Field(
        ..., description="95% CI on the SLOPE — parameter uncertainty, not a "
                         "prediction interval."
    )
    ci_high_per_week: float
    r2: float
    resid_sd: float = Field(..., description="±1σ of observed points about the fit, kg.")
    n: int = Field(..., description="Measured points in the fit; seeds excluded.")
    first_measured_at: datetime
    last_measured_at: datetime


class TriggerState(BaseModel):
    """Rate-based step-down trigger over the trailing window."""
    fired: bool
    rolling_slope: Optional[float] = Field(
        None, description="kg/week over the trailing window; null if <3 points in it."
    )
    threshold: Optional[float] = None
    window_days: int
    reason: str


class TargetCrossing(BaseModel):
    """When the constant-rate line reaches a target weight.

    UPPER BOUND, NOT A FORECAST. Real loss decelerates (adaptive thermogenesis
    plus falling maintenance as mass drops), so the true date lands LATER than
    this one. Never render as "you will reach X by Y".
    """
    target_kg: float
    crossing_date: date
    days_from_start: float
    within_horizon: bool
    already_passed: bool = Field(
        ..., description="True if the fit crossed this weight before the last weigh-in."
    )
    is_goal: bool = Field(
        default=False,
        description="True for the user's goal weight; the rest are waypoints to it.",
    )


class WeightModelSummary(BaseModel):
    """GET /body-metrics/weight-model response."""
    fit: TrendFit
    trigger: TriggerState
    projection: list[TargetCrossing]
    horizon_days: int
    seed_count: int = Field(..., description="Flagged rows excluded from the fit.")
    goal_weight_kg: Optional[float] = Field(
        None,
        description=(
            "The user's target weight. Null means no goal is set, in which case "
            "the chart carries no target lines and `projection` is empty."
        ),
    )
    projection_disclaimer: str = Field(
        default=(
            "Constant-rate counterfactual, not a forecast. Real loss "
            "decelerates, so true dates land later than these."
        ),
        description="Ship this verbatim next to any projected date.",
    )

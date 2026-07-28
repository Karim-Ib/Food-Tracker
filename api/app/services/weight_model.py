"""
Weight-trend model — reference implementation.

Encodes the decisions made when building this by hand:
  - seed / non-measured rows are excluded from fits (flagged, not date-based)
  - exact-duplicate timestamps collapsed
  - OLS slope reported with standard error and an honest slope CI
  - linear projection is labelled a COUNTERFACTUAL upper bound, not a forecast
  - a 14-day rolling slope drives a rate-based trigger

Dependency-light (numpy only) so it ports into a backend easily.
"""

from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timedelta
import numpy as np


@dataclass
class WeighIn:
    ts: datetime          # measurement timestamp (or logging time; see note)
    kg: float
    is_seed: bool = False # True => excluded from all fits


# ---------------------------------------------------------------------------
# NOTE on timestamps: in this app the stored timestamp is *logging* time, not
# measurement time. Measurements are fasted-morning; the user logs when
# convenient. For a daily-resolution trend this is fine (the date is right).
# If you later store a separate measurement date, fit on that instead.
# ---------------------------------------------------------------------------


def _clean(data: list[WeighIn]) -> list[WeighIn]:
    """Drop seeds and collapse exact-duplicate timestamps (keep last)."""
    seen: dict[datetime, WeighIn] = {}
    for w in sorted(data, key=lambda x: x.ts):
        if w.is_seed:
            continue
        seen[w.ts] = w          # later duplicate overwrites earlier
    return list(seen.values())


def _to_days(data: list[WeighIn]) -> tuple[np.ndarray, np.ndarray, datetime]:
    t0 = data[0].ts
    x = np.array([(w.ts - t0).total_seconds() / 86400.0 for w in data])
    y = np.array([w.kg for w in data])
    return x, y, t0


@dataclass
class Fit:
    slope_per_week: float
    intercept_kg: float          # at t0
    se_per_week: float
    r2: float
    resid_sd: float
    n: int
    t0: datetime

    def ci_per_week(self, z: float = 1.96) -> tuple[float, float]:
        return (self.slope_per_week - z * self.se_per_week,
                self.slope_per_week + z * self.se_per_week)

    def predict_kg(self, day: float) -> float:
        return self.intercept_kg + (self.slope_per_week / 7.0) * day

    def day_for_weight(self, kg: float) -> float:
        """Day index (from t0) at which the fit crosses `kg`."""
        return (kg - self.intercept_kg) / (self.slope_per_week / 7.0)


def fit_trend(data: list[WeighIn]) -> Fit:
    """OLS of kg on day-index. Slope returned per-week for readability."""
    pts = _clean(data)
    if len(pts) < 3:
        raise ValueError("need >=3 measured points to fit")
    x, y, t0 = _to_days(pts)
    n = len(x)

    xbar, ybar = x.mean(), y.mean()
    Sxx = np.sum((x - xbar) ** 2)
    Sxy = np.sum((x - xbar) * (y - ybar))
    slope_day = Sxy / Sxx
    intercept = ybar - slope_day * xbar

    resid = y - (intercept + slope_day * x)
    dof = n - 2
    resid_sd = float(np.sqrt(np.sum(resid ** 2) / dof))
    se_day = resid_sd / np.sqrt(Sxx)

    ss_tot = np.sum((y - ybar) ** 2)
    r2 = 1.0 - np.sum(resid ** 2) / ss_tot if ss_tot > 0 else float("nan")

    return Fit(
        slope_per_week=slope_day * 7.0,
        intercept_kg=intercept,
        se_per_week=se_day * 7.0,
        r2=float(r2),
        resid_sd=resid_sd,
        n=n,
        t0=t0,
    )


def rolling_slope(data: list[WeighIn], window_days: int = 14) -> float | None:
    """
    Slope (kg/week) over the trailing `window_days`, ending at the last point.
    Returns None if the window holds < 3 measured points (uninformative).
    A single low/high reading barely moves it; that is the point.
    """
    pts = _clean(data)
    if not pts:
        return None
    end = pts[-1].ts
    lo = end - timedelta(days=window_days)
    win = [w for w in pts if w.ts >= lo]
    if len(win) < 3:
        return None
    x, y, _ = _to_days(win)
    xbar, ybar = x.mean(), y.mean()
    Sxx = np.sum((x - xbar) ** 2)
    if Sxx == 0:
        return None
    return float(np.sum((x - xbar) * (y - ybar)) / Sxx * 7.0)


def trigger_state(
    data: list[WeighIn],
    threshold_per_week: float = 0.30,
    window_days: int = 14,
) -> dict:
    """
    Rate-based step-down trigger.

    Fires when the trailing-window slope is SHALLOWER than -threshold, i.e.
    weight loss has slowed below `threshold` kg/wk, signalling the current
    intake has stopped working and a calorie step-down is due.
    """
    rs = rolling_slope(data, window_days)
    if rs is None:
        return {"fired": False, "rolling_slope": None,
                "reason": "insufficient data in window"}
    fired = rs > -abs(threshold_per_week)
    return {
        "fired": fired,
        "rolling_slope": rs,
        "threshold": -abs(threshold_per_week),
        "reason": ("loss slowed past threshold — step-down due"
                   if fired else "loss rate still above threshold — hold"),
    }


def projection(fit: Fit, targets: list[float], horizon_days: int = 150) -> dict:
    """
    Linear extrapolation of the fit to `targets`.

    CRITICAL FRAMING for the UI: this is a CONSTANT-RATE COUNTERFACTUAL, an
    optimistic upper bound. Real loss decelerates (adaptive thermogenesis +
    falling maintenance as mass drops), so true dates land LATER than these.
    Label it as such; do not present it as a forecast.
    """
    out = {}
    for kg in targets:
        d = fit.day_for_weight(kg)
        date = fit.t0 + timedelta(days=d)
        out[kg] = {
            "date": date,
            "days_from_t0": d,
            "within_horizon": d <= horizon_days,
        }
    return out


if __name__ == "__main__":
    rows = [
        ("2026-06-01 10:24", 109.00, True),   # seed: remembered, not measured
        ("2026-06-21 10:30", 107.40, False), ("2026-06-21 22:23", 107.80, False),
        ("2026-06-22 07:55", 108.20, False), ("2026-06-23 11:58", 108.20, False),
        ("2026-06-25 10:12", 107.70, False), ("2026-07-03 13:42", 107.10, False),
        ("2026-07-05 08:24", 107.30, False), ("2026-07-06 10:04", 106.40, False),
        ("2026-07-07 09:54", 106.50, False), ("2026-07-08 20:54", 106.70, False),
        ("2026-07-12 12:08", 106.30, False), ("2026-07-13 11:58", 104.90, False),
        ("2026-07-14 06:46", 105.40, False), ("2026-07-15 06:46", 105.40, False),
        ("2026-07-16 07:15", 105.50, False), ("2026-07-17 10:09", 105.50, False),
        ("2026-07-18 11:45", 104.90, False), ("2026-07-19 16:58", 105.50, False),
        ("2026-07-20 09:13", 105.30, False), ("2026-07-21 09:50", 104.40, False),
        ("2026-07-23 07:13", 104.70, False), ("2026-07-24 07:02", 103.80, False),
        ("2026-07-25 16:27", 103.30, False),
    ]
    data = [WeighIn(datetime.strptime(t, "%Y-%m-%d %H:%M"), kg, s)
            for t, kg, s in rows]

    f = fit_trend(data)
    print(f"n={f.n}  slope={f.slope_per_week:.3f} kg/wk  "
          f"SE={f.se_per_week:.3f}  r2={f.r2:.3f}  sd={f.resid_sd:.3f}")
    lo, hi = f.ci_per_week()
    print(f"95% CI: {lo:.3f} to {hi:.3f} kg/wk")
    print("rolling 14d slope:", rolling_slope(data))
    print("trigger:", trigger_state(data))
    for kg, info in projection(f, [100, 95, 90, 86]).items():
        print(f"  {kg}kg -> {info['date'].date()} (day {info['days_from_t0']:.0f})")

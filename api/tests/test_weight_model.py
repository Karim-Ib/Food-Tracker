"""Reference check for the weight-trend model.

These numbers are ground truth, verified by hand before the model was ported in.
If a change to weight_model.py moves any of them, the change is wrong — not the
test. The seed-exclusion case is here because it is the failure mode that
silently produces a plausible-looking wrong answer (-0.72 instead of -0.82).
"""
from datetime import date, datetime

import pytest
from app.services.weight_model import (
    WeighIn,
    _clean,
    fit_trend,
    projection,
    rolling_slope,
    trigger_state,
)

# The sample series from weight_model.__main__ — one seed plus 23 measured points.
SAMPLE_ROWS = [
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


@pytest.fixture
def sample_data() -> list[WeighIn]:
    return [
        WeighIn(datetime.strptime(ts, "%Y-%m-%d %H:%M"), kg, seed)
        for ts, kg, seed in SAMPLE_ROWS
    ]


def test_fit_matches_reference(sample_data):
    fit = fit_trend(sample_data)

    assert fit.n == 23
    assert fit.slope_per_week == pytest.approx(-0.822, abs=0.001)
    assert fit.se_per_week == pytest.approx(0.063, abs=0.001)
    assert fit.r2 == pytest.approx(0.890, abs=0.001)


def test_crossing_dates_match_reference(sample_data):
    fit = fit_trend(sample_data)
    crossings = projection(fit, [100, 95, 90, 86])

    assert crossings[100]["date"].date() == date(2026, 8, 30)
    assert crossings[95]["date"].date() == date(2026, 10, 12)
    assert crossings[90]["date"].date() == date(2026, 11, 23)
    assert crossings[86]["date"].date() == date(2026, 12, 27)


def test_seed_row_is_excluded_from_the_fit(sample_data):
    """The seed must be dropped by its flag alone. Including it gives -0.72."""
    unflagged = [WeighIn(w.ts, w.kg, is_seed=False) for w in sample_data]

    assert fit_trend(unflagged).n == 24
    assert fit_trend(unflagged).slope_per_week == pytest.approx(-0.72, abs=0.01)
    assert fit_trend(sample_data).slope_per_week == pytest.approx(-0.822, abs=0.001)


def test_duplicate_timestamps_collapse_keeping_last():
    ts = [datetime(2026, 7, 1), datetime(2026, 7, 2), datetime(2026, 7, 3)]
    data = [
        WeighIn(ts[0], 100.0),
        WeighIn(ts[1], 99.0),
        WeighIn(ts[1], 98.0),   # same timestamp — this one wins
        WeighIn(ts[2], 97.0),
    ]

    assert [w.kg for w in _clean(data)] == [100.0, 98.0, 97.0]
    assert fit_trend(data).n == 3


def test_fit_needs_three_measured_points():
    data = [
        WeighIn(datetime(2026, 7, 1), 100.0, is_seed=True),
        WeighIn(datetime(2026, 7, 2), 99.0),
        WeighIn(datetime(2026, 7, 3), 98.0),
    ]
    with pytest.raises(ValueError, match="need >=3 measured points"):
        fit_trend(data)


def test_trigger_holds_while_loss_rate_is_steep(sample_data):
    state = trigger_state(sample_data)

    assert state["rolling_slope"] == pytest.approx(rolling_slope(sample_data))
    assert state["fired"] is False          # -0.30 threshold, trailing slope steeper
    assert state["threshold"] == -0.30


def test_trigger_fires_when_loss_flattens():
    """A flat trailing fortnight is exactly the step-down signal."""
    data = [
        WeighIn(datetime(2026, 7, day), 100.0 - 0.01 * day)
        for day in range(1, 15)
    ]
    state = trigger_state(data)

    assert state["fired"] is True
    assert "step-down due" in state["reason"]


def test_rolling_slope_needs_three_points_in_window():
    data = [
        WeighIn(datetime(2026, 6, 1), 105.0),
        WeighIn(datetime(2026, 6, 2), 104.5),
        WeighIn(datetime(2026, 7, 25), 103.0),   # alone in the trailing 14 days
    ]
    assert rolling_slope(data, window_days=14) is None
    assert trigger_state(data)["fired"] is False
    assert trigger_state(data)["reason"] == "insufficient data in window"

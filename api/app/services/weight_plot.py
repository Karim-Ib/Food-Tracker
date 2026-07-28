"""
weight_plot.py — reproduces the exact trend+projection figure.

Depends only on: numpy, matplotlib, pandas, and weight_model.py (same dir).
render_trend_figure(data) returns (fig, fit): measured points, OLS fit over
the observed range, dashed linear projection beyond it, ±1σ residual band,
slope-CI cone, target lines with crossing-date markers, residual panel beneath.
"""

from __future__ import annotations
from datetime import timedelta
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.dates import DateFormatter
import pandas as pd

from app.services.weight_model import WeighIn, fit_trend, _clean, _to_days

_C_FIT, _C_PROJ, _C_PT, _C_SEED = "#4C72B0", "#C44E52", "#2b2b2b", "#888888"

_DEFAULT_TARGETS = [(100, "100 kg"), (95, "95 · 'trained'"),
                    (90, "90 · ~15%"), (86, "86 · ~12%")]


def render_trend_figure(data, targets=None, horizon_days=152,
                        show_seed=True, title=None):
    if targets is None:
        targets = _DEFAULT_TARGETS

    fit = fit_trend(data)
    measured = _clean(data)
    x_obs, y_obs, t0 = _to_days(measured)

    slope_day = fit.slope_per_week / 7.0
    se_day = fit.se_per_week / 7.0
    sd = fit.resid_sd

    last_day = x_obs.max()
    end_day = last_day + horizon_days
    xs = np.linspace(0, end_day, 600)
    xd = t0 + pd.to_timedelta(xs, unit="D")
    yhat = fit.intercept_kg + slope_day * xs
    lo = fit.intercept_kg + (slope_day - 1.96 * se_day) * xs
    hi = fit.intercept_kg + (slope_day + 1.96 * se_day) * xs
    obs = xs <= last_day

    fig, (ax, axr) = plt.subplots(
        2, 1, figsize=(12, 7.5), sharex=True,
        gridspec_kw={"height_ratios": [3, 1], "hspace": 0.08})

    ax.fill_between(xd, lo, hi, color=_C_PROJ, alpha=0.12, lw=0,
                    label="95%% CI on slope (%.2f to %.2f kg/wk)"
                          % (fit.slope_per_week - 1.96 * fit.se_per_week,
                             fit.slope_per_week + 1.96 * fit.se_per_week))
    ax.fill_between(xd[obs], (yhat - sd)[obs], (yhat + sd)[obs],
                    color=_C_FIT, alpha=0.15, lw=0,
                    label="±1σ observed (%.2f kg)" % sd)
    ax.plot(xd[obs], yhat[obs], color=_C_FIT, lw=2.2,
            label="OLS fit: %.2f kg/wk (r²=%.2f)" % (fit.slope_per_week, fit.r2))
    ax.plot(xd[~obs], yhat[~obs], color=_C_PROJ, lw=2.2, ls="--",
            label="linear projection (constant rate — upper bound)")
    ax.scatter([w.ts for w in measured], [w.kg for w in measured],
               s=42, color=_C_PT, zorder=6, label="measured (n=%d)" % fit.n)

    seeds = [w for w in data if w.is_seed]
    if show_seed and seeds:
        ax.scatter([w.ts for w in seeds], [w.kg for w in seeds], s=70,
                   marker="x", color=_C_SEED, zorder=6, label="seed (excluded)")

    for kg, lab in targets:
        ax.axhline(kg, color="#999", lw=0.8, ls="--", alpha=0.5)
        d = fit.day_for_weight(kg)
        if 0 <= d <= end_day:
            dt = t0 + timedelta(days=d)
            ax.plot([dt], [kg], marker="v", color=_C_PROJ, ms=8, zorder=7)
            ax.text(dt, kg + 0.5, dt.strftime("%d %b"),
                    fontsize=8, ha="center", color=_C_PROJ)
        ax.text(xd[-1], kg + 0.2, lab, fontsize=8, ha="right", color="#777")

    ax.set_ylabel("weight (kg)")
    ax.set_title(title or ("Weight trend + projection · measured only · %.2f kg/wk"
                           % fit.slope_per_week), loc="left", fontsize=12)
    ax.grid(alpha=0.25)
    ax.legend(fontsize=8, loc="lower left", framealpha=0.92)

    resid = y_obs - (fit.intercept_kg + slope_day * x_obs)
    axr.axhline(0, color=_C_FIT, lw=1)
    axr.axhspan(-sd, sd, color=_C_FIT, alpha=0.12, lw=0)
    axr.scatter([w.ts for w in measured], resid, s=30, color=_C_PT)
    axr.set_ylabel("resid")
    axr.grid(alpha=0.25)
    axr.xaxis.set_major_formatter(DateFormatter("%b %d"))
    fig.autofmt_xdate()

    return fig, fit


def figure_to_png_bytes(fig, dpi=160):
    import io
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=dpi, bbox_inches="tight")
    plt.close(fig)
    return buf.getvalue()

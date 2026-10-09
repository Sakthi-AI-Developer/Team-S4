from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import numpy as np

from .model_evaluation import evaluate_forecast
from .trend_models import fit_linear_trend


def forecast_vegetation_trends(observations: list[dict[str, Any]], horizon_days: int = 90) -> dict[str, Any]:
    if len(observations) < 2:
        return {
            "status": "insufficient-data",
            "message": "Forecasting requires at least two historical observations.",
            "forecast": None,
        }
    ordered = sorted(
        [obs for obs in observations if obs.get("mean_ndvi") is not None and obs.get("date") or obs.get("acquisition_date")],
        key=lambda obs: obs.get("date") or obs.get("acquisition_date"),
    )
    if len(ordered) < 2:
        return {
            "status": "insufficient-data",
            "message": "Forecasting requires at least two historical observations.",
            "forecast": None,
        }
    trend = fit_linear_trend(ordered)
    if trend["status"] != "ok":
        return {"status": "insufficient-data", "message": trend["message"], "forecast": None}

    dates = []
    values = []
    for obs in ordered:
        date_value = obs.get("date") or obs.get("acquisition_date")
        dt = None
        for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y%m%d"):
            try:
                dt = datetime.strptime(str(date_value), fmt)
                break
            except ValueError:
                continue
        if dt is None:
            continue
        dates.append(dt)
        values.append(float(obs["mean_ndvi"]))
    if len(values) < 2:
        return {"status": "insufficient-data", "message": "Forecasting requires at least two historical observations.", "forecast": None}
    last_date = dates[-1]
    x = np.array([(item - dates[0]).days for item in dates], dtype=float)
    y = np.array(values, dtype=float)
    slope, intercept = np.polyfit(x, y, 1)
    forecast_dates = [last_date + timedelta(days=step) for step in range(1, horizon_days + 1)]
    forecast_values = [intercept + slope * ((date - dates[0]).days) for date in [last_date + timedelta(days=step) for step in range(1, horizon_days + 1)]]
    residuals = y - (intercept + slope * x)
    sigma = float(np.std(residuals)) if residuals.size else 0.0
    interval = 1.96 * sigma if sigma > 0 else 0.0
    return {
        "status": "ok",
        "model_name": "linear_trend",
        "model_version": "1.0",
        "training_observation_count": len(values),
        "historical_trend": {
            "slope_per_day": float(slope),
            "intercept": float(intercept),
            "first_date": dates[0].date().isoformat(),
            "last_date": last_date.date().isoformat(),
        },
        "forecast_dates": [day.isoformat() for day in forecast_dates],
        "forecast_values": [float(value) for value in forecast_values],
        "prediction_interval": {
            "lower": [float(value - interval) for value in forecast_values],
            "upper": [float(value + interval) for value in forecast_values],
            "support": bool(interval > 0),
        },
        "limitations": "This is a baseline estimate based on historical NDVI patterns and should not be interpreted as a crop-yield or drought prediction.",
        "evaluation": evaluate_forecast(values),
    }

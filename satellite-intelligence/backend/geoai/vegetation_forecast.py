from __future__ import annotations

from datetime import timedelta
from typing import Any

import numpy as np
from scipy.stats import t as student_t

from .model_evaluation import evaluate_forecast
from .trend_models import _parse_date, fit_linear_trend


def forecast_vegetation_trends(observations: list[dict[str, Any]], horizon_days: int = 90) -> dict[str, Any]:
    if not 1 <= horizon_days <= 3650:
        raise ValueError("horizon_days must be between 1 and 3650.")
    if len(observations) < 2:
        return {
            "status": "insufficient-data",
            "message": "Forecasting requires at least two historical observations.",
            "forecast": None,
        }
    parsed_observations = []
    for observation in observations:
        date = _parse_date(
            observation.get("date") or observation.get("acquisition_date")
        )
        value = observation.get("mean_ndvi")
        if date is None or value is None:
            continue
        try:
            numeric_value = float(value)
        except (TypeError, ValueError):
            continue
        if np.isfinite(numeric_value):
            parsed_observations.append((date, numeric_value))
    ordered = sorted(parsed_observations, key=lambda item: item[0])
    if len(ordered) < 2:
        return {
            "status": "insufficient-data",
            "message": "Forecasting requires at least two historical observations.",
            "forecast": None,
        }
    if len({item[0].date() for item in ordered}) < 2:
        return {
            "status": "insufficient-data",
            "message": "Forecasting requires observations from at least two distinct dates.",
            "forecast": None,
        }
    trend = fit_linear_trend(
        [{"date": date, "mean_ndvi": value} for date, value in ordered]
    )
    if trend["status"] != "ok":
        return {"status": "insufficient-data", "message": trend["message"], "forecast": None}

    dates = [item[0] for item in ordered]
    values = [item[1] for item in ordered]
    last_date = dates[-1]
    x = np.array([(item - dates[0]).days for item in dates], dtype=float)
    y = np.array(values, dtype=float)
    slope, intercept = np.polyfit(x, y, 1)
    forecast_dates = [
        last_date + timedelta(days=step)
        for step in range(1, horizon_days + 1)
    ]
    forecast_values = [
        float(intercept + slope * (date - dates[0]).days)
        for date in forecast_dates
    ]
    residuals = y - (intercept + slope * x)
    prediction_interval = {
        "lower": None,
        "upper": None,
        "support": False,
        "message": "At least three dated observations with non-zero residual variance are required.",
    }
    if len(values) >= 3 and len(np.unique(x)) >= 3:
        degrees_of_freedom = len(values) - 2
        residual_variance = float(np.sum(np.square(residuals)) / degrees_of_freedom)
        if residual_variance > 0:
            x_future = np.array(
                [(date - dates[0]).days for date in forecast_dates],
                dtype=float,
            )
            standard_error = np.sqrt(
                residual_variance
                * (
                    1
                    + 1 / len(values)
                    + np.square(x_future - np.mean(x))
                    / np.sum(np.square(x - np.mean(x)))
                )
            )
            critical_value = float(student_t.ppf(0.975, degrees_of_freedom))
            margin = critical_value * standard_error
            prediction_interval = {
                "lower": [float(value - error) for value, error in zip(forecast_values, margin)],
                "upper": [float(value + error) for value, error in zip(forecast_values, margin)],
                "support": True,
                "method": "95% ordinary-least-squares prediction interval",
                "limitations": "Assumes independent, homoscedastic errors around a linear time trend.",
            }
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
        "forecast_dates": [day.date().isoformat() for day in forecast_dates],
        "forecast_values": forecast_values,
        "prediction_interval": prediction_interval,
        "limitations": "Linear extrapolation from the supplied NDVI series; it is not a crop-yield or drought prediction, and future values are not constrained to the NDVI range.",
        "evaluation": evaluate_forecast(values),
    }

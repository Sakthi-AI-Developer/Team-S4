from __future__ import annotations

from datetime import datetime
from typing import Any

import numpy as np


def _parse_date(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y%m%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S%z"):
            try:
                return datetime.strptime(text, fmt)
            except ValueError:
                continue
    return None


def fit_linear_trend(observations: list[dict[str, Any]]) -> dict[str, Any]:
    valid = []
    for observation in observations:
        date = _parse_date(observation.get("date") or observation.get("acquisition_date"))
        value = observation.get("mean_ndvi")
        if date is None or value is None:
            continue
        valid.append((date, float(value)))
    if len(valid) < 2:
        return {"status": "insufficient-data", "message": "Forecasting requires at least two historical observations."}
    ordered = sorted(valid, key=lambda item: item[0])
    dates = np.array([item[0].timestamp() for item in ordered], dtype=float)
    values = np.array([item[1] for item in ordered], dtype=float)
    x = dates - dates[0]
    slope, intercept = np.polyfit(x / 86400.0, values, 1)
    return {
        "status": "ok",
        "model_name": "linear_trend",
        "model_version": "1.0",
        "slope_per_day": float(slope),
        "intercept": float(intercept),
        "first_date": ordered[0][0].date().isoformat(),
        "last_date": ordered[-1][0].date().isoformat(),
        "observations": len(ordered),
    }


def summarize_observation_history(observations: list[dict[str, Any]]) -> dict[str, Any]:
    if not observations:
        return {"status": "insufficient-data", "message": "Historical observations are unavailable."}
    values = [float(item["mean_ndvi"]) for item in observations if item.get("mean_ndvi") is not None]
    if len(values) < 2:
        return {"status": "insufficient-data", "message": "At least two viable NDVI observations are required."}
    return {
        "status": "ok",
        "count": len(values),
        "mean_ndvi": float(np.mean(values)),
        "latest_ndvi": float(values[-1]),
        "start_date": observations[0].get("date") or observations[0].get("acquisition_date"),
        "end_date": observations[-1].get("date") or observations[-1].get("acquisition_date"),
        "series": observations,
    }

from __future__ import annotations

from typing import Any

import numpy as np


def evaluate_forecast(values: list[float] | np.ndarray, test_fraction: float = 0.25) -> dict[str, Any]:
    history = np.asarray(values, dtype=float)
    if history.size < 3:
        return {
            "status": "insufficient-data",
            "message": "Forecast evaluation unavailable: insufficient historical observations.",
            "metrics": {},
        }
    split_index = max(2, min(history.size - 1, int(len(history) * (1 - test_fraction))))
    train = history[:split_index]
    test = history[split_index:]
    if test.size == 0:
        return {
            "status": "insufficient-data",
            "message": "Forecast evaluation unavailable: insufficient historical observations.",
            "metrics": {},
        }
    x = np.arange(train.size, dtype=float)
    slope, intercept = np.polyfit(x, train, 1)
    predicted = intercept + slope * np.arange(train.size, train.size + test.size, dtype=float)
    actual = test
    error = predicted - actual
    mae = float(np.mean(np.abs(error)))
    rmse = float(np.sqrt(np.mean(np.square(error))))
    baseline = float(np.mean(np.abs(actual - train[-1])))
    mape = None
    nonzero = actual[np.abs(actual) > 1e-8]
    if nonzero.size:
        mape = float(np.mean(np.abs(error[ np.abs(actual) > 1e-8] / nonzero))) * 100.0
    return {
        "status": "ok",
        "method": "time-ordered train/test split",
        "baseline_method": "persistence",
        "metrics": {
            "mae": mae,
            "rmse": rmse,
            "mape": mape,
            "baseline_mae": baseline,
        },
        "training_observation_count": int(train.size),
        "test_observation_count": int(test.size),
        "limitations": "Evaluation is based on a chronological hold-out split and is suitable only for the available historical series.",
    }

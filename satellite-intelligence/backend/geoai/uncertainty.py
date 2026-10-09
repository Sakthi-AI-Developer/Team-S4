from __future__ import annotations

from typing import Any


def summarize_uncertainty(data_quality: dict[str, Any] | None, forecast_uncertainty: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "status": "ok",
        "data_quality": data_quality or {"status": "unknown", "message": "No detailed data-quality summary was provided."},
        "classification_confidence": {"status": "baseline", "value": None, "notes": "Classification uses heuristic thresholds, not validated probabilities."},
        "forecast_uncertainty": forecast_uncertainty or {"status": "insufficient-data", "support": False, "message": "Prediction intervals are only reported when supported by enough observations."},
        "insight_confidence": {"status": "review", "value": "potential indicator"},
        "limitations": "Data quality, classification confidence, forecast uncertainty, and insight confidence must be interpreted separately.",
    }

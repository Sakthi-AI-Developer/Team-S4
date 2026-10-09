from __future__ import annotations

from typing import Any


def detect_risk_indicators(spatial_summary: dict[str, Any], forecast_summary: dict[str, Any] | None = None, transition_summary: dict[str, Any] | None = None) -> dict[str, Any]:
    indicators = []
    ndvi = spatial_summary.get("ndvi", {})
    if ndvi.get("mean") is not None and ndvi.get("std") is not None and ndvi["mean"] < 0.2:
        indicators.append(
            {
                "indicator_name": "Potential persistent NDVI decline",
                "location": "study area",
                "supporting_metrics": {"ndvi_mean": ndvi["mean"], "ndvi_std": ndvi["std"]},
                "observation_dates": ["historical and current observations"],
                "data_quality": {"status": "available"},
                "severity": "medium",
                "limitations": "This is a potential indicator only and requires independent verification before any impact claim.",
            }
        )
    if spatial_summary.get("persistent_change", {}).get("mean_ndvi_delta") is not None:
        delta = spatial_summary["persistent_change"]["mean_ndvi_delta"]
        if delta < -0.05:
            indicators.append({
                "indicator_name": "Repeated vegetation change",
                "location": "study area",
                "supporting_metrics": {"mean_ndvi_delta": delta},
                "observation_dates": ["historical and current observations"],
                "data_quality": {"status": "available"},
                "severity": "medium",
                "limitations": "The signal indicates a trend change only; it is not a confirmed drought, disease, or damage event.",
            })
    if forecast_summary and forecast_summary.get("status") == "ok":
        indicators.append({
            "indicator_name": "Forecasted vegetation trajectory",
            "location": "study area",
            "supporting_metrics": {"forecast_values": forecast_summary.get("forecast_values", [])[:5]},
            "observation_dates": forecast_summary.get("forecast_dates", [])[:2],
            "data_quality": {"status": "available"},
            "severity": "low",
            "limitations": "This estimate is based solely on historical NDVI trends and prediction intervals.",
        })
    if transition_summary and transition_summary.get("status") == "ok":
        transitions = transition_summary.get("transitions", [])
        if transitions:
            indicators.append({
                "indicator_name": "Repeated land-cover transitions",
                "location": "study area",
                "supporting_metrics": {"transition_count": len(transitions), "largest_transition": transitions[0]},
                "observation_dates": ["historical and current observations"],
                "data_quality": {"status": "available"},
                "severity": "medium",
                "limitations": "Pixel-level transitions are not proof of causation or specific land-use impacts.",
            })
    return {"status": "ok", "indicators": indicators or [], "count": len(indicators)}

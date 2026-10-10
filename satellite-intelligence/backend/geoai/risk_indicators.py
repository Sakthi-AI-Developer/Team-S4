from __future__ import annotations

from typing import Any


def detect_risk_indicators(spatial_summary: dict[str, Any], forecast_summary: dict[str, Any] | None = None, transition_summary: dict[str, Any] | None = None) -> dict[str, Any]:
    indicators = []
    ndvi = spatial_summary.get("ndvi", {})
    if ndvi.get("mean") is not None and ndvi.get("std") is not None and ndvi["mean"] < 0.2:
        indicators.append(
            {
                "indicator_name": "Low mean NDVI indicator",
                "location": "input raster extent",
                "supporting_metrics": {"ndvi_mean": ndvi["mean"], "ndvi_std": ndvi["std"]},
                "evidence": "Current valid-pixel NDVI mean is below the code-defined 0.2 screening threshold.",
                "observation_dates": spatial_summary.get("observation_dates", []),
                "data_quality": spatial_summary.get("data_quality", {"status": "unknown"}),
                "limitations": "This is a single-period index indicator, not evidence of decline, drought, disease, or damage.",
            }
        )
    if spatial_summary.get("period_change", {}).get("mean_ndvi_delta") is not None:
        delta = spatial_summary["period_change"]["mean_ndvi_delta"]
        if delta < -0.05:
            indicators.append({
                "indicator_name": "Negative current-vs-historical NDVI difference",
                "location": "input raster extent",
                "supporting_metrics": {"mean_ndvi_delta": delta},
                "evidence": "Mean aligned-pixel NDVI difference is below the code-defined -0.05 screening threshold.",
                "observation_dates": spatial_summary.get("observation_dates", []),
                "data_quality": spatial_summary.get("data_quality", {"status": "unknown"}),
                "limitations": "This compares one current and one historical raster; it is not a repeated trend or causal diagnosis.",
            })
    if forecast_summary and forecast_summary.get("status") == "ok":
        indicators.append({
            "indicator_name": "Modelled vegetation trajectory",
            "location": "input raster extent",
            "supporting_metrics": {"forecast_values": forecast_summary.get("forecast_values", [])[:5]},
            "observation_dates": forecast_summary.get("forecast_dates", [])[:2],
            "data_quality": spatial_summary.get("data_quality", {"status": "unknown"}),
            "evidence": "A linear extrapolation from the supplied dated NDVI series.",
            "limitations": "This is not an observed risk or a crop-yield, drought, or damage prediction.",
        })
    if transition_summary and transition_summary.get("status") == "ok":
        transitions = transition_summary.get("transitions", [])
        if transitions:
            indicators.append({
                "indicator_name": "Land-cover difference between raster periods",
                "location": "input raster extent",
                "supporting_metrics": {"transition_count": len(transitions), "largest_transition": transitions[0]},
                "evidence": "A pixel-level comparison of one historical and one current raster.",
                "observation_dates": spatial_summary.get("observation_dates", []),
                "data_quality": spatial_summary.get("data_quality", {"status": "unknown"}),
                "limitations": "Pixel-level class differences are not proof of causation, repeated change, or specific land-use impacts.",
            })
    return {"status": "ok", "indicators": indicators or [], "count": len(indicators)}

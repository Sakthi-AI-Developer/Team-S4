from __future__ import annotations

import math
from typing import Any

import numpy as np

from processing.ndbi import calculate_ndbi
from processing.ndvi import calculate_ndvi
from processing.ndwi import calculate_ndwi


def _summarize_index(name: str, raster: np.ndarray, valid: np.ndarray, block_size: int = 10) -> dict[str, Any]:
    values = raster[valid & np.isfinite(raster)]
    if values.size == 0:
        return {"status": "insufficient-data", "name": name, "valid_pixels": 0, "mean": None, "std": None, "hotspots": []}
    if block_size <= 1:
        block_size = 1
    height, width = raster.shape
    rows = max(1, height // block_size)
    cols = max(1, width // block_size)
    aggregated = []
    for row in range(rows):
        for col in range(cols):
            r0 = row * block_size
            r1 = min(height, r0 + block_size)
            c0 = col * block_size
            c1 = min(width, c0 + block_size)
            patch = raster[r0:r1, c0:c1]
            patch_valid = valid[r0:r1, c0:c1]
            patch_values = patch[patch_valid & np.isfinite(patch)]
            if patch_values.size:
                aggregated.append({
                    "row": int(r0),
                    "col": int(c0),
                    "mean": float(np.mean(patch_values)),
                    "std": float(np.std(patch_values)),
                    "pixels": int(patch_values.size),
                })
    hotspot_values = sorted((item["mean"] for item in aggregated), key=lambda value: abs(value))
    return {
        "status": "ok",
        "name": name,
        "mean": float(np.mean(values)),
        "std": float(np.std(values)),
        "min": float(np.min(values)),
        "max": float(np.max(values)),
        "valid_pixels": int(values.size),
        "grid_cells": len(aggregated),
        "hotspots": [
            {
                "row": item["row"],
                "col": item["col"],
                "value": item["mean"],
            }
            for item in sorted(aggregated, key=lambda item: abs(item["mean"]), reverse=True)[:10]
        ],
        "spatial_hotspot_count": int(sum(1 for item in aggregated if abs(item["mean"]) > max(0.05, np.std(values) if values.size else 0.0))),
    }


def summarize_spatial_patterns(current_dataset: Any, historical_dataset: Any | None = None, block_size: int = 10) -> dict[str, Any]:
    if not current_dataset or not getattr(current_dataset, "bands", None):
        return {"status": "insufficient-data", "message": "Current imagery is unavailable for spatial analysis."}
    if "B04" not in current_dataset.bands or "B08" not in current_dataset.bands:
        return {"status": "insufficient-data", "message": "NDVI spatial analysis requires B04 and B08."}
    ndvi = calculate_ndvi(current_dataset.bands["B04"], current_dataset.bands["B08"])
    ndwi = calculate_ndwi(current_dataset.bands["B03"], current_dataset.bands["B08"]) if "B03" in current_dataset.bands and "B08" in current_dataset.bands else None
    ndbi = calculate_ndbi(current_dataset.bands["B08"], current_dataset.bands["B11"]) if "B08" in current_dataset.bands and "B11" in current_dataset.bands else None
    spatial = {
        "status": "ok",
        "method": "Grid aggregation over valid pixels; no synthetic hotspots are generated.",
        "limitations": "The result is a raster summary for the available AOI, not a validated ground-truth land-use map.",
        "ndvi": _summarize_index("NDVI", ndvi["raster"], ndvi["valid_mask"], block_size=block_size),
        "ndwi": _summarize_index("NDWI", ndwi["raster"], ndwi["valid_mask"], block_size=block_size) if ndwi else {"status": "insufficient-data", "name": "NDWI", "message": "NDWI unavailable: B03 and B08 are required."},
        "ndbi": _summarize_index("NDBI", ndbi["raster"], ndbi["valid_mask"], block_size=block_size) if ndbi else {"status": "insufficient-data", "name": "NDBI", "message": "NDBI unavailable: B08 and B11 are required."},
    }
    if historical_dataset and historical_dataset.bands:
        try:
            historical_ndvi = calculate_ndvi(historical_dataset.bands["B04"], historical_dataset.bands["B08"])
            if ndvi["valid_mask"].shape == historical_ndvi["valid_mask"].shape:
                valid = ndvi["valid_mask"] & historical_ndvi["valid_mask"]
                delta = np.full(ndvi["raster"].shape, np.nan, dtype=np.float32)
                delta[valid] = ndvi["raster"][valid] - historical_ndvi["raster"][valid]
                persistent_change = float(np.nanmean(delta[valid])) if np.any(valid) else None
                spatial["persistent_change"] = {
                    "mean_ndvi_delta": persistent_change,
                    "change_pixels": int(np.count_nonzero(valid & (delta > 0.05))),
                    "reduction_pixels": int(np.count_nonzero(valid & (delta < -0.05))),
                    "status": "ok",
                }
        except Exception:
            spatial["persistent_change"] = {"status": "insufficient-data", "message": "Historical change analysis could not be compared for this dataset."}
    else:
        spatial["persistent_change"] = {"status": "insufficient-data", "message": "Historical observations are unavailable for persistent-change analysis."}
    return spatial

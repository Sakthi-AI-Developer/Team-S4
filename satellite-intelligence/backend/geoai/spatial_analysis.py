from __future__ import annotations

import math
from typing import Any

import numpy as np

from processing.ndbi import calculate_ndbi
from processing.ndvi import calculate_ndvi
from processing.ndwi import calculate_ndwi
from processing.preprocessing import DatasetError, validate_alignment


def _summarize_index(name: str, raster: np.ndarray, valid: np.ndarray, block_size: int = 10) -> dict[str, Any]:
    values = raster[valid & np.isfinite(raster)]
    if values.size == 0:
        return {"status": "insufficient-data", "name": name, "valid_pixels": 0, "mean": None, "std": None, "hotspots": []}
    if block_size <= 1:
        block_size = 1
    height, width = raster.shape
    rows = max(1, math.ceil(height / block_size))
    cols = max(1, math.ceil(width / block_size))
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
    try:
        validate_alignment([current_dataset.bands["B04"], current_dataset.bands["B08"]])
    except DatasetError as exc:
        return {"status": "insufficient-data", "message": str(exc)}
    ndvi = calculate_ndvi(current_dataset.bands["B04"], current_dataset.bands["B08"])
    ndwi = calculate_ndwi(current_dataset.bands["B03"], current_dataset.bands["B08"]) if "B03" in current_dataset.bands and "B08" in current_dataset.bands else None
    ndbi = calculate_ndbi(current_dataset.bands["B08"], current_dataset.bands["B11"]) if "B08" in current_dataset.bands and "B11" in current_dataset.bands else None
    spatial = {
        "status": "ok",
        "method": "Grid aggregation over valid pixels; no synthetic hotspots are generated.",
        "limitations": "The result summarizes the full input raster extent; no AOI clipping or ground-truth validation is performed.",
        "ndvi": _summarize_index("NDVI", ndvi["raster"], ndvi["valid_mask"], block_size=block_size),
        "ndwi": _summarize_index("NDWI", ndwi["raster"], ndwi["valid_mask"], block_size=block_size) if ndwi else {"status": "insufficient-data", "name": "NDWI", "message": "NDWI unavailable: B03 and B08 are required."},
        "ndbi": _summarize_index("NDBI", ndbi["raster"], ndbi["valid_mask"], block_size=block_size) if ndbi else {"status": "insufficient-data", "name": "NDBI", "message": "NDBI unavailable: B08 and B11 are required."},
    }
    if historical_dataset and historical_dataset.bands:
        try:
            if "B04" not in historical_dataset.bands or "B08" not in historical_dataset.bands:
                raise DatasetError("Historical NDVI requires B04 and B08.")
            validate_alignment(
                [
                    historical_dataset.bands["B04"],
                    historical_dataset.bands["B08"],
                ]
            )
            current_reference = current_dataset.bands["B04"]
            historical_reference = historical_dataset.bands["B04"]
            if (
                current_reference.width != historical_reference.width
                or current_reference.height != historical_reference.height
                or current_reference.crs != historical_reference.crs
                or not np.allclose(
                    tuple(current_reference.transform),
                    tuple(historical_reference.transform),
                    rtol=0,
                    atol=1e-9,
                )
            ):
                raise DatasetError("Current and historical NDVI rasters are not on the same grid.")
            historical_ndvi = calculate_ndvi(historical_dataset.bands["B04"], historical_dataset.bands["B08"])
            valid = ndvi["valid_mask"] & historical_ndvi["valid_mask"]
            delta = np.full(ndvi["raster"].shape, np.nan, dtype=np.float32)
            delta[valid] = ndvi["raster"][valid] - historical_ndvi["raster"][valid]
            period_change = float(np.nanmean(delta[valid])) if np.any(valid) else None
            spatial["period_change"] = {
                "mean_ndvi_delta": period_change,
                "change_pixels": int(np.count_nonzero(valid & (delta > 0.05))),
                "reduction_pixels": int(np.count_nonzero(valid & (delta < -0.05))),
                "valid_pixels": int(np.count_nonzero(valid)),
                "status": "ok" if period_change is not None else "insufficient-data",
            }
        except DatasetError as exc:
            spatial["period_change"] = {
                "status": "insufficient-data",
                "message": str(exc),
            }
    else:
        spatial["period_change"] = {
            "status": "insufficient-data",
            "message": "A paired-period comparison is unavailable because historical imagery is missing.",
        }
    return spatial

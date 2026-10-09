from __future__ import annotations

from typing import Any

import numpy as np

from processing.landcover import LandCoverClassifier


def analyze_land_cover_transitions(current_dataset: Any, historical_dataset: Any | None = None) -> dict[str, Any]:
    if current_dataset is None or not getattr(current_dataset, "bands", None):
        return {"status": "insufficient-data", "message": "Current classification data is unavailable."}
    if historical_dataset is None or not getattr(historical_dataset, "bands", None):
        return {"status": "insufficient-data", "message": "Historical classification data is unavailable."}
    current_result = LandCoverClassifier().classify(current_dataset.bands)
    historical_result = LandCoverClassifier().classify(historical_dataset.bands)
    current_classes = current_result["raster"]
    historical_classes = historical_result["raster"]
    valid = current_result["valid_mask"] & historical_result["valid_mask"]
    if not np.any(valid):
        return {"status": "insufficient-data", "message": "No comparable valid pixels were available for transition analysis."}
    class_ids = sorted(set(np.unique(current_classes[valid]).tolist()) | set(np.unique(historical_classes[valid]).tolist()))
    transitions = []
    for src in class_ids:
        for dst in class_ids:
            mask = (current_classes == src) & (historical_classes == dst) & valid
            count = int(np.count_nonzero(mask))
            if count == 0:
                continue
            transitions.append(
                {
                    "source_class": int(src),
                    "destination_class": int(dst),
                    "source_label": current_result["class_names"].get(src, str(src)),
                    "destination_label": historical_result["class_names"].get(dst, str(dst)),
                    "pixel_count": count,
                    "percentage_of_valid_pixels": float(count / max(int(np.count_nonzero(valid)), 1) * 100.0),
                    "area_square_metres": None,
                }
            )
    transitions.sort(key=lambda item: (-item["pixel_count"], item["source_class"], item["destination_class"]))
    return {
        "status": "ok",
        "method": "Land-cover transition matrix from aligned rasters.",
        "limitations": "Transitions describe pixel-level class changes and do not establish a direct causal mechanism.",
        "transitions": transitions,
        "valid_pixel_count": int(np.count_nonzero(valid)),
    }

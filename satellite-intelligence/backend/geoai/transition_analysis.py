from __future__ import annotations

from typing import Any

import numpy as np

from processing.landcover import LandCoverClassifier
from processing.preprocessing import (
    DatasetError,
    pixel_area_square_metres,
    validate_alignment,
)


def analyze_land_cover_transitions(
    current_dataset: Any,
    historical_dataset: Any | None = None,
    include_area: bool = True,
) -> dict[str, Any]:
    if current_dataset is None or not getattr(current_dataset, "bands", None):
        return {"status": "insufficient-data", "message": "Current classification data is unavailable."}
    if historical_dataset is None or not getattr(historical_dataset, "bands", None):
        return {"status": "insufficient-data", "message": "Historical classification data is unavailable."}
    required = ("B03", "B04", "B08", "B11")
    if any(code not in current_dataset.bands or code not in historical_dataset.bands for code in required):
        return {"status": "insufficient-data", "message": "Both periods require aligned B03, B04, B08, and B11 bands."}
    current_bands = [current_dataset.bands[code] for code in required]
    historical_bands = [historical_dataset.bands[code] for code in required]
    try:
        validate_alignment(current_bands)
        validate_alignment(historical_bands)
    except DatasetError as exc:
        return {"status": "insufficient-data", "message": str(exc)}
    current_reference = current_bands[0]
    historical_reference = historical_bands[0]
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
        return {
            "status": "insufficient-data",
            "message": "Current and historical rasters are not on the same grid; align them before comparing transitions.",
        }
    current_result = LandCoverClassifier().classify(current_dataset.bands)
    historical_result = LandCoverClassifier().classify(historical_dataset.bands)
    current_classes = current_result["raster"]
    historical_classes = historical_result["raster"]
    valid = current_result["valid_mask"] & historical_result["valid_mask"]
    if not np.any(valid):
        return {"status": "insufficient-data", "message": "No comparable valid pixels were available for transition analysis."}
    class_ids = sorted(
        set(np.unique(current_classes[valid]).tolist())
        | set(np.unique(historical_classes[valid]).tolist())
    )
    transitions = []
    pixel_area = (
        pixel_area_square_metres(current_reference) if include_area else None
    )
    for src in class_ids:
        for dst in class_ids:
            mask = (historical_classes == src) & (current_classes == dst) & valid
            count = int(np.count_nonzero(mask))
            if count == 0:
                continue
            transitions.append(
                {
                    "source_class": int(src),
                    "destination_class": int(dst),
                    "source_label": historical_result["class_names"].get(src, str(src)),
                    "destination_label": current_result["class_names"].get(dst, str(dst)),
                    "pixel_count": count,
                    "percentage_of_valid_pixels": float(count / max(int(np.count_nonzero(valid)), 1) * 100.0),
                    "area_square_metres": count * pixel_area if pixel_area is not None else None,
                }
            )
    transitions.sort(key=lambda item: (-item["pixel_count"], item["source_class"], item["destination_class"]))
    return {
        "status": "ok",
        "method": "Historical-to-current land-cover transition matrix from aligned rasters.",
        "limitations": "Transitions describe pixel-level class changes and do not establish a direct causal mechanism.",
        "transitions": transitions,
        "valid_pixel_count": int(np.count_nonzero(valid)),
    }

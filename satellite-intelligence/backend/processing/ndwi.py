import numpy as np

from processing.preprocessing import (
    DatasetError,
    RasterBand,
    common_valid_mask,
    raster_statistics,
    safe_normalized_difference,
)


def calculate_ndwi(
    green: np.ndarray | RasterBand,
    nir: np.ndarray | RasterBand,
    water_threshold: float = 0.0,
) -> dict:
    """Calculate McFeeters NDWI: (Green - NIR) / (Green + NIR), using B03 and B08."""
    green_data = green.data if isinstance(green, RasterBand) else np.asarray(green, dtype=np.float32)
    nir_data = nir.data if isinstance(nir, RasterBand) else np.asarray(nir, dtype=np.float32)
    if green_data.shape != nir_data.shape:
        raise DatasetError("NDWI bands have different dimensions; align B03 and B08 first.")
    if isinstance(green, RasterBand) and isinstance(nir, RasterBand):
        valid = common_valid_mask([green, nir])
        reference = green
    else:
        valid = np.isfinite(green_data) & np.isfinite(nir_data)
        reference = None
    result, valid = safe_normalized_difference(green_data, nir_data, valid)
    stats = raster_statistics(result, valid)
    water_percent = (
        float(np.count_nonzero(result[valid] > water_threshold) / stats["valid_pixels"] * 100)
        if stats["valid_pixels"]
        else None
    )
    return {
        "raster": result,
        "valid_mask": valid,
        "statistics": stats,
        "water_related_percentage": water_percent,
        "water_threshold": water_threshold,
        "formulation": "McFeeters NDWI (B03 Green - B08 NIR) / (B03 Green + B08 NIR)",
        "reference": reference,
    }

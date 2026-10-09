import numpy as np

from processing.preprocessing import (
    DatasetError,
    RasterBand,
    common_valid_mask,
    raster_statistics,
    safe_normalized_difference,
)


def calculate_ndbi(
    nir: np.ndarray | RasterBand,
    swir: np.ndarray | RasterBand,
    built_up_threshold: float = 0.0,
) -> dict:
    """Calculate Sentinel-2 NDBI: (B11 SWIR - B08 NIR) / (B11 SWIR + B08 NIR)."""
    nir_data = nir.data if isinstance(nir, RasterBand) else np.asarray(nir, dtype=np.float32)
    swir_data = swir.data if isinstance(swir, RasterBand) else np.asarray(swir, dtype=np.float32)
    if nir_data.shape != swir_data.shape:
        raise DatasetError("NDBI bands have different dimensions; align B08 and B11 first.")
    if isinstance(nir, RasterBand) and isinstance(swir, RasterBand):
        valid = common_valid_mask([nir, swir])
        reference = nir
    else:
        valid = np.isfinite(nir_data) & np.isfinite(swir_data)
        reference = None
    result, valid = safe_normalized_difference(swir_data, nir_data, valid)
    stats = raster_statistics(result, valid)
    indicator_percent = (
        float(np.count_nonzero(result[valid] > built_up_threshold) / stats["valid_pixels"] * 100)
        if stats["valid_pixels"]
        else None
    )
    return {
        "raster": result,
        "valid_mask": valid,
        "statistics": stats,
        "built_up_indicator_percentage": indicator_percent,
        "indicator_threshold": built_up_threshold,
        "interpretation": "Satellite-derived built-up indicator; not definitive building detection.",
        "reference": reference,
    }

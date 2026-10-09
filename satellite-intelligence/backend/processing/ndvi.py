import numpy as np

from processing.preprocessing import (
    DatasetError,
    RasterBand,
    common_valid_mask,
    raster_statistics,
    safe_normalized_difference,
)


def calculate_ndvi(
    red: np.ndarray | RasterBand,
    nir: np.ndarray | RasterBand,
    vegetation_threshold: float = 0.3,
) -> dict:
    red_data = red.data if isinstance(red, RasterBand) else np.asarray(red, dtype=np.float32)
    nir_data = nir.data if isinstance(nir, RasterBand) else np.asarray(nir, dtype=np.float32)
    if red_data.shape != nir_data.shape:
        raise DatasetError("NDVI bands have different dimensions; align B04 and B08 first.")
    if isinstance(red, RasterBand) and isinstance(nir, RasterBand):
        valid = common_valid_mask([red, nir])
        reference = red
    else:
        valid = np.isfinite(red_data) & np.isfinite(nir_data)
        reference = None
    result, valid = safe_normalized_difference(nir_data, red_data, valid)
    stats = raster_statistics(result, valid)
    vegetation_percent = (
        float(np.count_nonzero(result[valid] >= vegetation_threshold) / stats["valid_pixels"] * 100)
        if stats["valid_pixels"]
        else None
    )
    return {
        "raster": result,
        "valid_mask": valid,
        "statistics": stats,
        "vegetation_percentage": vegetation_percent,
        "vegetation_threshold": vegetation_threshold,
        "reference": reference,
    }

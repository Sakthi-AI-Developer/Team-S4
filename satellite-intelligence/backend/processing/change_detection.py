import numpy as np

from processing.preprocessing import DatasetError, RasterBand, raster_statistics
from processing.ndvi import calculate_ndvi


def calculate_change(
    current_red: RasterBand,
    current_nir: RasterBand,
    historical_red: RasterBand,
    historical_nir: RasterBand,
) -> dict:
    from processing.preprocessing import validate_alignment

    validate_alignment((current_red, current_nir))
    validate_alignment((historical_red, historical_nir))
    if (current_red.width, current_red.height) != (historical_red.width, historical_red.height):
        raise DatasetError("Current and historical rasters have different dimensions; align them before change detection.")
    if current_red.crs != historical_red.crs:
        raise DatasetError("Current and historical rasters use different CRS; reproject them before change detection.")
    if not np.allclose(tuple(current_red.transform), tuple(historical_red.transform), rtol=0, atol=1e-9):
        raise DatasetError("Current and historical rasters have different geotransforms; align them before change detection.")
    current = calculate_ndvi(current_red, current_nir)
    historical = calculate_ndvi(historical_red, historical_nir)
    valid = current["valid_mask"] & historical["valid_mask"]
    difference = np.full(current["raster"].shape, np.nan, dtype=np.float32)
    difference[valid] = current["raster"][valid] - historical["raster"][valid]
    valid &= np.isfinite(difference)
    stats = raster_statistics(difference, valid)
    count = stats["valid_pixels"]
    positive = int(np.count_nonzero(difference[valid] > 0))
    negative = int(np.count_nonzero(difference[valid] < 0))
    unchanged = int(np.count_nonzero(difference[valid] == 0))
    historic_mean = float(np.mean(historical["raster"][valid])) if count else None
    current_mean = float(np.mean(current["raster"][valid])) if count else None
    percent_change = (
        float((current_mean - historic_mean) / abs(historic_mean) * 100)
        if count and historic_mean is not None and abs(historic_mean) > 1e-12
        else None
    )
    return {
        "raster": difference,
        "valid_mask": valid,
        "statistics": stats,
        "mean_change": stats["mean"],
        "historical_mean": historic_mean,
        "current_mean": current_mean,
        "positive_change_percentage": float(positive / count * 100) if count else None,
        "negative_change_percentage": float(negative / count * 100) if count else None,
        "changed_pixel_percentage": float((positive + negative) / count * 100) if count else None,
        "percentage_change": percent_change,
        "positive_pixels": positive,
        "negative_pixels": negative,
        "unchanged_pixels": unchanged,
        "reference": current_red,
    }

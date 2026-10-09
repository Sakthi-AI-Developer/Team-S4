import numpy as np

from processing.preprocessing import DatasetError, RasterBand, common_valid_mask, pixel_area_square_metres
from processing.ndvi import calculate_ndvi
from processing.ndwi import calculate_ndwi
from processing.ndbi import calculate_ndbi


CLASS_NAMES = {
    1: "Agriculture",
    2: "Vegetation",
    3: "Water",
    4: "Built-up",
    5: "Bare land",
}


class LandCoverClassifier:
    """Transparent index-threshold baseline, not a trained machine-learning model."""

    def classify(self, bands: dict[str, RasterBand]) -> dict:
        required = ("B03", "B04", "B08", "B11")
        missing = [code for code in required if code not in bands]
        if missing:
            raise DatasetError(f"Land-cover classification requires bands: {', '.join(missing)}.")
        valid = common_valid_mask([bands[code] for code in required])
        ndvi_result = calculate_ndvi(bands["B04"], bands["B08"])
        ndwi_result = calculate_ndwi(bands["B03"], bands["B08"])
        ndbi_result = calculate_ndbi(bands["B08"], bands["B11"])
        ndvi = ndvi_result["raster"]
        ndwi = ndwi_result["raster"]
        ndbi = ndbi_result["raster"]
        valid &= ndvi_result["valid_mask"]
        valid &= ndwi_result["valid_mask"]
        valid &= ndbi_result["valid_mask"]
        classes = np.zeros(valid.shape, dtype=np.uint8)
        water = ndwi > 0.1
        built = ~water & (ndbi > 0.05)
        vegetation = ~water & ~built & (ndvi >= 0.45)
        agriculture = ~water & ~built & ~vegetation & (ndvi >= 0.25)
        bare = valid & ~(water | built | vegetation | agriculture)
        classes[water & valid] = 3
        classes[built & valid] = 4
        classes[vegetation & valid] = 2
        classes[agriculture & valid] = 1
        classes[bare] = 5
        classes[~valid] = 0
        count = int(np.count_nonzero(valid))
        pixel_area = pixel_area_square_metres(bands["B03"])
        distributions = {}
        for class_id, name in CLASS_NAMES.items():
            pixels = int(np.count_nonzero(classes == class_id))
            distributions[str(class_id)] = {
                "name": name,
                "pixel_count": pixels,
                "percentage": float(pixels / count * 100) if count else None,
                "area_square_metres": pixels * pixel_area if pixel_area is not None else None,
            }
        return {
            "raster": classes,
            "valid_mask": valid,
            "class_names": CLASS_NAMES,
            "class_distribution": distributions,
            "confidence": {
                "label": "Baseline/heuristic confidence",
                "method": "Index-threshold rule agreement; not a calibrated model probability.",
                "value": None,
                "warning": "A supervised ML model can supply probability estimates once labelled training data is available.",
            },
            "method": "Transparent rule-based baseline using McFeeters NDWI, NDVI, and NDBI thresholds.",
            "model": {
                "status": "baseline",
                "trained": False,
                "description": "Fallback Stage-A classifier; no validated supervised training labels are assumed.",
            },
            "reference": bands["B03"],
        }

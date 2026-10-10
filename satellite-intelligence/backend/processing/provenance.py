from __future__ import annotations

from typing import Any

import numpy as np
from rasterio.crs import CRS
from rasterio.errors import CRSError

from processing.preprocessing import SatelliteDataset, raster_metadata


ANALYSIS_METHODS: dict[str, dict[str, str]] = {
    "ndvi": {
        "name": "NDVI",
        "version": "ndvi-v1",
        "formula": "(B08 NIR - B04 Red) / (B08 NIR + B04 Red)",
    },
    "ndwi": {
        "name": "McFeeters NDWI",
        "version": "mcfeeters-ndwi-v1",
        "formula": "(B03 Green - B08 NIR) / (B03 Green + B08 NIR)",
    },
    "ndbi": {
        "name": "NDBI",
        "version": "ndbi-v1",
        "formula": "(B11 SWIR - B08 NIR) / (B11 SWIR + B08 NIR)",
    },
    "landcover": {
        "name": "Index-threshold land-cover baseline",
        "version": "index-threshold-landcover-v1",
        "formula": "Ordered NDWI, NDBI, and NDVI thresholds; see methodology documentation.",
    },
    "change_detection": {
        "name": "NDVI difference",
        "version": "aligned-ndvi-difference-v1",
        "formula": "Current NDVI - historical NDVI on jointly valid aligned pixels.",
    },
}

_METADATA_KEYS = {
    "platform": ("PLATFORM", "SATELLITE", "SPACECRAFT_NAME"),
    "sensor": ("SENSOR", "INSTRUMENT"),
    "provider": ("PROVIDER", "DATA_SOURCE", "SOURCE"),
    "acquisition_date": ("ACQUISITION_DATE", "SENSING_TIME", "DATE_ACQUIRED"),
    "cloud_cover_percentage": ("CLOUD_COVER", "CLOUD_COVERAGE", "CLOUDY_PIXEL_PERCENTAGE"),
}


def _resolution_units(crs_text: str | None) -> str:
    if not crs_text:
        return "unknown"
    try:
        crs = CRS.from_string(crs_text)
    except CRSError:
        return "unknown"
    if crs.is_geographic:
        return "degrees"
    if crs.is_projected:
        return crs.linear_units or "unknown"
    return "unknown"


def _find_consistent_tag(bands, candidates: tuple[str, ...]) -> tuple[str | None, bool]:
    candidate_keys = {key.casefold() for key in candidates}
    values = {
        value.strip()
        for band in bands
        for key, value in band.tags.items()
        if key.casefold() in candidate_keys and value.strip()
    }
    if len(values) != 1:
        return None, len(values) > 1
    return values.pop(), False


def _is_synthetic(bands) -> bool:
    return any(
        key.casefold() in {"data_classification", "satellite_vision_data_kind"}
        and value.strip().casefold() in {"synthetic", "synthetic_demo", "mock"}
        for band in bands
        for key, value in band.tags.items()
    )


def dataset_provenance(
    dataset_id: str,
    dataset: SatelliteDataset,
    band_codes: tuple[str, ...],
) -> dict[str, Any]:
    bands = [dataset.bands[code] for code in band_codes if code in dataset.bands]
    metadata: dict[str, Any] = {}
    warnings: list[str] = []
    for field, keys in _METADATA_KEYS.items():
        value, conflict = _find_consistent_tag(bands, keys)
        metadata[field] = value
        if conflict:
            warnings.append(f"Conflicting {field.replace('_', ' ')} tags were omitted.")

    reference = bands[0] if bands else None
    reference_metadata = raster_metadata(reference)
    synthetic = _is_synthetic(bands)
    band_metadata = {
        band.code: _band_provenance(band)
        for band in bands
    }
    if metadata["acquisition_date"] is None:
        warnings.append("Acquisition date is not recorded in the input raster metadata.")
    if metadata["platform"] is None or metadata["sensor"] is None:
        warnings.append("Satellite platform and sensor are unverified for these local inputs.")
    if metadata["cloud_cover_percentage"] is None:
        warnings.append("No cloud-cover percentage or cloud-quality mask was supplied.")
    if any(
        values["valid_value_min"] is not None and values["valid_value_min"] < 0
        for values in band_metadata.values()
    ):
        warnings.append(
            "Negative input values are present; normalized-difference indices may fall outside [-1, 1] and are not clamped."
        )

    return {
        "dataset_id": dataset_id,
        "source": (
            "synthetic in-memory or mock fixture"
            if synthetic
            else "local GeoTIFF input"
        ),
        "data_classification": "synthetic" if synthetic else "unverified_local_input",
        **metadata,
        "crs": reference.crs if reference else None,
        "spatial_bounds_wgs84": (
            reference_metadata["bounds"] if reference_metadata else None
        ),
        "band_codes": list(band_codes),
        "bands": band_metadata,
        "quality_warnings": warnings,
    }


def _band_provenance(band) -> dict[str, Any]:
    values = band.data[band.valid & np.isfinite(band.data)]
    return {
        "crs": band.crs,
        "width": band.width,
        "height": band.height,
        "resolution": [float(value) for value in band.resolution],
        "resolution_units": _resolution_units(band.crs),
        "nodata_value": band.profile.get("nodata"),
        "valid_pixel_count": int(values.size),
        "total_pixel_count": int(band.valid.size),
        "valid_value_min": float(values.min()) if values.size else None,
        "valid_value_max": float(values.max()) if values.size else None,
        "nodata_or_invalid_percentage": float(
            (1 - values.size / max(band.valid.size, 1)) * 100
        ),
    }


def analysis_provenance(
    analysis_key: str,
    datasets: dict[str, tuple[SatelliteDataset, tuple[str, ...]]],
) -> dict[str, Any]:
    method = ANALYSIS_METHODS[analysis_key]
    return {
        "data_classification": (
            "synthetic"
            if any(
                dataset_provenance(period, dataset, bands)["data_classification"] == "synthetic"
                for period, (dataset, bands) in datasets.items()
            )
            else "unverified_local_input"
        ),
        "datasets": {
            period: dataset_provenance(period, dataset, bands)
            for period, (dataset, bands) in datasets.items()
        },
        "processing": {
            **method,
            "steps": [
                "Read GeoTIFF input metadata and NoData masks.",
                "Require matching dimensions, CRS, and affine transforms for all compared bands.",
                "Exclude invalid and zero-denominator pixels from statistics.",
            ],
        },
        "limitations": [
            "Acquisition and sensor metadata are reported only when present in the input tags.",
            "Band identifiers alone do not verify satellite, sensor, calibration, or reflectance units.",
            "No cloud masking or atmospheric correction is performed by this pipeline.",
        ],
    }

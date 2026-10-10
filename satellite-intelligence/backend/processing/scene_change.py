from __future__ import annotations

from collections.abc import Callable
import math
from pathlib import Path
from typing import Any

import numpy as np
import rasterio
from PIL import Image
from pyproj import CRS as PyprojCRS
from rasterio.enums import Resampling
from rasterio.errors import RasterioError
from rasterio.io import DatasetReader
from rasterio.transform import Affine, array_bounds
from rasterio.vrt import WarpedVRT
from rasterio.warp import calculate_default_transform
from rasterio.windows import Window

from processing.preprocessing import DatasetError, safe_normalized_difference
from processing.satellite_analysis import validate_band_mapping


WINDOW_SIZE = 256
PREVIEW_MAX_SIDE = 512
MEDIAN_SAMPLE_SIZE = 100_000
FLOAT_NODATA = -9999.0
MASK_NODATA = 255
MASK_UNCHANGED = 0
MASK_NEGATIVE = 1
MASK_POSITIVE = 2

CHANGE_LEGEND = [
    {"value": MASK_NEGATIVE, "label": "Candidate negative NDVI change", "color": "#c43c39"},
    {"value": MASK_UNCHANGED, "label": "Below threshold / little or no change", "color": "#f2e8b6"},
    {"value": MASK_POSITIVE, "label": "Candidate positive NDVI change", "color": "#2a9855"},
]


class _DifferenceStatistics:
    def __init__(self) -> None:
        self.count = 0
        self.minimum = math.inf
        self.maximum = -math.inf
        self.total = 0.0
        self.sample = np.empty(0, dtype=np.float32)
        self.priorities = np.empty(0, dtype=np.float64)
        self.rng = np.random.default_rng(42)

    def add(self, values: np.ndarray) -> None:
        if values.size == 0:
            return
        self.count += int(values.size)
        self.minimum = min(self.minimum, float(np.min(values)))
        self.maximum = max(self.maximum, float(np.max(values)))
        self.total += float(np.sum(values, dtype=np.float64))

        priorities = self.rng.random(values.size)
        combined_priorities = np.concatenate((self.priorities, priorities))
        combined_values = np.concatenate((self.sample, values.astype(np.float32, copy=False)))
        if combined_values.size > MEDIAN_SAMPLE_SIZE:
            selected = np.argpartition(combined_priorities, MEDIAN_SAMPLE_SIZE - 1)[
                :MEDIAN_SAMPLE_SIZE
            ]
            self.priorities = combined_priorities[selected]
            self.sample = combined_values[selected]
        else:
            self.priorities = combined_priorities
            self.sample = combined_values

    def as_dict(self) -> dict[str, Any]:
        if self.count == 0:
            raise DatasetError("The two scenes contain no common valid NDVI pixels.")
        return {
            "valid_pixels": self.count,
            "min": self.minimum,
            "max": self.maximum,
            "mean": self.total / self.count,
            "median": float(np.median(self.sample)),
            "median_method": (
                "Exact median for up to 100,000 valid pixels; otherwise median of a "
                "deterministic uniform reservoir sample of 100,000 pixels."
            ),
        }


def _windows(width: int, height: int):
    for row in range(0, height, WINDOW_SIZE):
        for col in range(0, width, WINDOW_SIZE):
                window_height = min(WINDOW_SIZE, height - row)
                window_width = min(WINDOW_SIZE, width - col)
                yield Window.from_slices(
                    (row, row + window_height),
                    (col, col + window_width),
                )


def _validate_source(source: DatasetReader, max_pixels: int) -> None:
    if source.crs is None:
        raise DatasetError("Both scenes must have a coordinate reference system.")
    if source.width < 1 or source.height < 1 or source.count < 1:
        raise DatasetError("A scene contains no readable raster pixels or bands.")
    if source.width * source.height > max_pixels:
        raise DatasetError(
            f"A scene exceeds the configured {max_pixels:,}-pixel comparison limit."
        )
    transform = source.transform
    determinant = transform.a * transform.e - transform.b * transform.d
    if (
        not all(math.isfinite(value) for value in tuple(transform))
        or not math.isfinite(determinant)
        or abs(determinant) <= 1e-18
    ):
        raise DatasetError("A scene has an invalid or singular geotransform.")
    if not all(math.isfinite(value) for value in source.bounds):
        raise DatasetError("A scene has an invalid spatial extent.")


def _read_scaled(
    source: DatasetReader,
    index: int,
    window: Window,
    scale_source: DatasetReader | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    masked = source.read(index, window=window, masked=True)
    values = np.asarray(masked.data, dtype=np.float32)
    metadata_source = scale_source or source
    scale = float(metadata_source.scales[index - 1])
    offset = float(metadata_source.offsets[index - 1])
    if not math.isfinite(scale) or not math.isfinite(offset):
        raise DatasetError(f"Band {index} has a non-finite scale or offset.")
    values = values * np.float32(scale) + np.float32(offset)
    valid = ~np.ma.getmaskarray(masked) & np.isfinite(values)
    return values, valid


def _pixel_area_model(
    source: DatasetReader,
) -> tuple[Callable[[int], float | None], str | None]:
    try:
        crs = PyprojCRS.from_wkt(source.crs.to_wkt())
    except (AttributeError, TypeError, ValueError):
        return lambda _row: None, "Pixel area is unavailable because the target CRS is invalid."

    determinant = abs(source.transform.a * source.transform.e - source.transform.b * source.transform.d)
    if crs.is_projected:
        units = crs.axis_info[0].unit_conversion_factor if crs.axis_info else None
        if units is not None and math.isfinite(units) and units > 0:
            area = determinant * units * units
            return lambda _row: area, None
        return lambda _row: None, "Pixel area is unavailable because projected CRS units are unknown."

    transform = source.transform
    if crs.is_geographic and abs(transform.b) < 1e-12 and abs(transform.d) < 1e-12:
        geod = crs.get_geod()
        if geod is None:
            return lambda _row: None, "Pixel area is unavailable because the geographic CRS has no geodesic model."
        center_x = transform.c + source.width * transform.a / 2

        def geographic_area(row: int) -> float | None:
            top = transform.f + row * transform.e
            bottom = top + transform.e
            left = center_x - transform.a / 2
            right = center_x + transform.a / 2
            area, _ = geod.polygon_area_perimeter(
                [left, right, right, left],
                [top, top, bottom, bottom],
            )
            area = abs(float(area))
            return area if math.isfinite(area) and area > 0 else None

        return geographic_area, None

    if crs.is_geographic:
        return (
            lambda _row: None,
            "Area is not reported for rotated geographic grids; degree-based pixel dimensions are not treated as metres.",
        )
    return lambda _row: None, "Area is not reported because the target CRS has no supported linear area model."


def _write_profile(source: DatasetReader, dtype: str, nodata: int | float) -> dict[str, Any]:
    profile = source.profile.copy()
    profile.pop("blockxsize", None)
    profile.pop("blockysize", None)
    profile.pop("photometric", None)
    profile.update(
        driver="GTiff",
        count=1,
        dtype=dtype,
        nodata=nodata,
        compress="deflate",
        BIGTIFF="IF_SAFER",
    )
    return profile


def _save_preview(mask_path: Path, preview_path: Path) -> tuple[int, int, list[float]]:
    colors = np.asarray(
        [
            (242, 232, 182),
            (196, 60, 57),
            (42, 152, 85),
        ],
        dtype=np.uint8,
    )
    with rasterio.open(mask_path) as source:
        transform, width, height = calculate_default_transform(
            source.crs,
            "EPSG:4326",
            source.width,
            source.height,
            *source.bounds,
        )
        scale = min(PREVIEW_MAX_SIDE / width, PREVIEW_MAX_SIDE / height, 1.0)
        output_width = max(1, math.ceil(width * scale))
        output_height = max(1, math.ceil(height * scale))
        preview_transform = transform @ Affine.scale(
            width / output_width,
            height / output_height,
        )
        west, south, east, north = array_bounds(
            output_height,
            output_width,
            preview_transform,
        )
        with WarpedVRT(
            source,
            crs="EPSG:4326",
            transform=preview_transform,
            width=output_width,
            height=output_height,
            resampling=Resampling.nearest,
            src_nodata=MASK_NODATA,
            nodata=MASK_NODATA,
        ) as warped:
            sample = warped.read(1, masked=True)
            values = np.asarray(sample.data)
            valid = ~np.ma.getmaskarray(sample) & (values != MASK_NODATA)
            rgba = np.zeros((output_height, output_width, 4), dtype=np.uint8)
            rgba[..., :3] = colors[np.clip(values.astype(np.int16), 0, 2)]
            rgba[..., 3] = np.where(valid, 220, 0).astype(np.uint8)
            Image.fromarray(rgba, mode="RGBA").save(
                preview_path,
                format="PNG",
                optimize=True,
            )
        return output_width, output_height, [
            float(west),
            float(south),
            float(east),
            float(north),
        ]


def run_ndvi_change_detection(
    baseline_path: Path,
    comparison_path: Path,
    output_directory: Path,
    *,
    baseline_metadata: dict[str, Any],
    comparison_metadata: dict[str, Any],
    baseline_band_mapping: dict[str, int] | None,
    comparison_band_mapping: dict[str, int] | None,
    threshold: float,
    max_pixels: int,
    max_output_bytes: int,
    deadline_check: Callable[[], None] | None = None,
) -> dict[str, Any]:
    if not math.isfinite(threshold) or not 0 < threshold <= 2:
        raise DatasetError(
            "The absolute NDVI-difference threshold must be greater than 0 and at most 2."
        )

    try:
        with rasterio.open(baseline_path) as baseline, rasterio.open(
            comparison_path
        ) as comparison:
            _validate_source(baseline, max_pixels)
            _validate_source(comparison, max_pixels)
            baseline_mapping = validate_band_mapping(
                baseline,
                "ndvi",
                baseline_metadata,
                baseline_band_mapping,
            )
            comparison_mapping = validate_band_mapping(
                comparison,
                "ndvi",
                comparison_metadata,
                comparison_band_mapping,
            )

            output_directory.mkdir(parents=True, exist_ok=True)
            difference_path = output_directory / "ndvi-difference.tif"
            mask_path = output_directory / "change-mask.tif"
            difference_stats = _DifferenceStatistics()
            total_pixels = baseline.width * baseline.height
            positive_pixels = 0
            negative_pixels = 0
            unchanged_pixels = 0
            valid_area = 0.0
            changed_area = 0.0
            positive_area = 0.0
            negative_area = 0.0
            area_supported = True
            pixel_area, area_warning = _pixel_area_model(baseline)
            area_warnings = [area_warning] if area_warning else []
            difference_profile = _write_profile(baseline, "float32", FLOAT_NODATA)
            mask_profile = _write_profile(baseline, "uint8", MASK_NODATA)

            with WarpedVRT(
                comparison,
                crs=baseline.crs,
                transform=baseline.transform,
                width=baseline.width,
                height=baseline.height,
                resampling=Resampling.bilinear,
                src_nodata=comparison.nodata,
                nodata=FLOAT_NODATA,
            ) as aligned, rasterio.open(
                difference_path,
                "w",
                **difference_profile,
            ) as difference_output, rasterio.open(
                mask_path,
                "w",
                **mask_profile,
            ) as mask_output:
                for window in _windows(baseline.width, baseline.height):
                    if deadline_check:
                        deadline_check()
                    baseline_red, baseline_red_valid = _read_scaled(
                        baseline,
                        baseline_mapping["red"],
                        window,
                    )
                    baseline_nir, baseline_nir_valid = _read_scaled(
                        baseline,
                        baseline_mapping["nir"],
                        window,
                    )
                    comparison_red, comparison_red_valid = _read_scaled(
                        aligned,
                        comparison_mapping["red"],
                        window,
                        comparison,
                    )
                    comparison_nir, comparison_nir_valid = _read_scaled(
                        aligned,
                        comparison_mapping["nir"],
                        window,
                        comparison,
                    )

                    baseline_ndvi, baseline_valid = safe_normalized_difference(
                        baseline_nir,
                        baseline_red,
                        baseline_red_valid & baseline_nir_valid,
                    )
                    comparison_ndvi, comparison_valid = safe_normalized_difference(
                        comparison_nir,
                        comparison_red,
                        comparison_red_valid & comparison_nir_valid,
                    )
                    valid = baseline_valid & comparison_valid
                    difference = np.full(valid.shape, np.nan, dtype=np.float32)
                    difference[valid] = comparison_ndvi[valid] - baseline_ndvi[valid]
                    valid &= np.isfinite(difference)
                    difference_stats.add(difference[valid])

                    candidate_negative = valid & (difference <= -threshold)
                    candidate_positive = valid & (difference >= threshold)
                    candidate_changed = candidate_negative | candidate_positive
                    candidate_unchanged = valid & ~candidate_changed
                    negative_pixels += int(np.count_nonzero(candidate_negative))
                    positive_pixels += int(np.count_nonzero(candidate_positive))
                    unchanged_pixels += int(np.count_nonzero(candidate_unchanged))

                    mask_values = np.full(valid.shape, MASK_NODATA, dtype=np.uint8)
                    mask_values[candidate_unchanged] = MASK_UNCHANGED
                    mask_values[candidate_negative] = MASK_NEGATIVE
                    mask_values[candidate_positive] = MASK_POSITIVE
                    difference_values = np.full(valid.shape, FLOAT_NODATA, dtype=np.float32)
                    difference_values[valid] = difference[valid]
                    difference_output.write(difference_values, 1, window=window)
                    mask_output.write(mask_values, 1, window=window)

                    row_start = int(window.row_off)
                    for local_row in range(int(window.height)):
                        global_row = row_start + local_row
                        row_area = pixel_area(global_row)
                        if row_area is None:
                            area_supported = False
                            continue
                        valid_area += int(np.count_nonzero(valid[local_row])) * row_area
                        changed_area += int(np.count_nonzero(candidate_changed[local_row])) * row_area
                        positive_area += int(np.count_nonzero(candidate_positive[local_row])) * row_area
                        negative_area += int(np.count_nonzero(candidate_negative[local_row])) * row_area

                tags = {
                    "analysis": "NDVI_CHANGE_DETECTION",
                    "formula": "comparison-date NDVI - baseline-date NDVI",
                    "threshold": repr(float(threshold)),
                    "threshold_units": "absolute NDVI difference",
                    "threshold_comparison": "candidate change when abs(difference) >= threshold",
                    "baseline_red_band": str(baseline_mapping["red"]),
                    "baseline_nir_band": str(baseline_mapping["nir"]),
                    "comparison_red_band": str(comparison_mapping["red"]),
                    "comparison_nir_band": str(comparison_mapping["nir"]),
                    "resampling": "bilinear",
                    "target_grid": "baseline scene grid",
                }
                difference_output.update_tags(**tags)
                mask_output.update_tags(
                    **tags,
                    mask_values="0=below threshold,1=candidate negative,2=candidate positive,255=nodata",
                )

            statistics = difference_stats.as_dict()
            valid_pixels = statistics["valid_pixels"]
            if valid_pixels < 1:
                raise DatasetError("The two scenes contain no common valid NDVI pixels.")
            if not area_supported:
                valid_area = changed_area = positive_area = negative_area = 0.0

            preview_path = output_directory / "preview.png"
            preview_width, preview_height, bounds = _save_preview(mask_path, preview_path)
            output_bytes = sum(
                path.stat().st_size
                for path in (difference_path, mask_path, preview_path)
            )
            if output_bytes > max_output_bytes:
                raise DatasetError(
                    f"Change-detection outputs exceed the configured {max_output_bytes:,}-byte artifact limit."
                )

            excluded_pixels = total_pixels - valid_pixels
            return {
                "method": "Windowed NDVI difference on the baseline grid",
                "formula": "comparison-date NDVI - baseline-date NDVI",
                "baseline_bands": baseline_mapping,
                "comparison_bands": comparison_mapping,
                "threshold": float(threshold),
                "threshold_units": "absolute NDVI difference",
                "threshold_comparison": "abs(difference) >= threshold",
                "statistics": statistics,
                "valid_comparison_pixels": valid_pixels,
                "total_target_pixels": total_pixels,
                "excluded_pixels": excluded_pixels,
                "valid_pixel_percentage": float(valid_pixels / total_pixels * 100),
                "changed_pixels": positive_pixels + negative_pixels,
                "unchanged_pixels": unchanged_pixels,
                "positive_change_pixels": positive_pixels,
                "negative_change_pixels": negative_pixels,
                "change_percentage_of_valid_pixels": float(
                    (positive_pixels + negative_pixels) / valid_pixels * 100
                ),
                "valid_comparison_area_square_metres": (
                    valid_area if area_supported else None
                ),
                "changed_area_square_metres": changed_area if area_supported else None,
                "positive_change_area_square_metres": (
                    positive_area if area_supported else None
                ),
                "negative_change_area_square_metres": (
                    negative_area if area_supported else None
                ),
                "area_warnings": area_warnings,
                "alignment": {
                    "target": "baseline scene grid",
                    "resampling": "bilinear for continuous reflectance bands",
                    "baseline_crs": baseline.crs.to_string(),
                    "baseline_transform": list(baseline.transform),
                    "baseline_width": baseline.width,
                    "baseline_height": baseline.height,
                    "comparison_was_reprojected_or_resampled": (
                        comparison.crs != baseline.crs
                        or comparison.transform != baseline.transform
                        or comparison.width != baseline.width
                        or comparison.height != baseline.height
                    ),
                },
                "nodata_policy": (
                    "A pixel is valid only when both red and NIR bands are unmasked, "
                    "finite, and have non-zero normalized-difference denominators on both dates."
                ),
                "raster": {"artifact_name": difference_path.name},
                "change_mask": {
                    "artifact_name": mask_path.name,
                    "values": {
                        "0": "Below threshold / little or no change",
                        "1": "Candidate negative NDVI change",
                        "2": "Candidate positive NDVI change",
                        str(MASK_NODATA): "No valid comparison data",
                    },
                },
                "preview": {
                    "artifact_name": preview_path.name,
                    "width": preview_width,
                    "height": preview_height,
                    "bounds": bounds,
                },
                "bounds": bounds,
                "legend": CHANGE_LEGEND,
                "limitations": (
                    "Thresholded pixels are candidate NDVI changes, not confirmed environmental events. "
                    "Threshold suitability depends on sensor calibration, season, biome, atmospheric "
                    "conditions, and registration quality. Input metadata and band labels are not independently authenticated."
                ),
                "warnings": [
                    "The comparison scene is bilinearly resampled to the baseline grid; spatial resolution is not increased.",
                    "Only the common valid-data footprint contributes to statistics.",
                    "Metadata-derived acquisition dates and sensor labels are unverified source-file claims.",
                ],
            }
    except DatasetError:
        raise
    except (RasterioError, OSError, ValueError, OverflowError) as exc:
        raise DatasetError(
            "The selected scenes could not be aligned and compared as georeferenced GeoTIFFs."
        ) from exc

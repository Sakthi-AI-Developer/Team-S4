from __future__ import annotations

from collections.abc import Callable
import math
from pathlib import Path
from typing import Any, Literal

import numpy as np
import rasterio
from PIL import Image
from rasterio.enums import Resampling
from rasterio.errors import RasterioError
from rasterio.io import DatasetReader
from rasterio.transform import Affine, array_bounds
from rasterio.vrt import WarpedVRT
from rasterio.warp import calculate_default_transform
from rasterio.windows import Window
from sklearn.cluster import MiniBatchKMeans

from processing.preprocessing import DatasetError, safe_normalized_difference


AnalysisType = Literal["ndvi", "ndwi", "kmeans"]
WINDOW_SIZE = 256
MAX_KMEANS_FEATURES = 16
KMEANS_SAMPLE_SIZE = 50_000
INDEX_HISTOGRAM_BINS = 4096
INDEX_HISTOGRAM_MIN = -1.0
INDEX_HISTOGRAM_MAX = 1.0
PREVIEW_MAX_SIDE = 512

ANALYSIS_CATALOG: tuple[dict[str, Any], ...] = (
    {
        "id": "ndvi",
        "name": "Normalized Difference Vegetation Index (NDVI)",
        "formula": "(NIR - Red) / (NIR + Red)",
        "roles": {"red": "Red", "nir": "Near infrared"},
        "automatic_codes": {"red": "B04", "nir": "B08"},
        "legend": [
            {"range": "< 0", "label": "Lower relative vegetation response", "color": "#8c6a9e"},
            {"range": "0 to 0.2", "label": "Low relative vegetation response", "color": "#c98555"},
            {"range": "0.2 to 0.5", "label": "Moderate relative vegetation response", "color": "#d9c66b"},
            {"range": "> 0.5", "label": "Higher relative vegetation response", "color": "#357a45"},
        ],
        "interpretation_note": (
            "Illustrative value ranges only; interpretation depends on sensor, biome, season, "
            "surface, and processing and is not a universal vegetation classification."
        ),
    },
    {
        "id": "ndwi",
        "name": "McFeeters Normalized Difference Water Index (NDWI)",
        "formula": "(Green - NIR) / (Green + NIR)",
        "roles": {"green": "Green", "nir": "Near infrared"},
        "automatic_codes": {"green": "B03", "nir": "B08"},
        "legend": [
            {"range": "< 0", "label": "Lower relative water-index response", "color": "#a76c4f"},
            {"range": "0 to 0.2", "label": "Intermediate water-index response", "color": "#c8c5a0"},
            {"range": "> 0.2", "label": "Higher water-index response", "color": "#357eb3"},
        ],
        "interpretation_note": (
            "McFeeters green/NIR water-related NDWI; this is not the Gao NIR/SWIR vegetation "
            "liquid-water index. Values are not a universal water mask."
        ),
    },
    {
        "id": "kmeans",
        "name": "Unsupervised multispectral K-means",
        "formula": "Deterministic MiniBatch K-means over selected, standardized spectral bands.",
        "roles": {},
        "automatic_codes": {},
        "legend": [],
        "interpretation_note": (
            "Clusters are spectral groups, not verified land-cover classes. No cluster is "
            "labelled as water, forest, agriculture, or urban."
        ),
    },
)

_INDEX_PALETTES = {
    "ndvi": np.asarray(
        [
            (113, 75, 126),
            (161, 106, 79),
            (210, 184, 91),
            (115, 151, 81),
            (44, 117, 61),
        ],
        dtype=np.float32,
    ),
    "ndwi": np.asarray(
        [
            (153, 96, 66),
            (194, 162, 118),
            (215, 213, 175),
            (125, 177, 203),
            (40, 100, 160),
        ],
        dtype=np.float32,
    ),
}


def analysis_catalog(scene_metadata: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Describe supported methods and, when supplied, codes identified in a scene."""
    catalog = []
    identified: dict[str, list[int]] = {}
    band_count = None
    if scene_metadata:
        band_count = scene_metadata.get("band_count")
        for band in scene_metadata.get("bands", []):
            if isinstance(band, dict) and isinstance(band.get("code"), str):
                index = band.get("index")
                if isinstance(index, int):
                    identified.setdefault(band["code"], []).append(index)
    for definition in ANALYSIS_CATALOG:
        item = {**definition}
        required = definition["automatic_codes"]
        item["detected_bands"] = {
            role: (identified.get(code, [None])[0] if len(identified.get(code, [])) == 1 else None)
            for role, code in required.items()
        }
        item["ambiguous_bands"] = [
            role for role, code in required.items() if len(identified.get(code, [])) > 1
        ]
        item["available"] = (
            all(item["detected_bands"].values()) if required else bool(band_count and band_count >= 2)
        )
        item["band_count"] = band_count
        catalog.append(item)
    return catalog


def validate_band_mapping(
    source: DatasetReader,
    analysis: AnalysisType,
    scene_metadata: dict[str, Any],
    supplied_mapping: dict[str, int] | None,
) -> dict[str, int]:
    definition = next(item for item in ANALYSIS_CATALOG if item["id"] == analysis)
    if analysis == "kmeans":
        return {}
    roles: dict[str, str] = definition["automatic_codes"]
    role_labels: dict[str, str] = definition["roles"]
    if supplied_mapping is None:
        found: dict[str, list[int]] = {}
        for band in scene_metadata.get("bands", []):
            if not isinstance(band, dict):
                continue
            code, index = band.get("code"), band.get("index")
            for role, expected_code in roles.items():
                if code == expected_code and isinstance(index, int):
                    found.setdefault(role, []).append(index)
        ambiguous = [role for role in roles if len(found.get(role, [])) > 1]
        if ambiguous:
            raise DatasetError(
                "Band identity is ambiguous for "
                + ", ".join(ambiguous)
                + "; choose the source band numbers explicitly."
            )
        mapping = {
            role: indexes[0]
            for role, indexes in found.items()
            if len(indexes) == 1
        }
        missing = [role for role in roles if role not in mapping]
        if missing:
            labels = ", ".join(
                f"{role_labels[role]} ({roles[role]})" for role in missing
            )
            raise DatasetError(
                f"{analysis.upper()} requires an identifiable band for {labels}; "
                "select the matching source band numbers explicitly."
            )
    else:
        if set(supplied_mapping) != set(roles):
            raise DatasetError(
                f"{analysis.upper()} band mapping must select exactly: "
                + ", ".join(roles)
                + "."
            )
        mapping = supplied_mapping
    if len(set(mapping.values())) != len(mapping):
        raise DatasetError("Each required spectral role must use a different source band.")
    if any(not isinstance(index, int) or not 1 <= index <= source.count for index in mapping.values()):
        raise DatasetError(
            f"Selected band numbers must be between 1 and {source.count}."
        )
    return mapping


def _windows(width: int, height: int):
    for row in range(0, height, WINDOW_SIZE):
        for col in range(0, width, WINDOW_SIZE):
            yield Window.from_slices(
                slice(row, row + min(WINDOW_SIZE, height - row)),
                slice(col, col + min(WINDOW_SIZE, width - col)),
            )


def _read_features(
    source: DatasetReader,
    indexes: list[int],
    window: Window,
) -> tuple[np.ndarray, np.ndarray]:
    channels = []
    valid = np.ones(
        (int(window.height), int(window.width)),
        dtype=bool,
    )
    for index in indexes:
        band = source.read(index, window=window, masked=True)
        values = np.asarray(band.data, dtype=np.float32)
        values = values * np.float32(source.scales[index - 1]) + np.float32(
            source.offsets[index - 1]
        )
        valid &= ~np.ma.getmaskarray(band) & np.isfinite(values)
        channels.append(values)
    features = np.stack(channels, axis=-1)
    valid &= np.all(np.isfinite(features), axis=-1)
    return features, valid


def _output_profile(source: DatasetReader, dtype: str, nodata: int | float) -> dict:
    profile = source.profile.copy()
    profile.pop("blockxsize", None)
    profile.pop("blockysize", None)
    profile.update(
        driver="GTiff",
        count=1,
        dtype=dtype,
        nodata=nodata,
        compress="deflate",
        BIGTIFF="IF_SAFER",
    )
    return profile


class _IndexStatistics:
    def __init__(self) -> None:
        self.count = 0
        self.minimum = math.inf
        self.maximum = -math.inf
        self.sum = 0.0
        self.histogram = np.zeros(INDEX_HISTOGRAM_BINS, dtype=np.int64)
        self.histogram_underflow = 0
        self.rng = np.random.default_rng(42)
        self.median_sample = np.empty(0, dtype=np.float32)
        self.median_priorities = np.empty(0, dtype=np.float64)

    def add(self, values: np.ndarray) -> None:
        if values.size == 0:
            return
        self.count += int(values.size)
        self.minimum = min(self.minimum, float(np.min(values)))
        self.maximum = max(self.maximum, float(np.max(values)))
        self.sum += float(np.sum(values, dtype=np.float64))
        self.histogram_underflow += int(np.count_nonzero(values < INDEX_HISTOGRAM_MIN))
        histogram, _ = np.histogram(
            values,
            bins=INDEX_HISTOGRAM_BINS,
            range=(INDEX_HISTOGRAM_MIN, INDEX_HISTOGRAM_MAX),
        )
        self.histogram += histogram
        self.median_sample, self.median_priorities = _reservoir_add(
            self.median_sample.reshape(-1, 1),
            self.median_priorities,
            values.reshape(-1, 1),
            self.rng,
        )

    def to_dict(self) -> dict[str, Any]:
        if not self.count:
            raise DatasetError("The selected bands contain no valid index pixels.")
        central_count = int(np.sum(self.histogram))
        bin_width = (INDEX_HISTOGRAM_MAX - INDEX_HISTOGRAM_MIN) / INDEX_HISTOGRAM_BINS

        def histogram_quantile(rank: int) -> float | None:
            if not self.histogram_underflow <= rank < self.histogram_underflow + central_count:
                return None
            central_rank = rank - self.histogram_underflow
            bin_index = int(np.searchsorted(np.cumsum(self.histogram), central_rank + 1))
            return INDEX_HISTOGRAM_MIN + (bin_index + 0.5) * bin_width

        lower = histogram_quantile((self.count - 1) // 2)
        upper = histogram_quantile(self.count // 2)
        if lower is not None and upper is not None:
            median = (lower + upper) / 2
            median_method = (
                f"Histogram estimate with {INDEX_HISTOGRAM_BINS} bins across [-1, 1]; "
                f"maximum bin width {bin_width:.7f}."
            )
        else:
            median = float(np.median(self.median_sample))
            median_method = (
                f"Median of a deterministic reservoir sample of at most "
                f"{KMEANS_SAMPLE_SIZE:,} pixels (needed because the median falls outside "
                "the histogram's [-1, 1] range)."
            )
        return {
            "valid_pixels": self.count,
            "min": self.minimum,
            "max": self.maximum,
            "mean": self.sum / self.count,
            "median": median,
            "median_method": median_method,
        }


def _index_colors(kind: str, values: np.ndarray) -> np.ndarray:
    palette = _INDEX_PALETTES[kind]
    scaled = np.clip((values + 1.0) * (len(palette) - 1) / 2.0, 0, len(palette) - 1)
    lower = np.floor(scaled).astype(np.int32)
    upper = np.minimum(lower + 1, len(palette) - 1)
    fraction = (scaled - lower)[..., None]
    return np.clip(palette[lower] * (1 - fraction) + palette[upper] * fraction, 0, 255)


def _preview_grid(source: DatasetReader) -> tuple[Affine, int, int, list[float]]:
    if source.crs is None:
        raise DatasetError("The scene has no coordinate reference system.")
    transform, width, height = calculate_default_transform(
        source.crs,
        "EPSG:4326",
        source.width,
        source.height,
        *source.bounds,
    )
    scale = min(PREVIEW_MAX_SIDE / width, PREVIEW_MAX_SIDE / height, 1.0)
    preview_width = max(1, math.ceil(width * scale))
    preview_height = max(1, math.ceil(height * scale))
    preview_transform = transform @ Affine.scale(
        width / preview_width,
        height / preview_height,
    )
    bounds = array_bounds(preview_height, preview_width, preview_transform)
    return (
        preview_transform,
        preview_width,
        preview_height,
        [float(value) for value in bounds],
    )


def _save_index_preview(
    raster_path: Path,
    preview_path: Path,
    kind: str,
) -> tuple[int, int, list[float]]:
    with rasterio.open(raster_path) as output:
        transform, width, height, bounds = _preview_grid(output)
        with WarpedVRT(
            output,
            crs="EPSG:4326",
            transform=transform,
            width=width,
            height=height,
            resampling=Resampling.nearest,
            nodata=output.nodata,
        ) as warped:
            sampled = warped.read(1, masked=True)
            values = np.asarray(sampled.data, dtype=np.float32)
            valid = ~np.ma.getmaskarray(sampled) & np.isfinite(values)
            rgba = np.zeros((height, width, 4), dtype=np.uint8)
            rgba[..., :3] = _index_colors(kind, values).astype(np.uint8)
            rgba[..., 3] = np.where(valid, 230, 0).astype(np.uint8)
            Image.fromarray(rgba, mode="RGBA").save(preview_path, format="PNG", optimize=True)
            return width, height, bounds


def _run_index(
    source: DatasetReader,
    result_directory: Path,
    analysis: Literal["ndvi", "ndwi"],
    mapping: dict[str, int],
    deadline_check: Callable[[], None],
) -> dict[str, Any]:
    if analysis == "ndvi":
        first, second = mapping["nir"], mapping["red"]
        formula = "(NIR - Red) / (NIR + Red)"
    else:
        first, second = mapping["green"], mapping["nir"]
        formula = "(Green - NIR) / (Green + NIR)"

    raster_path = result_directory / f"{analysis}.tif"
    statistics = _IndexStatistics()
    output_profile = _output_profile(source, "float32", -9999.0)
    with rasterio.open(raster_path, "w", **output_profile) as destination:
        for window in _windows(source.width, source.height):
            deadline_check()
            first_data, first_valid = _read_features(source, [first], window)
            second_data, second_valid = _read_features(source, [second], window)
            valid = first_valid & second_valid
            values, valid = safe_normalized_difference(
                first_data[..., 0],
                second_data[..., 0],
                valid,
            )
            statistics.add(values[valid])
            block = np.full(values.shape, -9999.0, dtype=np.float32)
            block[valid] = values[valid]
            destination.write(block, 1, window=window)
        destination.update_tags(
            AREA_OR_POINT="Area",
            analysis=analysis.upper(),
            formula=formula,
            input_band_numbers=",".join(f"{role}={index}" for role, index in mapping.items()),
            scale_offset_applied="true",
        )
    preview_path = result_directory / "preview.png"
    preview_width, preview_height, preview_bounds = _save_index_preview(
        raster_path,
        preview_path,
        analysis,
    )
    catalog = next(item for item in ANALYSIS_CATALOG if item["id"] == analysis)
    return {
        "analysis_type": analysis,
        "formula": formula,
        "bands": mapping,
        "band_scale_offsets": {
            str(index): {
                "scale": float(source.scales[index - 1]),
                "offset": float(source.offsets[index - 1]),
            }
            for index in mapping.values()
        },
        "statistics": statistics.to_dict(),
        "legend": catalog["legend"],
        "interpretation_note": catalog["interpretation_note"],
        "bounds": preview_bounds,
        "crs": source.crs.to_string(),
        "width": int(source.width),
        "height": int(source.height),
        "preview": {"artifact_name": "preview.png", "width": preview_width, "height": preview_height},
        "raster": {"artifact_name": raster_path.name, "nodata": -9999.0, "dtype": "float32"},
    }


def _reservoir_add(
    current: np.ndarray,
    priorities: np.ndarray,
    candidates: np.ndarray,
    rng: np.random.Generator,
) -> tuple[np.ndarray, np.ndarray]:
    if candidates.size == 0:
        return current, priorities
    candidate_priorities = rng.random(candidates.shape[0])
    values = np.concatenate((current, candidates), axis=0)
    weights = np.concatenate((priorities, candidate_priorities))
    if values.shape[0] > KMEANS_SAMPLE_SIZE:
        retained = np.argpartition(weights, KMEANS_SAMPLE_SIZE - 1)[:KMEANS_SAMPLE_SIZE]
        values = values[retained]
        weights = weights[retained]
    return values, weights


def _read_scene_sample(
    source: DatasetReader,
    indexes: list[int],
    random_seed: int,
    deadline_check: Callable[[], None],
) -> np.ndarray:
    rng = np.random.default_rng(random_seed)
    sample = np.empty((0, len(indexes)), dtype=np.float32)
    priorities = np.empty(0, dtype=np.float64)
    for window in _windows(source.width, source.height):
        deadline_check()
        features, valid = _read_features(source, indexes, window)
        sample, priorities = _reservoir_add(sample, priorities, features[valid], rng)
    if sample.shape[0] < 2:
        raise DatasetError("The selected bands contain fewer than two valid pixels.")
    return sample


def _run_kmeans(
    source: DatasetReader,
    result_directory: Path,
    indexes: list[int],
    cluster_count: int,
    random_seed: int,
    deadline_check: Callable[[], None],
) -> dict[str, Any]:
    sample = _read_scene_sample(source, indexes, random_seed, deadline_check)
    if np.unique(sample, axis=0).shape[0] < cluster_count:
        raise DatasetError(
            "The selected scene has fewer distinct spectral samples than the requested cluster count."
        )
    feature_mean = np.mean(sample, axis=0, dtype=np.float64).astype(np.float32)
    feature_std = np.std(sample, axis=0, dtype=np.float64).astype(np.float32)
    feature_std[~np.isfinite(feature_std) | (feature_std < 1e-8)] = 1.0
    standardized_sample = (sample - feature_mean) / feature_std
    model = MiniBatchKMeans(
        n_clusters=cluster_count,
        random_state=random_seed,
        batch_size=min(4096, max(256, standardized_sample.shape[0])),
        n_init=3,
        reassignment_ratio=0.0,
    )
    model.fit(standardized_sample)

    raster_path = result_directory / "classification.tif"
    class_counts = np.zeros(cluster_count, dtype=np.int64)
    output_profile = _output_profile(source, "uint8", 0)
    with rasterio.open(raster_path, "w", **output_profile) as destination:
        for window in _windows(source.width, source.height):
            deadline_check()
            features, valid = _read_features(source, indexes, window)
            block = np.zeros(valid.shape, dtype=np.uint8)
            if np.any(valid):
                standardized = (features[valid] - feature_mean) / feature_std
                labels = model.predict(standardized)
                block[valid] = labels.astype(np.uint8) + 1
                class_counts += np.bincount(labels, minlength=cluster_count)
            destination.write(block, 1, window=window)
        destination.update_tags(
            AREA_OR_POINT="Area",
            classification="unsupervised spectral clusters",
            cluster_count=str(cluster_count),
            random_seed=str(random_seed),
            feature_band_numbers=",".join(str(index) for index in indexes),
            feature_standardization="sample mean and standard deviation",
            semantic_labels="none",
        )

    with rasterio.open(raster_path) as classification:
        transform, preview_width, preview_height, preview_bounds = _preview_grid(classification)
        with WarpedVRT(
            classification,
            crs="EPSG:4326",
            transform=transform,
            width=preview_width,
            height=preview_height,
            resampling=Resampling.nearest,
            nodata=0,
        ) as warped:
            sampled = warped.read(1, masked=True)
            labels = np.asarray(sampled.data, dtype=np.uint8)
            valid_preview = ~np.ma.getmaskarray(sampled) & (labels > 0)
    rng = np.random.default_rng(random_seed)
    colors = rng.integers(48, 224, size=(cluster_count, 3), dtype=np.uint8)
    rgb = np.zeros((preview_height, preview_width, 4), dtype=np.uint8)
    rgb[valid_preview, :3] = colors[labels[valid_preview] - 1]
    rgb[..., 3] = np.where(valid_preview, 255, 0).astype(np.uint8)
    preview_path = result_directory / "preview.png"
    Image.fromarray(rgb, mode="RGBA").save(preview_path, format="PNG", optimize=True)

    valid_count = int(np.sum(class_counts))
    return {
        "analysis_type": "kmeans",
        "method": "scikit-learn MiniBatchKMeans; deterministic random seed",
        "features": [
            {
                "band": index,
                "scale": float(source.scales[index - 1]),
                "offset": float(source.offsets[index - 1]),
            }
            for index in indexes
        ],
        "feature_standardization": "Sample mean and standard deviation; deterministic reservoir sample.",
        "sample_pixels": int(sample.shape[0]),
        "cluster_count": cluster_count,
        "random_seed": random_seed,
        "valid_pixels": valid_count,
        "classes": [
            {
                "value": class_id,
                "label": f"Class {class_id}",
                "pixel_count": int(count),
                "proportion": float(count / valid_count) if valid_count else None,
            }
            for class_id, count in enumerate(class_counts, start=1)
        ],
        "nodata_value": 0,
        "interpretation_note": next(
            item["interpretation_note"]
            for item in ANALYSIS_CATALOG
            if item["id"] == "kmeans"
        ),
        "bounds": preview_bounds,
        "crs": source.crs.to_string(),
        "width": int(source.width),
        "height": int(source.height),
        "preview": {
            "artifact_name": "preview.png",
            "width": preview_width,
            "height": preview_height,
            "legend": [
                {
                    "value": class_id,
                    "label": f"Class {class_id}",
                    "color": "#{:02x}{:02x}{:02x}".format(*color),
                }
                for class_id, color in enumerate(colors, start=1)
            ],
        },
        "raster": {"artifact_name": raster_path.name, "nodata": 0, "dtype": "uint8"},
    }


def run_satellite_analysis(
    source_path: Path,
    result_directory: Path,
    *,
    analysis: AnalysisType,
    scene_metadata: dict[str, Any],
    band_mapping: dict[str, int] | None = None,
    feature_bands: list[int] | None = None,
    cluster_count: int = 3,
    random_seed: int = 42,
    max_pixels: int,
    max_output_bytes: int = 512 * 1024 * 1024,
    max_bands: int = MAX_KMEANS_FEATURES,
    deadline_check: Callable[[], None] = lambda: None,
) -> dict[str, Any]:
    try:
        with rasterio.open(source_path) as source:
            if source.driver != "GTiff":
                raise DatasetError("Satellite analysis currently supports GeoTIFF scenes only.")
            if source.crs is None:
                raise DatasetError("The imagery scene has no coordinate reference system.")
            if source.width < 1 or source.height < 1 or source.count < 1:
                raise DatasetError("The imagery scene contains no raster pixels or bands.")
            if source.width * source.height > max_pixels:
                raise DatasetError(
                    f"The scene exceeds the configured {max_pixels:,}-pixel analysis limit."
                )
            if source.count > 64:
                raise DatasetError("Scenes with more than 64 source bands are not supported.")
            output_bytes_per_pixel = 4 if analysis in {"ndvi", "ndwi"} else 1
            size_headroom = min(1024 * 1024, max(4096, max_output_bytes // 100))
            if source.width * source.height * output_bytes_per_pixel + size_headroom > max_output_bytes:
                raise DatasetError(
                    "The output raster would exceed the configured artifact size limit."
                )
            result_directory.mkdir(parents=True, exist_ok=True)
            if analysis == "ndvi" or analysis == "ndwi":
                mapping = validate_band_mapping(
                    source,
                    analysis,
                    scene_metadata,
                    band_mapping,
                )
                result = _run_index(
                    source,
                    result_directory,
                    analysis,
                    mapping,
                    deadline_check,
                )
            else:
                if band_mapping is not None:
                    raise DatasetError(
                        "Band-role mappings apply only to NDVI or McFeeters NDWI."
                    )
                if not 2 <= cluster_count <= 10:
                    raise DatasetError("Cluster count must be between 2 and 10.")
                if not 0 <= random_seed <= 2**32 - 1:
                    raise DatasetError("Random seed must be between 0 and 4294967295.")
                indexes = (
                    feature_bands
                    if feature_bands is not None
                    else list(range(1, source.count + 1))
                )
                if len(indexes) != len(set(indexes)):
                    raise DatasetError("Feature band selections must not contain duplicates.")
                if len(indexes) < 2:
                    raise DatasetError("K-means requires at least two selected spectral bands.")
                if len(indexes) > max_bands:
                    raise DatasetError(
                        f"K-means supports at most {max_bands} selected feature bands."
                    )
                if any(not isinstance(index, int) or not 1 <= index <= source.count for index in indexes):
                    raise DatasetError(
                        f"Feature band numbers must be between 1 and {source.count}."
                    )
                result = _run_kmeans(
                    source,
                    result_directory,
                    indexes,
                    cluster_count,
                    random_seed,
                    deadline_check,
                )
        return result
    except DatasetError:
        raise
    except MemoryError as exc:
        raise DatasetError("Satellite analysis exceeded the available memory.") from exc
    except (RasterioError, OSError, ValueError, OverflowError) as exc:
        raise DatasetError(
            "The imagery scene could not be processed as a valid GeoTIFF."
        ) from exc

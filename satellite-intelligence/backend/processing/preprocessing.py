from dataclasses import dataclass, field
from pathlib import Path
import re
from typing import Callable, Iterable

import numpy as np
import rasterio
from rasterio._err import CPLE_BaseError
from rasterio.crs import CRS
from rasterio.errors import CRSError, RasterioError
from rasterio.transform import array_bounds
from rasterio.warp import transform_bounds


BAND_NAMES = {
    "B02": "Blue",
    "B03": "Green",
    "B04": "Red",
    "B08": "Near infrared",
    "B11": "Short-wave infrared",
}


class DatasetError(ValueError):
    """An understandable error describing invalid or unusable raster data."""


@dataclass
class RasterBand:
    code: str
    data: np.ndarray
    valid: np.ndarray
    profile: dict
    crs: str | None
    transform: rasterio.Affine
    width: int
    height: int
    resolution: tuple[float, float]
    tags: dict[str, str] = field(default_factory=dict)


@dataclass
class SatelliteDataset:
    directory: Path
    bands: dict[str, RasterBand]
    missing_bands: list[str]
    errors: list[str] = field(default_factory=list)

    def require(self, required: Iterable[str]) -> dict[str, RasterBand]:
        missing = [band for band in required if band not in self.bands]
        if missing:
            names = ", ".join(f"{band} ({BAND_NAMES.get(band, band)})" for band in missing)
            detail = f"Required Sentinel-2 band(s) missing or unreadable: {names}."
            if self.errors:
                detail += f" File issue: {self.errors[0]}"
            raise DatasetError(detail)
        return {band: self.bands[band] for band in required}


def identify_band(filename: str) -> str | None:
    match = re.search(r"(?<![A-Z0-9])(B(?:02|03|04|08|11))(?![0-9])", filename.upper())
    return match.group(1) if match else None


def _read_raster(
    path: Path,
    band: str,
    max_pixels: int | None = None,
    max_file_bytes: int | None = None,
    max_input_array_bytes: int | None = None,
    deadline_check: Callable[[], None] | None = None,
) -> RasterBand:
    try:
        if deadline_check:
            deadline_check()
        if max_file_bytes is not None and path.stat().st_size > max_file_bytes:
            raise DatasetError(
                f"{path.name} exceeds the configured raster file limit of "
                f"{max_file_bytes} bytes."
            )
        with rasterio.open(path) as src:
            if src.count < 1 or src.width < 1 or src.height < 1:
                raise DatasetError(f"{path.name} contains no readable raster pixels.")
            pixel_count = src.width * src.height
            if max_pixels is not None and pixel_count > max_pixels:
                raise DatasetError(
                    f"{path.name} has {pixel_count} pixels; the configured analysis limit is "
                    f"{max_pixels} pixels."
                )
            retained_array_bytes = pixel_count * (
                np.dtype(np.float32).itemsize + np.dtype(bool).itemsize
            )
            if (
                max_input_array_bytes is not None
                and retained_array_bytes > max_input_array_bytes
            ):
                raise DatasetError(
                    f"{path.name} requires {retained_array_bytes} bytes for its retained "
                    f"input arrays; the remaining configured budget is "
                    f"{max_input_array_bytes} bytes."
                )
            values = src.read(1, masked=True).astype(np.float32)
            data = np.asarray(values.filled(np.nan), dtype=np.float32)
            valid = ~np.ma.getmaskarray(values) & np.isfinite(data)
            if deadline_check:
                deadline_check()
            if not np.any(valid):
                raise DatasetError(f"{path.name} contains no valid pixels.")
            return RasterBand(
                code=band,
                data=data,
                valid=valid,
                profile=src.profile.copy(),
                crs=src.crs.to_string() if src.crs else None,
                transform=src.transform,
                width=src.width,
                height=src.height,
                resolution=src.res,
                tags=src.tags(),
            )
    except DatasetError:
        raise
    except MemoryError as exc:
        raise DatasetError(f"{path.name} is too large to load into available memory.") from exc
    except (RasterioError, OSError, ValueError) as exc:
        raise DatasetError(f"Could not read GeoTIFF {path.name}: {exc}") from exc


def load_dataset(
    directory: Path,
    required: Iterable[str] = (),
    *,
    include_other_bands: bool = True,
    max_pixels: int | None = None,
    max_file_bytes: int | None = None,
    max_input_array_bytes: int | None = None,
    deadline_check: Callable[[], None] | None = None,
) -> SatelliteDataset:
    if not directory.exists() or not directory.is_dir():
        raise DatasetError(f"Dataset directory is unavailable: {directory.name}.")
    candidates: dict[str, Path] = {}
    required_order = tuple(required)
    required_codes = set(required_order)
    if max_file_bytes is not None and max_file_bytes <= 0:
        raise DatasetError("Raster file-size limit must be positive.")
    if max_input_array_bytes is not None and max_input_array_bytes <= 0:
        raise DatasetError("Input array memory budget must be positive.")
    for path in sorted(directory.iterdir()):
        if path.is_file() and path.suffix.lower() in {".tif", ".tiff"}:
            band = identify_band(path.name)
            if (
                band
                and (include_other_bands or not required_codes or band in required_codes)
                and band not in candidates
            ):
                candidates[band] = path
    loaded = {}
    errors = []
    remaining_array_bytes = max_input_array_bytes
    for code, path in candidates.items():
        try:
            if deadline_check:
                deadline_check()
            band = _read_raster(
                path,
                code,
                max_pixels=max_pixels,
                max_file_bytes=max_file_bytes,
                max_input_array_bytes=remaining_array_bytes,
                deadline_check=deadline_check,
            )
            loaded[code] = band
            if remaining_array_bytes is not None:
                remaining_array_bytes -= band.data.nbytes + band.valid.nbytes
        except DatasetError as exc:
            errors.append(str(exc))
    missing = [band for band in required_order if band not in loaded]
    return SatelliteDataset(
        directory=directory,
        bands=loaded,
        missing_bands=missing,
        errors=errors,
    )


def validate_alignment(bands: Iterable[RasterBand]) -> None:
    bands = list(bands)
    if not bands:
        raise DatasetError("No satellite bands were selected.")
    reference = bands[0]
    if not reference.crs:
        raise DatasetError(
            f"Band {reference.code} has no CRS; geospatial alignment cannot be verified."
        )
    for band in bands[1:]:
        if (band.width, band.height) != (reference.width, reference.height):
            raise DatasetError(
                f"Band {band.code} has different dimensions; all required bands must be aligned."
            )
        if band.crs != reference.crs:
            raise DatasetError(
                f"Band {band.code} uses a different CRS; reproject and align bands before analysis."
            )
        if not np.allclose(tuple(band.transform), tuple(reference.transform), rtol=0, atol=1e-9):
            raise DatasetError(
                f"Band {band.code} has a different geotransform; align bands before analysis."
            )


def common_valid_mask(bands: Iterable[RasterBand]) -> np.ndarray:
    bands = list(bands)
    validate_alignment(bands)
    mask = np.ones((bands[0].height, bands[0].width), dtype=bool)
    for band in bands:
        mask &= band.valid
    return mask


def safe_normalized_difference(
    first: np.ndarray, second: np.ndarray, valid_mask: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    denominator = first + second
    valid = valid_mask & np.isfinite(denominator) & (np.abs(denominator) > 1e-12)
    result = np.full(first.shape, np.nan, dtype=np.float32)
    np.divide(first - second, denominator, out=result, where=valid)
    valid &= np.isfinite(result)
    result[~valid] = np.nan
    return result, valid


def raster_statistics(array: np.ndarray, valid: np.ndarray) -> dict[str, float | int | None]:
    values = array[valid & np.isfinite(array)]
    if values.size == 0:
        return {"min": None, "max": None, "mean": None, "median": None, "valid_pixels": 0}
    return {
        "min": float(np.min(values)),
        "max": float(np.max(values)),
        "mean": float(np.mean(values)),
        "median": float(np.median(values)),
        "valid_pixels": int(values.size),
    }


def pixel_area_square_metres(band: RasterBand) -> float | None:
    if not band.crs:
        return None
    try:
        crs = CRS.from_string(band.crs)
    except CRSError:
        return None
    if not crs.is_projected:
        return None
    unit_details = crs.linear_units_factor
    if not unit_details or len(unit_details) != 2:
        return None
    _, factor = unit_details
    return abs(band.transform.a * band.transform.e - band.transform.b * band.transform.d) * factor**2


def raster_metadata(band: RasterBand | None) -> dict | None:
    if band is None:
        return None
    bounds = array_bounds(band.height, band.width, band.transform)
    if not band.crs:
        bounds = None
    elif band.crs != "EPSG:4326":
        try:
            source_crs = CRS.from_string(band.crs)
            if source_crs.is_geographic or source_crs.is_projected:
                bounds = transform_bounds(
                    band.crs, "EPSG:4326", *bounds, densify_pts=21
                )
            else:
                bounds = None
        except (CPLE_BaseError, RasterioError, ValueError):
            bounds = None
    geographic_bounds = None
    if bounds is not None:
        if np.all(np.isfinite(bounds)):
            west, south, east, north = bounds
            geographic_bounds = {
                "west": float(west),
                "south": float(south),
                "east": float(east),
                "north": float(north),
            }
    return {
        "crs": band.crs,
        "bounds": geographic_bounds,
        "width": int(band.width),
        "height": int(band.height),
        "resolution": [float(value) for value in band.resolution],
        "nodata": band.profile.get("nodata"),
        "transform": list(band.transform),
    }


def build_profile(reference: RasterBand, dtype: str = "float32", nodata: float = -9999) -> dict:
    profile = reference.profile.copy()
    profile.update(driver="GTiff", count=1, dtype=dtype, compress="deflate")
    profile["nodata"] = nodata
    return profile

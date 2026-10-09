from dataclasses import dataclass, field
from pathlib import Path
import re
from typing import Iterable

import numpy as np
import rasterio


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


def _read_raster(path: Path, band: str) -> RasterBand:
    try:
        with rasterio.open(path) as src:
            if src.count < 1 or src.width < 1 or src.height < 1:
                raise DatasetError(f"{path.name} contains no readable raster pixels.")
            values = src.read(1, masked=True).astype(np.float32)
            data = np.asarray(values.filled(np.nan), dtype=np.float32)
            valid = ~np.ma.getmaskarray(values) & np.isfinite(data)
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
            )
    except DatasetError:
        raise
    except MemoryError as exc:
        raise DatasetError(f"{path.name} is too large to load into available memory.") from exc
    except (rasterio.errors.RasterioError, OSError, ValueError) as exc:
        raise DatasetError(f"Could not read GeoTIFF {path.name}: {exc}") from exc


def load_dataset(directory: Path, required: Iterable[str] = ()) -> SatelliteDataset:
    if not directory.exists() or not directory.is_dir():
        raise DatasetError(f"Dataset directory is unavailable: {directory.name}.")
    candidates: dict[str, Path] = {}
    for path in sorted(directory.iterdir()):
        if path.is_file() and path.suffix.lower() in {".tif", ".tiff"}:
            band = identify_band(path.name)
            if band and band not in candidates:
                candidates[band] = path
    loaded = {}
    errors = []
    for code, path in candidates.items():
        try:
            loaded[code] = _read_raster(path, code)
        except DatasetError as exc:
            errors.append(str(exc))
    missing = [band for band in required if band not in loaded]
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
    crs = rasterio.crs.CRS.from_string(band.crs)
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
    bounds = rasterio.transform.array_bounds(band.height, band.width, band.transform)
    if band.crs and band.crs != "EPSG:4326":
        try:
            bounds = rasterio.warp.transform_bounds(band.crs, "EPSG:4326", *bounds, densify_pts=21)
        except Exception:
            pass
    west, south, east, north = bounds
    return {
        "crs": band.crs,
        "bounds": {"west": float(west), "south": float(south), "east": float(east), "north": float(north)},
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

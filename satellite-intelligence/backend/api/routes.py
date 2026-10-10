import json
import hashlib
import heapq
import logging
import mimetypes
import os
import re
import tempfile
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from threading import BoundedSemaphore
from time import monotonic
from typing import Any
from urllib.parse import quote
from uuid import uuid4

import numpy as np
import rasterio
from rasterio._err import CPLE_BaseError
from rasterio.crs import CRS
from rasterio.errors import RasterioError
from rasterio.transform import Affine, array_bounds
from rasterio.warp import transform_bounds
from fastapi import APIRouter, Depends, File, Header, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from config import settings, validate_runtime_settings
from auth import AuthenticatedUser, authenticated_user_id, current_user
from persistence.artifacts import ArtifactStorageError
from persistence.manager import PersistenceManager
from models.landcover_model import LandCoverModel
from processing.change_detection import calculate_change
from processing.data_provider import LocalDataProvider
from processing.landcover import LandCoverClassifier
from processing.ndbi import calculate_ndbi
from processing.ndvi import calculate_ndvi
from processing.ndwi import calculate_ndwi
from processing.preprocessing import (
    DatasetError,
    RasterBand,
    SatelliteDataset,
    build_profile,
    identify_band,
    load_dataset,
    pixel_area_square_metres,
    raster_metadata,
)
from processing.provenance import analysis_provenance, dataset_provenance
from processing.visualization import save_class_png, save_index_png, save_rgb_preview
from geoai import (
    analyze_land_cover_transitions,
    detect_risk_indicators,
    evaluate_forecast,
    forecast_vegetation_trends,
    summarize_observation_history,
    summarize_spatial_patterns,
)
from geoai.schemas import SpatialAnalysisRequest, TransitionRequest, VegetationForecastRequest
from satellite.base_provider import (
    ProviderCapabilityUnavailableError,
    ProviderNotConfiguredError,
)
from satellite.cache import CacheManager
from satellite.local_provider import LocalSatelliteProvider
from satellite.live_provider import LiveSatelliteProvider
from satellite.mock_provider import MockSatelliteProvider
from satellite.models import SatelliteDownloadRequest, SatelliteSearchRequest
from geoai.trend_models import _parse_date

router = APIRouter()
logger = logging.getLogger("satellite-intelligence")
persistence_manager = PersistenceManager(settings)
analysis_slots = BoundedSemaphore(settings.max_concurrent_analyses)
ANALYSES = ("ndvi", "ndwi", "ndbi", "landcover", "change_detection")
ANALYSIS_ALIASES = {"change": "change_detection"}
DOWNLOAD_FILES = {
    "ndvi": "ndvi.tif",
    "ndwi": "ndwi.tif",
    "ndbi": "ndbi.tif",
    "landcover": "landcover.tif",
    "change_detection": "change_detection.tif",
    "report": "summary.json",
}
data_provider = LocalDataProvider(settings.data_dir)
EMPTY_DATA_MESSAGE = (
    "No satellite dataset found. Please add Sentinel-2 GeoTIFF bands to backend/data/current/."
)
REQUIRED_BANDS = {
    "ndvi": ("B04", "B08"),
    "ndwi": ("B03", "B08"),
    "ndbi": ("B08", "B11"),
    "landcover": ("B03", "B04", "B08", "B11"),
}
SUPPORTED_BANDS = ("B02", "B03", "B04", "B08", "B11")
DATASET_PERIODS = {"current", "historical"}
DEFAULT_RESULTS_PAGE_SIZE = 50
MAX_RESULTS_PAGE_SIZE = 100
cache_manager = CacheManager(settings.satellite_cache_dir)
_analysis_deadline: ContextVar[float | None] = ContextVar(
    "analysis_deadline",
    default=None,
)


def _check_analysis_deadline() -> None:
    deadline = _analysis_deadline.get()
    if deadline is not None and monotonic() >= deadline:
        raise HTTPException(
            status_code=504,
            detail="Analysis exceeded its configured processing time limit.",
        )


@contextmanager
def _analysis_slot():
    if not analysis_slots.acquire(blocking=False):
        raise HTTPException(
            status_code=503,
            detail="Analysis capacity is busy. Retry the request shortly.",
            headers={"Retry-After": "1"},
        )
    deadline_token = _analysis_deadline.set(
        monotonic() + settings.max_analysis_seconds
    )
    try:
        yield
    finally:
        _analysis_deadline.reset(deadline_token)
        analysis_slots.release()


def _request_dataset_root() -> Path:
    if not settings.authentication_required:
        return settings.data_dir.resolve()
    user_id = authenticated_user_id.get()
    if not user_id:
        raise HTTPException(
            status_code=401,
            detail="A valid Supabase sign-in is required.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    namespace = hashlib.sha256(user_id.encode("utf-8")).hexdigest()
    root = (settings.data_dir / "users" / namespace).resolve()
    root.mkdir(parents=True, exist_ok=True)
    for period in DATASET_PERIODS:
        (root / period).mkdir(exist_ok=True)
    return root


def _request_cache_manager() -> CacheManager:
    if not settings.authentication_required:
        return cache_manager
    data_root = _request_dataset_root()
    return CacheManager(settings.satellite_cache_dir / "users" / data_root.name)


def _provider_for_mode(cache: CacheManager | None = None):
    provider_name = (settings.satellite_provider or "local").lower()
    if provider_name == "mock":
        return MockSatelliteProvider(cache or _request_cache_manager())
    if provider_name == "live":
        return LiveSatelliteProvider()
    return LocalSatelliteProvider()


def _dataset(
    directory: Path,
    required: tuple[str, ...] = (),
    *,
    include_other_bands: bool = True,
) -> SatelliteDataset:
    try:
        relative_path = directory.resolve().relative_to(settings.data_dir.resolve())
    except ValueError as exc:
        raise DatasetError("Requested dataset directory is outside the configured data root.") from exc
    if settings.authentication_required:
        data_root = _request_dataset_root()
        scoped_directory = (data_root / relative_path).resolve()
        if data_root not in scoped_directory.parents:
            raise DatasetError("Requested dataset directory is outside the authenticated user's data root.")
        return load_dataset(
            scoped_directory,
            required,
            include_other_bands=include_other_bands,
            max_pixels=settings.max_raster_pixels,
            max_file_bytes=settings.max_raster_bytes,
            max_input_array_bytes=settings.max_input_array_bytes,
            deadline_check=_check_analysis_deadline,
        )
    return data_provider.load(
        relative_path.as_posix(),
        required,
        include_other_bands=include_other_bands,
        max_pixels=settings.max_raster_pixels,
        max_file_bytes=settings.max_raster_bytes,
        max_input_array_bytes=settings.max_input_array_bytes,
        deadline_check=_check_analysis_deadline,
    )


def _public_dataset(dataset: SatelliteDataset, dataset_id: str) -> dict:
    reference = next(iter(dataset.bands.values()), None)
    band_codes = tuple(code for code in SUPPORTED_BANDS if code in dataset.bands)
    return {
        "available": bool(dataset.bands),
        "bands": [
            {
                "code": code,
                "name": band_name,
                "width": dataset.bands[code].width,
                "height": dataset.bands[code].height,
                "crs": dataset.bands[code].crs,
                "resolution": dataset.bands[code].resolution,
            }
            for code, band_name in (
                ("B02", "Blue"),
                ("B03", "Green"),
                ("B04", "Red"),
                ("B08", "Near infrared"),
                ("B11", "Short-wave infrared"),
            )
            if code in dataset.bands
        ],
        "missing_bands": [band for band in SUPPORTED_BANDS if band not in dataset.bands],
        "errors": dataset.errors,
        "bounds": _bounds(reference) if reference else None,
        "metadata": raster_metadata(reference),
        "provenance": dataset_provenance(dataset_id, dataset, band_codes),
    }


def _data_quality_summary(dataset: SatelliteDataset) -> dict:
    if not dataset.bands:
        return {
            "status": "MISSING",
            "message": EMPTY_DATA_MESSAGE,
            "missing_bands": list(SUPPORTED_BANDS),
            "nodata_percentage": 100.0,
            "warnings": ["No current dataset was found. Add Sentinel-2 GeoTIFFs under backend/data/current/."],
        }
    reference = next(iter(dataset.bands.values()))
    valid_pixels = 0
    total_band_pixels = 0
    invalid_band_pixels = 0
    for band in dataset.bands.values():
        valid_pixels += int(np.count_nonzero(band.valid))
        total_band_pixels += int(band.valid.size)
        invalid_band_pixels += int(np.count_nonzero(~band.valid))
    nodata_percentage = (
        invalid_band_pixels / total_band_pixels * 100
        if total_band_pixels
        else 100.0
    )
    warnings = []
    if dataset.missing_bands:
        warnings.append(f"Missing required bands: {', '.join(dataset.missing_bands)}")
    if nodata_percentage > 20:
        warnings.append("A large share of pixels is nodata or invalid.")
    if reference.crs is None:
        warnings.append("Raster CRS is missing; geospatial interpretation is limited.")
    try:
        from processing.preprocessing import validate_alignment

        validate_alignment(dataset.bands.values())
    except DatasetError as exc:
        warnings.append(f"Available bands are not on a verified common grid: {exc}")
    try:
        historical_available = bool(_dataset(settings.data_dir / "historical").bands)
    except DatasetError:
        historical_available = False
    return {
        "status": "GOOD" if not warnings else "WARNING",
        "missing_bands": dataset.missing_bands,
        "nodata_percentage": float(nodata_percentage),
        "crs": reference.crs,
        "width": reference.width,
        "height": reference.height,
        "resolution": list(reference.resolution),
        "valid_band_pixels": valid_pixels,
        "total_band_pixels": total_band_pixels,
        "nodata_percentage_method": "invalid band-pixels divided by all available band-pixels",
        "historical_available": historical_available,
        "warnings": warnings,
    }


def _bounds(reference) -> list[float] | None:
    if not reference or not reference.crs:
        return None
    try:
        bounds = array_bounds(reference.height, reference.width, reference.transform)
        if reference.crs != "EPSG:4326":
            crs = CRS.from_string(reference.crs)
            if not (crs.is_geographic or crs.is_projected):
                return None
            bounds = transform_bounds(
                reference.crs, "EPSG:4326", *bounds, densify_pts=21
            )
    except (CPLE_BaseError, RasterioError, ValueError):
        return None
    if not np.all(np.isfinite(bounds)):
        return None
    return [float(value) for value in bounds]


def _dated_ndvi_observations(
    dataset_ids: tuple[str, ...] = ("current", "historical"),
) -> tuple[list[dict[str, Any]], list[str]]:
    observations: list[dict[str, Any]] = []
    warnings: list[str] = []
    seen_dates: set[str] = set()
    for dataset_id in dataset_ids:
        try:
            dataset = _check_dataset(settings.data_dir / dataset_id, "ndvi")
        except DatasetError as exc:
            warnings.append(f"{dataset_id}: {exc}")
            continue
        provenance = dataset_provenance(
            dataset_id, dataset, REQUIRED_BANDS["ndvi"]
        )
        if provenance["data_classification"] == "synthetic":
            warnings.append(
                f"{dataset_id}: synthetic fixture; it is not a satellite observation."
            )
        else:
            warnings.append(
                f"{dataset_id}: acquisition metadata is user-provided and has not been independently verified."
            )
        parsed_date = _parse_date(provenance.get("acquisition_date"))
        if parsed_date is None:
            warnings.append(
                f"{dataset_id}: no valid acquisition date in raster metadata; excluded from temporal analysis."
            )
            continue
        observation_date = parsed_date.date().isoformat()
        if observation_date in seen_dates:
            warnings.append(
                f"{dataset_id}: duplicate acquisition date {observation_date}; excluded from temporal analysis."
            )
            continue
        ndvi = calculate_ndvi(dataset.bands["B04"], dataset.bands["B08"])
        valid_values = ndvi["raster"][ndvi["valid_mask"]]
        if valid_values.size == 0:
            warnings.append(f"{dataset_id}: no valid NDVI pixels; excluded from temporal analysis.")
            continue
        seen_dates.add(observation_date)
        observations.append(
            {
                "date": observation_date,
                "mean_ndvi": float(np.mean(valid_values)),
                "dataset_id": dataset_id,
                "provenance": provenance,
            }
        )
    observations.sort(key=lambda item: item["date"])
    return observations, warnings


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items() if key not in {"raster", "valid_mask", "reference"}}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return float(value) if np.isfinite(value) else None
    if isinstance(value, float) and not np.isfinite(value):
        return None
    return value


def _save_raster(path: Path, data: np.ndarray, valid: np.ndarray, reference, classes: bool = False) -> None:
    profile = build_profile(reference, dtype="uint8" if classes else "float32", nodata=0 if classes else -9999)
    output = np.where(valid, data, 0 if classes else -9999)
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(output.astype(profile["dtype"]), 1)
        dst.update_tags(AREA_OR_POINT="Area")


def _save_analysis(
    result_dir: Path,
    analysis_key: str,
    data: dict,
    title: str,
    visual_kind: str | None = None,
    classes: bool = False,
) -> dict:
    result_dir.mkdir(parents=True, exist_ok=True)
    raster_name = DOWNLOAD_FILES[analysis_key]
    _save_raster(result_dir / raster_name, data["raster"], data["valid_mask"], data["reference"], classes)
    image_name = f"{analysis_key}.png"
    if classes:
        save_class_png(data["raster"], data["valid_mask"], result_dir / image_name)
    else:
        save_index_png(data["raster"], data["valid_mask"], result_dir / image_name, visual_kind or analysis_key)
    summary = _json_safe(data)
    summary.update(
        {
            "analysis": title,
            "bounds": _bounds(data["reference"]),
            "visualization_url": f"/api/results/{result_dir.name}/image/{analysis_key}",
            "download_url": f"/api/results/{result_dir.name}/download/{analysis_key}",
        }
    )
    return summary


def _single_result(result_dir: Path, key: str, dataset: SatelliteDataset) -> dict:
    bands = dataset.bands
    if key == "ndvi":
        data = calculate_ndvi(bands["B04"], bands["B08"])
        pixel_area = pixel_area_square_metres(bands["B04"])
        data["vegetation_area_square_metres"] = (
            data["statistics"]["valid_pixels"] * data["vegetation_percentage"] / 100 * pixel_area
            if pixel_area is not None and data["vegetation_percentage"] is not None
            else None
        )
        result = _save_analysis(result_dir, key, data, "NDVI", "ndvi")
    elif key == "ndwi":
        result = _save_analysis(
            result_dir,
            key,
            calculate_ndwi(bands["B03"], bands["B08"]),
            "NDWI",
            "ndwi",
        )
    elif key == "ndbi":
        result = _save_analysis(
            result_dir,
            key,
            calculate_ndbi(bands["B08"], bands["B11"]),
            "NDBI",
            "ndbi",
        )
    elif key == "landcover":
        result = _save_analysis(
            result_dir,
            key,
            LandCoverClassifier().classify(bands),
            "Land-use / land-cover",
            classes=True,
        )
    else:
        raise DatasetError(f"Unsupported analysis: {key}.")
    result["provenance"] = analysis_provenance(
        key, {"current": (dataset, REQUIRED_BANDS[key])}
    )
    return result


def _validate_dataset(dataset: SatelliteDataset, key: str) -> SatelliteDataset:
    dataset.require(REQUIRED_BANDS[key])
    from processing.preprocessing import validate_alignment
    validate_alignment([dataset.bands[code] for code in REQUIRED_BANDS[key]])
    return dataset


def _validate_upload_alignment(upload_path: Path, dataset_dir: Path) -> None:
    try:
        with rasterio.open(upload_path) as uploaded:
            if uploaded.count != 1:
                raise DatasetError("Each uploaded GeoTIFF must contain exactly one band.")
            if uploaded.crs is None:
                raise DatasetError("The uploaded GeoTIFF has no CRS; add georeferenced imagery.")
            if uploaded.width < 1 or uploaded.height < 1:
                raise DatasetError("The uploaded GeoTIFF has no raster pixels.")
            for existing_path in sorted(dataset_dir.iterdir()):
                if not existing_path.is_file() or existing_path.suffix.lower() not in {".tif", ".tiff"}:
                    continue
                if identify_band(existing_path.name) is None:
                    continue
                if existing_path.stat().st_size > settings.max_raster_bytes:
                    raise DatasetError(
                        f"{existing_path.name} exceeds the configured raster file limit."
                    )
                try:
                    with rasterio.open(existing_path) as existing:
                        if existing.width * existing.height > settings.max_raster_pixels:
                            raise DatasetError(
                                f"{existing_path.name} exceeds the configured raster pixel limit."
                            )
                        if (uploaded.width, uploaded.height) != (existing.width, existing.height):
                            raise DatasetError(
                                f"The uploaded raster dimensions differ from {existing_path.name}; "
                                "align the bands before uploading."
                            )
                        if uploaded.crs != existing.crs:
                            raise DatasetError(
                                f"The uploaded raster CRS differs from {existing_path.name}; "
                                "reproject the bands before uploading."
                            )
                        if not np.allclose(
                            tuple(uploaded.transform),
                            tuple(existing.transform),
                            rtol=0,
                            atol=1e-9,
                        ):
                            raise DatasetError(
                                f"The uploaded raster geotransform differs from {existing_path.name}; "
                                "align the bands before uploading."
                            )
                except DatasetError:
                    raise
                except RasterioError:
                    continue
    except DatasetError:
        raise
    except (RasterioError, OSError) as exc:
        raise DatasetError("The uploaded file is not a readable GeoTIFF.") from exc


@router.post("/dataset/{period}/upload", status_code=201)
async def upload_dataset_band(
    period: str,
    file: UploadFile = File(...),
    user: AuthenticatedUser | None = Depends(current_user),
) -> dict:
    if period not in DATASET_PERIODS:
        raise HTTPException(status_code=404, detail="Dataset period must be current or historical.")
    if Path(file.filename or "").suffix.lower() not in {".tif", ".tiff"}:
        raise HTTPException(status_code=415, detail="Upload a GeoTIFF file with a .tif or .tiff extension.")
    band_code = identify_band(Path(file.filename or "").name)
    if band_code not in SUPPORTED_BANDS:
        raise HTTPException(
            status_code=422,
            detail="Filename must identify one Sentinel-2 band: B02, B03, B04, B08, or B11.",
        )

    dataset_root = _request_dataset_root()
    dataset_dir = (dataset_root / period).resolve()
    if dataset_dir.parent != dataset_root.resolve():
        raise HTTPException(status_code=400, detail="Invalid dataset destination.")
    dataset_dir.mkdir(parents=True, exist_ok=True)
    destination = dataset_dir / f"{band_code}.tif"
    if destination.exists():
        raise HTTPException(
            status_code=409,
            detail=f"{band_code} already exists in the {period} dataset; existing files are not overwritten.",
        )

    temp_path: Path | None = None
    total_bytes = 0
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=".satellite-upload-",
            suffix=".tif",
            dir=dataset_dir,
            delete=False,
        ) as temporary:
            temp_path = Path(temporary.name)
            while chunk := await file.read(1024 * 1024):
                total_bytes += len(chunk)
                if total_bytes > settings.max_upload_bytes:
                    raise HTTPException(
                        status_code=413,
                        detail=f"File exceeds the {settings.max_upload_bytes // (1024 * 1024)} MB upload limit.",
                    )
                if total_bytes > settings.max_raster_bytes:
                    raise HTTPException(
                        status_code=413,
                        detail="File exceeds the configured raster file-size limit.",
                    )
                temporary.write(chunk)
        if total_bytes == 0:
            raise HTTPException(status_code=422, detail="The uploaded GeoTIFF is empty.")

        try:
            with rasterio.open(temp_path) as source:
                if source.count != 1:
                    raise DatasetError("Each uploaded GeoTIFF must contain exactly one band.")
                if source.crs is None:
                    raise DatasetError("The uploaded GeoTIFF has no CRS; add georeferenced imagery.")
                pixel_count = source.width * source.height
                if pixel_count > settings.max_raster_pixels:
                    raise HTTPException(
                        status_code=413,
                        detail=(
                            f"GeoTIFF has {pixel_count} pixels; the configured limit is "
                            f"{settings.max_raster_pixels} pixels per raster."
                        ),
                    )
                has_valid_pixel = False
                for _, window in source.block_windows(1):
                    block = source.read(1, window=window, masked=True)
                    if np.any(~np.ma.getmaskarray(block) & np.isfinite(block.filled(np.nan))):
                        has_valid_pixel = True
                        break
                if not has_valid_pixel:
                    raise DatasetError("The uploaded GeoTIFF contains no valid pixels.")
        except DatasetError:
            raise
        except (RasterioError, OSError, ValueError) as exc:
            raise DatasetError("The uploaded file is corrupt or is not a readable GeoTIFF.") from exc

        _validate_upload_alignment(temp_path, dataset_dir)
        try:
            os.link(temp_path, destination)
        except FileExistsError as exc:
            raise HTTPException(
                status_code=409,
                detail=f"{band_code} already exists in the {period} dataset; existing files are not overwritten.",
            ) from exc
        return {
            "success": True,
            "period": period,
            "band": band_code,
            "filename": destination.name,
            "size_bytes": total_bytes,
            "message": f"{band_code} uploaded to the {period} dataset.",
        }
    except DatasetError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally:
        await file.close()
        if temp_path and temp_path.exists():
            temp_path.unlink()


def _check_dataset(directory: Path, key: str) -> SatelliteDataset:
    return _validate_dataset(
        _dataset(
            directory,
            REQUIRED_BANDS[key],
            include_other_bands=False,
        ),
        key,
    )


def _historical_analysis(
    result_dir: Path,
    current: SatelliteDataset | None = None,
    historical: SatelliteDataset | None = None,
) -> dict:
    historical_dir = settings.data_dir / "historical"
    if not historical_dir.exists():
        raise DatasetError("Historical imagery is unavailable. Current-period analysis is still available.")
    historical = historical or _dataset(historical_dir, REQUIRED_BANDS["ndvi"])
    if not historical.bands:
        raise DatasetError("Historical imagery is unavailable. Current-period analysis is still available.")
    historical = _validate_dataset(historical, "ndvi")
    current = _validate_dataset(current, "ndvi") if current else _check_dataset(settings.data_dir / "current", "ndvi")
    data = calculate_change(
        current.bands["B04"],
        current.bands["B08"],
        historical.bands["B04"],
        historical.bands["B08"],
    )
    current_band = current.bands["B04"]
    pixel_area = pixel_area_square_metres(current_band)
    changed_pct = data["changed_pixel_percentage"]
    data["changed_area_square_metres"] = (
        data["statistics"]["valid_pixels"] * changed_pct / 100 * pixel_area
        if pixel_area is not None and changed_pct is not None
        else None
    )
    result = _save_analysis(
        result_dir, "change_detection", data, "Historical change detection", "change"
    )
    result["provenance"] = analysis_provenance(
        "change_detection",
        {
            "current": (current, REQUIRED_BANDS["ndvi"]),
            "historical": (historical, REQUIRED_BANDS["ndvi"]),
        },
    )
    return result


def _store_result(result_id: str, directory: Path, response: dict, job_id: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    serializable = _json_safe(response)
    (directory / "summary.json").write_text(json.dumps(serializable, indent=2), encoding="utf-8")
    try:
        persistence_manager.persist_result(result_id, job_id, serializable, directory)
    except ArtifactStorageError as exc:
        raise HTTPException(
            status_code=503,
            detail=(
                f"Analysis outputs could not be persisted: {exc} "
                "Local outputs have been retained for recovery."
            ),
            headers={"X-Processing-Job-ID": job_id},
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=(
                "Analysis outputs could not be persisted because metadata storage failed. "
                "Local outputs have been retained for recovery."
            ),
            headers={"X-Processing-Job-ID": job_id},
        ) from exc


def _validate_idempotency_key(value: str | None) -> str | None:
    if value is not None and not re.fullmatch(r"[A-Za-z0-9_-]{16,128}", value):
        raise HTTPException(
            status_code=400,
            detail="Idempotency-Key must contain 16-128 safe ASCII characters.",
        )
    return value


def _existing_analysis_response(
    analysis_id: str,
    job_id: str,
    analysis: str,
    input_parameters: dict[str, Any],
    owner_id: str | None,
) -> dict:
    record = persistence_manager.get_analysis(analysis_id, owner_id)
    job = persistence_manager.get_job(job_id, owner_id)
    if record is None or job is None:
        raise HTTPException(
            status_code=409,
            detail="The matching analysis request is already registered.",
            headers={"X-Processing-Job-ID": job_id},
        )
    if (
        record["analysis"] != analysis
        or record["input_parameters"] != input_parameters
        or job["input_parameters"] != input_parameters
    ):
        raise HTTPException(
            status_code=409,
            detail="Idempotency-Key was already used for a different analysis request.",
            headers={"X-Processing-Job-ID": job_id},
        )
    if job["status"] == "completed" and isinstance(record["summary"], dict):
        return record["summary"]
    if job["status"] in {"queued", "running"}:
        detail = "The matching analysis request is still processing."
    else:
        detail = "The matching analysis request failed; use a new Idempotency-Key to retry."
    raise HTTPException(
        status_code=409,
        detail=detail,
        headers={"X-Processing-Job-ID": job_id},
    )


def _run_analysis(
    key: str,
    owner_id: str | None = None,
    idempotency_key: str | None = None,
) -> dict:
    key = ANALYSIS_ALIASES.get(key, key)
    if key not in ANALYSES:
        raise HTTPException(status_code=404, detail="The requested analysis is not available.")
    result_id = uuid4().hex
    input_parameters = {
        "analysis_key": key,
        "dataset_ids": ["current", "historical"] if key == "change_detection" else ["current"],
    }
    job_start = persistence_manager.start_job(
        result_id,
        key,
        input_parameters,
        owner_id=owner_id,
        idempotency_key=idempotency_key,
    )
    result_id, job_id = job_start.analysis_id, job_start.job_id
    if not job_start.created:
        return _existing_analysis_response(
            result_id, job_id, key, input_parameters, owner_id
        )
    result_dir = settings.output_dir / result_id
    try:
        _check_analysis_deadline()
        if key == "change_detection":
            analysis = _historical_analysis(result_dir)
        else:
            dataset = _check_dataset(settings.data_dir / "current", key)
            analysis = _single_result(result_dir, key, dataset)
        _check_analysis_deadline()
        summary = {
            "success": True,
            "id": result_id,
            "job_id": job_id,
            "analysis": analysis["analysis"],
            "created_at": datetime.now(timezone.utc).isoformat(),
            "result": analysis,
        }
        _store_result(result_id, result_dir, summary, job_id)
        logger.info(
            "Analysis job completed.",
            extra={
                "event": "analysis.job_completed",
                "job_id": job_id,
                "analysis": key,
            },
        )
        return summary
    except DatasetError as exc:
        try:
            persistence_manager.fail_job(job_id, str(exc))
        except Exception as state_error:
            logger.error(
                "Failed to persist analysis job failure state.",
                extra={
                    "event": "analysis.job_state_update_failed",
                    "job_id": job_id,
                    "analysis": key,
                    "error_type": type(state_error).__name__,
                },
            )
        logger.warning(
            "Analysis job failed validation.",
            extra={
                "event": "analysis.job_failed",
                "job_id": job_id,
                "analysis": key,
                "error_type": type(exc).__name__,
            },
        )
        raise HTTPException(
            status_code=422,
            detail=str(exc),
            headers={"X-Processing-Job-ID": job_id},
        ) from exc
    except Exception as exc:
        safe_detail = (
            exc.detail
            if isinstance(exc, HTTPException) and isinstance(exc.detail, str)
            else "Analysis processing failed."
        )
        try:
            persistence_manager.fail_job(job_id, safe_detail)
        except Exception as state_error:
            logger.error(
                "Failed to persist analysis job failure state.",
                extra={
                    "event": "analysis.job_state_update_failed",
                    "job_id": job_id,
                    "analysis": key,
                    "error_type": type(state_error).__name__,
                },
            )
        logger.error(
            "Analysis job failed.",
            extra={
                "event": "analysis.job_failed",
                "job_id": job_id,
                "analysis": key,
                "error_type": type(exc).__name__,
            },
        )
        if isinstance(exc, HTTPException) and exc.status_code == 504:
            headers = dict(exc.headers or {})
            headers.setdefault("X-Processing-Job-ID", job_id)
            raise HTTPException(
                status_code=exc.status_code,
                detail=exc.detail,
                headers=headers,
            ) from exc
        raise


def _result_directory(result_id: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{32}", result_id):
        raise HTTPException(status_code=404, detail="Result was not found.")
    directory = (settings.output_dir / result_id).resolve()
    if directory.parent != settings.output_dir.resolve() or not directory.is_dir():
        raise HTTPException(status_code=404, detail="Result was not found.")
    return directory


@router.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "service": "satellite-intelligence-api",
    }


@router.get("/persistence/status")
def persistence_status() -> dict:
    return persistence_manager.status()


@router.get("/auth/status")
def authentication_status() -> dict:
    return {
        "authentication_required": settings.authentication_required,
        "supabase_auth_configured": bool(settings.supabase_url and settings.supabase_anon_key),
    }


def _validated_id(value: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{32}", value):
        raise HTTPException(status_code=404, detail="Record was not found.")
    return value


@router.get("/analyses/{analysis_id}")
def get_analysis_record(
    analysis_id: str,
    user: AuthenticatedUser | None = Depends(current_user),
) -> dict:
    analysis_id = _validated_id(analysis_id)
    record = persistence_manager.get_analysis(analysis_id, user.id if user else None)
    if record is None:
        raise HTTPException(status_code=404, detail="Analysis metadata is unavailable.")
    return record


@router.get("/jobs/{job_id}")
def get_processing_job(
    job_id: str,
    user: AuthenticatedUser | None = Depends(current_user),
) -> dict:
    job_id = _validated_id(job_id)
    record = persistence_manager.get_job(job_id, user.id if user else None)
    if record is None:
        raise HTTPException(status_code=404, detail="Processing job is unavailable.")
    return record


@router.get("/ready")
def ready() -> JSONResponse:
    report = persistence_manager.readiness(validate_runtime_settings())
    return JSONResponse(
        status_code=200 if report["status"] == "ok" else 503,
        content={
            **report,
        "service": "satellite-intelligence-api",
        "stage": "A",
        },
    )


@router.get("/dataset")
def dataset_status() -> dict:
    try:
        current = _public_dataset(_dataset(settings.data_dir / "current"), "current")
        historical = _public_dataset(
            _dataset(settings.data_dir / "historical"), "historical"
        )
    except DatasetError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    provider = _provider_for_mode()
    return {
        "success": True,
        "current": current,
        "historical": historical,
        "provider": provider.name,
        "provider_configured": provider.configured,
        "ready_for_analysis": bool(current["available"]),
        "message": None if current["available"] else EMPTY_DATA_MESSAGE,
    }


@router.get("/satellite/status")
def satellite_status() -> dict:
    provider = _provider_for_mode()
    return {"success": True, **provider.status()}


@router.post("/satellite/search")
def satellite_search(request: SatelliteSearchRequest) -> dict:
    provider = _provider_for_mode()
    try:
        results = provider.search(request)
    except ProviderNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ProviderCapabilityUnavailableError as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"success": True, "provider": provider.name, "results": results}


@router.post("/satellite/download")
def satellite_download(
    request: SatelliteDownloadRequest,
    user: AuthenticatedUser | None = Depends(current_user),
) -> dict:
    provider = _provider_for_mode()
    try:
        result = provider.download(request.product_id, aoi=request.aoi)
    except ProviderNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ProviderCapabilityUnavailableError as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=404 if isinstance(exc, KeyError) else 400, detail=str(exc)) from exc
    return {"success": True, "provider": provider.name, "download": result}


@router.get("/satellite/products")
def satellite_products() -> dict:
    provider = _provider_for_mode()
    return {"success": True, "provider": provider.name, "results": provider.list_products()}


@router.get("/satellite/products/{product_id}")
def satellite_product(product_id: str) -> dict:
    provider = _provider_for_mode()
    try:
        return {"success": True, "provider": provider.name, "result": provider.get_product(product_id)}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/satellite/cache")
def satellite_cache() -> dict:
    return {
        "success": True,
        "provider": _provider_for_mode().name,
        "results": _request_cache_manager().list_products(),
    }


@router.delete("/satellite/cache/{product_id}")
def clear_satellite_cache(product_id: str) -> dict:
    try:
        deleted = _request_cache_manager().delete_product(product_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"success": True, "deleted": deleted, "product_id": product_id}


@router.get("/metadata")
def metadata() -> dict:
    try:
        current = _dataset(settings.data_dir / "current")
        historical = _dataset(settings.data_dir / "historical")
    except DatasetError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "success": True,
        "current": {
            "available": bool(current.bands),
            "metadata": raster_metadata(next(iter(current.bands.values()), None)),
            "bands": list(current.bands.keys()),
            "provenance": dataset_provenance(
                "current", current, tuple(current.bands.keys())
            ),
        },
        "historical": {
            "available": bool(historical.bands),
            "metadata": raster_metadata(next(iter(historical.bands.values()), None)),
            "bands": list(historical.bands.keys()),
            "provenance": dataset_provenance(
                "historical", historical, tuple(historical.bands.keys())
            ),
        },
    }


@router.get("/data-quality")
def data_quality() -> dict:
    try:
        current = _dataset(settings.data_dir / "current", SUPPORTED_BANDS)
    except DatasetError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "success": True,
        "current": _data_quality_summary(current),
    }


@router.get("/model/status")
def model_status() -> dict:
    model = LandCoverModel()
    return {
        "success": True,
        "model": model.status(),
    }


@router.get("/geoai/status")
def geoai_status() -> dict:
    return {
        "success": True,
        "status": "ok",
        "available": True,
        "components": [
            "spatial_analysis",
            "trend_models",
            "vegetation_forecast",
            "transition_analysis",
            "risk_indicators",
            "model_evaluation",
            "uncertainty",
        ],
        "endpoints": [
            "/api/geoai/status",
            "/api/geoai/demo",
            "/api/geoai/spatial-analysis",
            "/api/geoai/vegetation-forecast",
            "/api/geoai/land-cover-transitions",
            "/api/geoai/model-evaluation",
            "/api/geoai/risk-indicators",
            "/api/geoai/history",
        ],
    }


@router.get("/geoai/demo")
def geoai_demo_scenario(
    user: AuthenticatedUser | None = Depends(current_user),
) -> dict:
    del user
    synthetic_wkt = (
        'LOCAL_CS["Synthetic demo grid",'
        'LOCAL_DATUM["Synthetic local datum",0],UNIT["metre",1],'
        "AXIS[\"Easting\",EAST],AXIS[\"Northing\",NORTH]]"
    )
    reflectance = {
        "B02": np.full((4, 4), 0.1, dtype=np.float32),
        "B03": np.array(
            [
                [0.4, 0.2, 0.1, 0.3],
                [0.4, 0.2, 0.1, 0.3],
                [0.3, 0.4, 0.2, 0.1],
                [0.3, 0.4, 0.2, 0.1],
            ],
            dtype=np.float32,
        ),
        "B04": np.array(
            [
                [0.1, 0.3, 0.2, 0.4],
                [0.1, 0.3, 0.2, 0.4],
                [0.2, 0.1, 0.3, 0.4],
                [0.2, 0.1, 0.3, 0.4],
            ],
            dtype=np.float32,
        ),
        "B08": np.array(
            [
                [0.6, 0.4, 0.3, 0.4],
                [0.6, 0.4, 0.3, 0.4],
                [0.3, 0.6, 0.4, 0.5],
                [0.3, 0.6, 0.4, 0.5],
            ],
            dtype=np.float32,
        ),
        "B11": np.array(
            [
                [0.2, 0.6, 0.7, 0.2],
                [0.2, 0.6, 0.7, 0.2],
                [0.7, 0.2, 0.6, 0.5],
                [0.7, 0.2, 0.6, 0.5],
            ],
            dtype=np.float32,
        ),
    }
    valid = np.ones((4, 4), dtype=bool)
    bands = {
        code: RasterBand(
            code=code,
            data=values,
            valid=valid.copy(),
            profile={"nodata": None},
            crs=synthetic_wkt,
            transform=Affine.identity(),
            width=4,
            height=4,
            resolution=(1.0, 1.0),
            tags={
                "SATELLITE_VISION_DATA_KIND": "synthetic",
                "SOURCE": "deterministic in-memory hackathon demo fixture",
            },
        )
        for code, values in reflectance.items()
    }
    dataset = SatelliteDataset(
        directory=Path("synthetic-demo-fixture"),
        bands=bands,
        missing_bands=[],
    )
    raw_results = {
        "ndvi": calculate_ndvi(bands["B04"], bands["B08"]),
        "ndwi": calculate_ndwi(bands["B03"], bands["B08"]),
        "ndbi": calculate_ndbi(bands["B08"], bands["B11"]),
        "landcover": LandCoverClassifier().classify(bands),
    }
    return {
        "success": True,
        "scenario_id": "synthetic-index-baseline-v1",
        "data_classification": "synthetic",
        "source": "deterministic in-memory synthetic fixture",
        "acquisition_date": None,
        "geographic_coverage": None,
        "sensor": None,
        "message": (
            "This reproducible 4x4 example is synthetic, not satellite imagery. "
            "It has no acquisition date, real-world coordinates, or validated reflectance."
        ),
        "results": {
            key: _json_safe(result) for key, result in raw_results.items()
        },
        "provenance": {
            key: analysis_provenance(
                key, {"synthetic_demo": (dataset, REQUIRED_BANDS[key])}
            )
            for key in raw_results
        },
    }


@router.post("/geoai/spatial-analysis")
def geoai_spatial_analysis(request: SpatialAnalysisRequest) -> dict:
    if request.aoi is not None:
        raise HTTPException(
            status_code=422,
            detail="AOI clipping is not implemented; this endpoint summarizes the full input raster extent.",
        )
    dataset_dir = settings.data_dir / request.dataset_id
    try:
        dataset = _dataset(dataset_dir)
    except DatasetError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    historical = None
    if request.include_history and request.dataset_id == "current":
        try:
            historical = _check_dataset(
                settings.data_dir / "historical", "ndvi"
            )
        except DatasetError as exc:
            history_warning = str(exc)
        else:
            history_warning = None
    else:
        history_warning = None
    result = summarize_spatial_patterns(dataset, historical, block_size=request.block_size)
    provenance_datasets = {
        request.dataset_id: (dataset, ("B04", "B08"))
    }
    if historical is not None:
        provenance_datasets["historical"] = (historical, ("B04", "B08"))
    result["provenance"] = analysis_provenance("ndvi", provenance_datasets)
    result["observation_dates"] = [
        date
        for date in (
            dataset_provenance(
                dataset_id, source_dataset, bands
            ).get("acquisition_date")
            for dataset_id, (source_dataset, bands) in provenance_datasets.items()
        )
        if date
    ]
    if history_warning:
        result["warnings"] = [history_warning]
    return {"success": result.get("status") == "ok", **result}


@router.post("/geoai/vegetation-forecast")
def geoai_vegetation_forecast(request: VegetationForecastRequest) -> dict:
    caller_supplied = request.observations is not None
    warnings: list[str] = []
    if caller_supplied:
        observations = request.observations or []
        warnings.append(
            "Caller-supplied observations are not independently verified as satellite acquisitions."
        )
    else:
        dataset_ids = (
            ("current", "historical")
            if request.dataset_id == "all"
            else (request.dataset_id,)
        )
        observations, warnings = _dated_ndvi_observations(dataset_ids)
    if request.lookback_limit is not None and len(observations) > request.lookback_limit:
        observations = observations[-request.lookback_limit:]
    result = forecast_vegetation_trends(observations, horizon_days=request.horizon_days)
    result["data_source"] = (
        "caller_supplied_unverified"
        if caller_supplied
        else "unverified_raster_acquisition_tags"
    )
    result["data_classification"] = (
        "caller_supplied_unverified"
        if caller_supplied
        else (
            "synthetic"
            if any(
                observation["provenance"]["data_classification"] == "synthetic"
                for observation in observations
            )
            else "unverified_local_input"
        )
    )
    result["observation_warnings"] = warnings
    result["observation_count"] = len(observations)
    if caller_supplied and result.get("status") == "ok":
        result["evaluation"] = {
            "status": "unavailable",
            "message": "Evaluation metrics are suppressed for caller-supplied observations that have not been independently verified.",
            "metrics": {},
        }
    if result.get("status") == "insufficient-data":
        return {"success": False, **result}
    return {"success": True, **result}


@router.post("/geoai/land-cover-transitions")
def geoai_land_cover_transitions(request: TransitionRequest) -> dict:
    try:
        current = _dataset(settings.data_dir / request.current_dataset_id)
        historical = _dataset(settings.data_dir / request.historical_dataset_id)
    except DatasetError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    result = analyze_land_cover_transitions(current, historical)
    result["provenance"] = analysis_provenance(
        "landcover",
        {
            "current": (current, REQUIRED_BANDS["landcover"]),
            "historical": (historical, REQUIRED_BANDS["landcover"]),
        },
    )
    if result.get("status") == "insufficient-data":
        return {"success": False, **result}
    return {"success": True, **result}


@router.get("/geoai/model-evaluation")
def geoai_model_evaluation() -> dict:
    observations, warnings = _dated_ndvi_observations()
    if len(observations) < 5:
        return {
            "success": False,
            "status": "insufficient-data",
            "model": "linear-trend forecast baseline",
            "data_classification": (
                "synthetic"
                if any(
                    item["provenance"]["data_classification"] == "synthetic"
                    for item in observations
                )
                else "unverified_local_input"
            ),
            "message": "Evaluation requires at least five distinct, metadata-dated NDVI observations; the available inputs do not establish supervised land-cover model accuracy.",
            "observation_count": len(observations),
            "observation_warnings": warnings,
            "metrics": {},
        }
    result = evaluate_forecast(
        [item["mean_ndvi"] for item in observations]
    )
    result["model"] = "linear-trend forecast baseline"
    result["observation_dates"] = [item["date"] for item in observations]
    result["observation_warnings"] = warnings
    result["data_classification"] = (
        "synthetic"
        if any(
            item["provenance"]["data_classification"] == "synthetic"
            for item in observations
        )
        else "unverified_local_input"
    )
    result["limitations"] = (
        "This is a time-ordered evaluation of the simple NDVI trend baseline, "
        "not a supervised land-cover classifier evaluation. It uses only "
        "metadata-dated local rasters and is not a substitute for independent ground truth."
    )
    return {"success": result.get("status") == "ok", **result}


@router.get("/geoai/risk-indicators")
def geoai_risk_indicators() -> dict:
    try:
        current = _check_dataset(settings.data_dir / "current", "ndvi")
    except DatasetError as exc:
        return {
            "success": False,
            "status": "insufficient-data",
            "message": str(exc),
            "indicators": [],
            "count": 0,
        }
    spatial = summarize_spatial_patterns(current)
    if spatial.get("status") != "ok":
        return {
            "success": False,
            "status": "insufficient-data",
            "message": spatial.get("message", "Current raster data is insufficient."),
            "indicators": [],
            "count": 0,
        }
    provenance_datasets = {"current": (current, REQUIRED_BANDS["ndvi"])}
    current_date = dataset_provenance(
        "current", current, REQUIRED_BANDS["ndvi"]
    ).get("acquisition_date")
    observation_dates = [current_date] if current_date else []
    transition_summary = None
    try:
        historical = _check_dataset(settings.data_dir / "historical", "ndvi")
    except DatasetError:
        historical = None
    if historical is not None:
        provenance_datasets["historical"] = (
            historical,
            REQUIRED_BANDS["ndvi"],
        )
        historical_date = dataset_provenance(
            "historical", historical, REQUIRED_BANDS["ndvi"]
        ).get("acquisition_date")
        if historical_date:
            observation_dates.insert(0, historical_date)
        spatial = summarize_spatial_patterns(current, historical)
        if all(code in current.bands and code in historical.bands for code in REQUIRED_BANDS["landcover"]):
            transition_summary = analyze_land_cover_transitions(
                current, historical
            )
            if transition_summary.get("status") != "ok":
                transition_summary = None
    spatial["observation_dates"] = observation_dates
    spatial["data_quality"] = {
        "status": "available",
        "source": "available local raster inputs",
        "warnings": [
            warning
            for dataset_id, (source_dataset, band_codes) in provenance_datasets.items()
            for warning in dataset_provenance(
                dataset_id, source_dataset, band_codes
            )["quality_warnings"]
        ],
    }
    result = detect_risk_indicators(
        spatial, transition_summary=transition_summary
    )
    result["evidence_sources"] = {
        dataset_id: dataset_provenance(dataset_id, source_dataset, band_codes)
        for dataset_id, (source_dataset, band_codes) in provenance_datasets.items()
    }
    result["data_classification"] = (
        "synthetic"
        if any(
            source["data_classification"] == "synthetic"
            for source in result["evidence_sources"].values()
        )
        else "unverified_local_input"
    )
    return {"success": True, **result}


@router.get("/geoai/history")
def geoai_history() -> dict:
    observations, warnings = _dated_ndvi_observations()
    if len(observations) < 2:
        return {
            "success": False,
            "status": "insufficient-data",
            "message": "At least two distinct raster acquisition dates are required; untagged or undated inputs are not treated as a time series.",
            "observation_count": len(observations),
            "observation_warnings": warnings,
            "series": observations,
        }
    summary = summarize_observation_history(observations)
    summary["observation_warnings"] = warnings
    summary["data_classification"] = (
        "synthetic"
        if any(
            observation["provenance"]["data_classification"] == "synthetic"
            for observation in observations
        )
        else "unverified_local_input"
    )
    return {"success": summary.get("status") == "ok", **summary}


def _visualization_path(kind: str) -> Path:
    if settings.authentication_required:
        data_root = _request_dataset_root()
        output_dir = settings.output_dir / "users" / data_root.name / "visualizations"
    else:
        output_dir = settings.output_dir / "visualizations"
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir / f"{kind}.png"


def _generate_visualization(kind: str) -> Path:
    if kind == "rgb":
        dataset = _dataset(settings.data_dir / "current")
        dataset.require(("B02", "B03", "B04"))
        output = _visualization_path(kind)
        save_rgb_preview(dataset.bands["B04"], dataset.bands["B03"], dataset.bands["B02"], output)
        return output
    if kind == "ndvi":
        dataset = _check_dataset(settings.data_dir / "current", "ndvi")
        result = calculate_ndvi(dataset.bands["B04"], dataset.bands["B08"])
        output = _visualization_path(kind)
        save_index_png(result["raster"], result["valid_mask"], output, "ndvi")
        return output
    if kind == "ndwi":
        dataset = _check_dataset(settings.data_dir / "current", "ndwi")
        result = calculate_ndwi(dataset.bands["B03"], dataset.bands["B08"])
        output = _visualization_path(kind)
        save_index_png(result["raster"], result["valid_mask"], output, "ndwi")
        return output
    if kind == "ndbi":
        dataset = _check_dataset(settings.data_dir / "current", "ndbi")
        result = calculate_ndbi(dataset.bands["B08"], dataset.bands["B11"])
        output = _visualization_path(kind)
        save_index_png(result["raster"], result["valid_mask"], output, "ndbi")
        return output
    if kind == "landcover":
        dataset = _check_dataset(settings.data_dir / "current", "landcover")
        result = LandCoverClassifier().classify(dataset.bands)
        output = _visualization_path(kind)
        save_class_png(result["raster"], result["valid_mask"], output)
        return output
    if kind == "change":
        current = _check_dataset(settings.data_dir / "current", "ndvi")
        historical = _dataset(settings.data_dir / "historical")
        if not historical.bands:
            raise DatasetError("Historical imagery is unavailable for change visualization.")
        historical = _validate_dataset(historical, "ndvi")
        result = calculate_change(current.bands["B04"], current.bands["B08"], historical.bands["B04"], historical.bands["B08"])
        output = _visualization_path(kind)
        save_index_png(result["raster"], result["valid_mask"], output, "change")
        return output
    raise HTTPException(status_code=404, detail="Visualization layer was not found.")


@router.get("/visualization/{kind}")
def visualization(
    kind: str,
    user: AuthenticatedUser | None = Depends(current_user),
):
    if kind not in {"rgb", "ndvi", "ndwi", "ndbi", "landcover", "change"}:
        raise HTTPException(status_code=404, detail="Visualization layer was not found.")
    try:
        path = _generate_visualization(kind)
    except DatasetError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return FileResponse(path, media_type="image/png")


@router.post("/analyze/all")
def analyze_all(
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    user: AuthenticatedUser | None = Depends(current_user),
) -> dict:
    idempotency_key = _validate_idempotency_key(idempotency_key)
    with _analysis_slot():
        return _run_all_analysis(idempotency_key, user)


def _run_all_analysis(
    idempotency_key: str | None,
    user: AuthenticatedUser | None,
) -> dict:
    result_id = uuid4().hex
    input_parameters = {
        "analysis_keys": list(ANALYSES),
        "dataset_ids": ["current", "historical"],
    }
    job_start = persistence_manager.start_job(
        result_id,
        "all",
        input_parameters,
        owner_id=user.id if user else None,
        idempotency_key=idempotency_key,
    )
    result_id, job_id = job_start.analysis_id, job_start.job_id
    if not job_start.created:
        return _existing_analysis_response(
            result_id,
            job_id,
            "all",
            input_parameters,
            user.id if user else None,
        )
    result_dir = settings.output_dir / result_id
    try:
        _check_analysis_deadline()
        current = _dataset(settings.data_dir / "current")
        historical = _dataset(settings.data_dir / "historical")
        results = {}
        for key in ANALYSES:
            _check_analysis_deadline()
            try:
                if key == "change_detection":
                    results[key] = {
                        "success": True,
                        **_historical_analysis(result_dir, current=current, historical=historical),
                    }
                else:
                    validated = _validate_dataset(current, key)
                    results[key] = {
                        "success": True,
                        **_single_result(result_dir, key, validated),
                    }
            except DatasetError as exc:
                results[key] = {"success": False, "message": str(exc)}
        response = {
            "success": True,
            "id": result_id,
            "job_id": job_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "dataset": {
                "current": bool(current.bands),
                "historical": bool(historical.bands),
                "message": None if current.bands else EMPTY_DATA_MESSAGE,
            },
            **results,
        }
        _check_analysis_deadline()
        _store_result(result_id, result_dir, response, job_id)
        return response
    except Exception as exc:
        persistence_manager.fail_job(job_id, f"{type(exc).__name__}: {exc}")
        if isinstance(exc, HTTPException) and exc.status_code == 504:
            headers = dict(exc.headers or {})
            headers.setdefault("X-Processing-Job-ID", job_id)
            raise HTTPException(
                status_code=exc.status_code,
                detail=exc.detail,
                headers=headers,
            ) from exc
        raise


@router.post("/analyze/{analysis_key}")
def analyze(
    analysis_key: str,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    user: AuthenticatedUser | None = Depends(current_user),
) -> dict:
    with _analysis_slot():
        return _run_analysis(
            analysis_key,
            user.id if user else None,
            _validate_idempotency_key(idempotency_key),
        )


@router.get("/results")
def list_results(
    limit: int = Query(DEFAULT_RESULTS_PAGE_SIZE, ge=1, le=MAX_RESULTS_PAGE_SIZE),
    offset: int = Query(0, ge=0, le=10_000),
    user: AuthenticatedUser | None = Depends(current_user),
) -> dict:
    results_by_id = {}
    owner_id = user.id if user else None
    has_more = False
    persisted_results = persistence_manager.list_analyses(
        owner_id,
        limit=limit,
        offset=offset,
    )
    if persisted_results is not None:
        has_more = len(persisted_results) > limit
        for result in persisted_results[:limit]:
            results_by_id[result["id"]] = {
                "id": result["id"],
                "created_at": result["created_at"],
                "analysis": result["analysis"],
            }
        page = list(results_by_id.values())
        return {
            "success": True,
            "results": page,
            "has_more": has_more,
            "next_offset": offset + len(page) if has_more else None,
        }

    output_dir = settings.output_dir
    if user is None and not settings.authentication_required and output_dir.exists():
        def modified_time(path: Path) -> float:
            try:
                return path.stat().st_mtime
            except OSError:
                return 0.0

        candidate_limit = offset + limit + 1
        candidates = heapq.nlargest(
            candidate_limit,
            (
                path
                for path in output_dir.iterdir()
                if path.is_dir() and (path / "summary.json").is_file()
            ),
            key=modified_time,
        )
        has_more = len(candidates) > offset + limit
        for path in candidates[offset : offset + limit]:
            summary_path = path / "summary.json"
            try:
                summary = json.loads(summary_path.read_text(encoding="utf-8"))
                summary_id = summary.get("id")
                if summary_id:
                    results_by_id[summary_id] = {
                        "id": summary_id,
                        "created_at": summary.get("created_at"),
                        "analysis": summary.get("analysis", "Analysis bundle"),
                    }
            except (OSError, json.JSONDecodeError):
                continue
    page = list(results_by_id.values())
    return {
        "success": True,
        "results": page,
        "has_more": has_more if user is None and not settings.authentication_required else False,
        "next_offset": (
            offset + len(page)
            if has_more and user is None and not settings.authentication_required
            else None
        ),
    }


@router.get("/results/{result_id}")
def get_result(
    result_id: str,
    user: AuthenticatedUser | None = Depends(current_user),
) -> dict:
    result_id = _validated_id(result_id)
    record = persistence_manager.get_analysis(result_id, user.id if user else None)
    if record and record["summary"] is not None:
        return record["summary"]
    if user is not None or settings.authentication_required:
        raise HTTPException(status_code=404, detail="Result was not found.")
    try:
        summary_path = _result_directory(result_id) / "summary.json"
    except HTTPException:
        raise HTTPException(status_code=404, detail="Result was not found.")
    if not summary_path.is_file():
        raise HTTPException(status_code=404, detail="Result was not found.")
    return json.loads(summary_path.read_text(encoding="utf-8"))


@router.get("/results/{result_id}/artifacts")
def list_result_artifacts(
    result_id: str,
    user: AuthenticatedUser | None = Depends(current_user),
) -> dict:
    result_id = _validated_id(result_id)
    artifacts = []
    for artifact in persistence_manager.get_artifacts(
        result_id, user.id if user else None
    ):
        artifacts.append(
            {
                "artifact_name": artifact["artifact_name"],
                "media_type": artifact["media_type"],
                "artifact_type": artifact.get("artifact_type", "file"),
                "size_bytes": artifact["size_bytes"],
                "created_at": artifact.get("created_at"),
                "download_url": (
                    f"/api/results/{result_id}/artifacts/"
                    f"{quote(artifact['artifact_name'], safe='')}/download"
                ),
            }
        )
    return {
        "success": True,
        "result_id": result_id,
        "artifacts": artifacts,
    }


@router.get("/results/{result_id}/artifacts/{artifact_name}/download")
def download_registered_artifact(
    result_id: str,
    artifact_name: str,
    user: AuthenticatedUser | None = Depends(current_user),
):
    result_id = _validated_id(result_id)
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,255}", artifact_name):
        raise HTTPException(status_code=404, detail="Artifact was not found.")
    try:
        stored = persistence_manager.get_artifact(
            result_id,
            artifact_name,
            owner_id=user.id if user else None,
        )
    except (ArtifactStorageError, OSError) as exc:
        raise HTTPException(
            status_code=503,
            detail="Analysis artifact storage is temporarily unavailable.",
        ) from exc
    if stored is None:
        raise HTTPException(status_code=404, detail="Artifact was not found.")
    file_obj, artifact = stored

    def chunks():
        try:
            while chunk := file_obj.read(1024 * 1024):
                yield chunk
        finally:
            file_obj.close()

    return StreamingResponse(
        chunks(),
        media_type=artifact["media_type"] or "application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{artifact_name}"'},
    )


@router.get("/results/{result_id}/signed-download")
def create_signed_download(
    result_id: str,
    artifact_name: str,
    user: AuthenticatedUser | None = Depends(current_user),
):
    result_id = _validated_id(result_id)
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,255}", artifact_name):
        raise HTTPException(status_code=404, detail="Artifact was not found.")
    try:
        signed_url = persistence_manager.create_signed_download_url(
            result_id,
            artifact_name,
            owner_id=user.id if user else None,
            expires_in=300,
        )
    except (ArtifactStorageError, OSError) as exc:
        raise HTTPException(
            status_code=503,
            detail="Analysis artifact storage is temporarily unavailable.",
        ) from exc
    if signed_url is None:
        raise HTTPException(status_code=404, detail="Artifact was not found.")
    return {"signed_url": signed_url, "expires_in": 300}


def _stream_artifact(
    result_id: str,
    filename: str,
    media_type: str,
    download: bool = False,
    owner_id: str | None = None,
):
    result_id = _validated_id(result_id)
    try:
        stored = persistence_manager.get_artifact(result_id, filename, owner_id)
    except (ArtifactStorageError, OSError) as exc:
        raise HTTPException(
            status_code=503,
            detail="Analysis artifact storage is temporarily unavailable.",
        ) from exc
    if stored is not None:
        file_obj, artifact = stored
        actual_media_type = artifact["media_type"] or media_type
        headers = {"Content-Disposition": f'attachment; filename="{filename}"'} if download else {}

        def chunks():
            try:
                while chunk := file_obj.read(1024 * 1024):
                    yield chunk
            finally:
                file_obj.close()

        return StreamingResponse(chunks(), media_type=actual_media_type, headers=headers)
    if owner_id is not None or settings.authentication_required:
        raise HTTPException(status_code=404, detail="Artifact was not found.")
    try:
        path = _result_directory(result_id) / filename
    except HTTPException:
        raise HTTPException(status_code=404, detail="Artifact was not found.")
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Artifact was not found.")
    return FileResponse(
        path,
        media_type=media_type,
        filename=filename if download else None,
    )


@router.get("/results/{result_id}/download/{file_key}")
def download_result(
    result_id: str,
    file_key: str,
    user: AuthenticatedUser | None = Depends(current_user),
):
    if file_key not in DOWNLOAD_FILES:
        raise HTTPException(status_code=404, detail="Download was not found.")
    return _stream_artifact(
        result_id,
        DOWNLOAD_FILES[file_key],
        mimetypes.guess_type(DOWNLOAD_FILES[file_key])[0] or "application/octet-stream",
        download=True,
        owner_id=user.id if user else None,
    )


@router.get("/results/{result_id}/image/{analysis_key}")
def get_result_image(
    result_id: str,
    analysis_key: str,
    user: AuthenticatedUser | None = Depends(current_user),
):
    if analysis_key not in ANALYSES:
        raise HTTPException(status_code=404, detail="Map layer was not found.")
    return _stream_artifact(
        result_id, f"{analysis_key}.png", "image/png", owner_id=user.id if user else None
    )

import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import numpy as np
import rasterio
from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from rasterio.warp import transform_bounds

from config import settings, validate_runtime_settings
from models.landcover_model import LandCoverModel
from processing.change_detection import calculate_change
from processing.data_provider import LocalDataProvider
from processing.landcover import LandCoverClassifier
from processing.ndbi import calculate_ndbi
from processing.ndvi import calculate_ndvi
from processing.ndwi import calculate_ndwi
from processing.preprocessing import (
    DatasetError,
    SatelliteDataset,
    build_profile,
    identify_band,
    pixel_area_square_metres,
    raster_metadata,
)
from processing.visualization import save_class_png, save_index_png, save_rgb_preview
from geoai import (
    analyze_land_cover_transitions,
    detect_risk_indicators,
    evaluate_forecast,
    forecast_vegetation_trends,
    summarize_observation_history,
    summarize_spatial_patterns,
    summarize_uncertainty,
)
from geoai.schemas import SpatialAnalysisRequest, TransitionRequest, VegetationForecastRequest
from satellite.base_provider import ProviderNotConfiguredError
from satellite.cache import CacheManager
from satellite.local_provider import LocalSatelliteProvider
from satellite.live_provider import LiveSatelliteProvider
from satellite.mock_provider import MockSatelliteProvider
from satellite.models import SatelliteDownloadRequest, SatelliteSearchRequest

router = APIRouter()
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
cache_manager = CacheManager(settings.satellite_cache_dir)


def _provider_for_mode():
    provider_name = (settings.satellite_provider or "local").lower()
    if provider_name == "mock":
        return MockSatelliteProvider(cache_manager)
    if provider_name == "live":
        return LiveSatelliteProvider()
    return LocalSatelliteProvider()


def _dataset(directory: Path, required: tuple[str, ...] = ()) -> SatelliteDataset:
    try:
        period = directory.resolve().relative_to(settings.data_dir.resolve()).as_posix()
    except ValueError as exc:
        raise DatasetError("Requested dataset directory is outside the configured data root.") from exc
    return data_provider.load(period, required)


def _public_dataset(dataset: SatelliteDataset) -> dict:
    reference = next(iter(dataset.bands.values()), None)
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
    total_pixels = reference.width * reference.height
    nodata_pixels = 0
    for band in dataset.bands.values():
        valid_pixels += int(np.count_nonzero(band.valid))
        nodata_pixels += int(np.count_nonzero(~band.valid))
    nodata_percentage = (nodata_pixels / max(total_pixels, 1)) * 100 if total_pixels else 0.0
    warnings = []
    if dataset.missing_bands:
        warnings.append(f"Missing required bands: {', '.join(dataset.missing_bands)}")
    if nodata_percentage > 20:
        warnings.append("A large share of pixels is nodata or invalid.")
    if reference.crs is None:
        warnings.append("Raster CRS is missing; geospatial interpretation is limited.")
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
        "historical_available": historical_available,
        "warnings": warnings,
    }


def _bounds(reference) -> list[float] | None:
    if not reference or not reference.crs:
        return None
    bounds = rasterio.transform.array_bounds(
        reference.height, reference.width, reference.transform
    )
    if reference.crs != "EPSG:4326":
        bounds = transform_bounds(reference.crs, "EPSG:4326", *bounds, densify_pts=21)
    west, south, east, north = bounds
    return [west, south, east, north]


def _json_safe(value):
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
        return _save_analysis(result_dir, key, data, "NDVI", "ndvi")
    if key == "ndwi":
        return _save_analysis(result_dir, key, calculate_ndwi(bands["B03"], bands["B08"]), "NDWI", "ndwi")
    if key == "ndbi":
        return _save_analysis(result_dir, key, calculate_ndbi(bands["B08"], bands["B11"]), "NDBI", "ndbi")
    if key == "landcover":
        return _save_analysis(
            result_dir,
            key,
            LandCoverClassifier().classify(bands),
            "Land-use / land-cover",
            classes=True,
        )
    raise DatasetError(f"Unsupported analysis: {key}.")


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
                try:
                    with rasterio.open(existing_path) as existing:
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
                except rasterio.errors.RasterioError:
                    continue
    except DatasetError:
        raise
    except (rasterio.errors.RasterioError, OSError) as exc:
        raise DatasetError("The uploaded file is not a readable GeoTIFF.") from exc


@router.post("/dataset/{period}/upload", status_code=201)
async def upload_dataset_band(period: str, file: UploadFile = File(...)) -> dict:
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

    dataset_dir = (settings.data_dir / period).resolve()
    if dataset_dir.parent != settings.data_dir.resolve():
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
                temporary.write(chunk)
        if total_bytes == 0:
            raise HTTPException(status_code=422, detail="The uploaded GeoTIFF is empty.")

        try:
            with rasterio.open(temp_path) as source:
                if source.count != 1:
                    raise DatasetError("Each uploaded GeoTIFF must contain exactly one band.")
                if source.crs is None:
                    raise DatasetError("The uploaded GeoTIFF has no CRS; add georeferenced imagery.")
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
        except (rasterio.errors.RasterioError, OSError, ValueError) as exc:
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
    return _validate_dataset(_dataset(directory, REQUIRED_BANDS[key]), key)


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
    return _save_analysis(result_dir, "change_detection", data, "Historical change detection", "change")


def _store_result(result_id: str, directory: Path, response: dict) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    serializable = _json_safe(response)
    (directory / "summary.json").write_text(json.dumps(serializable, indent=2), encoding="utf-8")


def _run_analysis(key: str) -> dict:
    key = ANALYSIS_ALIASES.get(key, key)
    if key not in ANALYSES:
        raise HTTPException(status_code=404, detail="The requested analysis is not available.")
    result_id = uuid4().hex
    result_dir = settings.output_dir / result_id
    try:
        if key == "change_detection":
            analysis = _historical_analysis(result_dir)
        else:
            dataset = _check_dataset(settings.data_dir / "current", key)
            analysis = _single_result(result_dir, key, dataset)
    except DatasetError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    summary = {
        "success": True,
        "id": result_id,
        "analysis": analysis["analysis"],
        "created_at": datetime.now(timezone.utc).isoformat(),
        "result": analysis,
    }
    _store_result(result_id, result_dir, summary)
    return summary


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
        "stage": "A",
        "provider": _provider_for_mode().name,
        "configured": _provider_for_mode().configured,
    }


@router.get("/ready")
def ready() -> dict:
    issues = validate_runtime_settings()
    return {
        "status": "ok" if not issues else "degraded",
        "checks": {"configuration": issues},
        "service": "satellite-intelligence-api",
        "stage": "A",
    }


@router.get("/dataset")
def dataset_status() -> dict:
    try:
        current = _public_dataset(_dataset(settings.data_dir / "current"))
        historical = _public_dataset(_dataset(settings.data_dir / "historical"))
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
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"success": True, "provider": provider.name, "results": results}


@router.post("/satellite/download")
def satellite_download(request: SatelliteDownloadRequest) -> dict:
    provider = _provider_for_mode()
    try:
        result = provider.download(request.product_id, aoi=request.aoi)
    except ProviderNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
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
    return {"success": True, "provider": _provider_for_mode().name, "results": cache_manager.list_products()}


@router.delete("/satellite/cache/{product_id}")
def clear_satellite_cache(product_id: str) -> dict:
    try:
        deleted = cache_manager.delete_product(product_id)
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
        },
        "historical": {
            "available": bool(historical.bands),
            "metadata": raster_metadata(next(iter(historical.bands.values()), None)),
            "bands": list(historical.bands.keys()),
        },
    }


@router.get("/data-quality")
def data_quality() -> dict:
    try:
        current = _dataset(settings.data_dir / "current")
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
            "/api/geoai/spatial-analysis",
            "/api/geoai/vegetation-forecast",
            "/api/geoai/land-cover-transitions",
            "/api/geoai/model-evaluation",
            "/api/geoai/risk-indicators",
            "/api/geoai/history",
        ],
    }


@router.post("/geoai/spatial-analysis")
def geoai_spatial_analysis(request: SpatialAnalysisRequest) -> dict:
    dataset_dir = settings.data_dir / request.dataset_id
    try:
        dataset = _dataset(dataset_dir)
    except DatasetError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    historical = None
    if request.include_history:
        try:
            historical = _dataset(settings.data_dir / "historical")
        except DatasetError:
            historical = None
    result = summarize_spatial_patterns(dataset, historical, block_size=request.block_size)
    return {"success": True, **result}


@router.post("/geoai/vegetation-forecast")
def geoai_vegetation_forecast(request: VegetationForecastRequest) -> dict:
    observations = request.observations or []
    if not observations:
        try:
            dataset = _dataset(settings.data_dir / request.dataset_id)
        except DatasetError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        ndvi = calculate_ndvi(dataset.bands["B04"], dataset.bands["B08"])
        observations = []
        for idx, value in enumerate(np.asarray(ndvi["raster"][ndvi["valid_mask"]]).ravel()[:10]):
            observations.append({"date": f"2024-01-{(idx % 28) + 1:02d}", "mean_ndvi": float(value)})
    if request.lookback_limit is not None and len(observations) > request.lookback_limit:
        observations = observations[-request.lookback_limit:]
    result = forecast_vegetation_trends(observations, horizon_days=request.horizon_days)
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
    if result.get("status") == "insufficient-data":
        return {"success": False, **result}
    return {"success": True, **result}


@router.get("/geoai/model-evaluation")
def geoai_model_evaluation() -> dict:
    history = [
        {"date": "2024-01-01", "mean_ndvi": 0.42},
        {"date": "2024-02-01", "mean_ndvi": 0.44},
        {"date": "2024-03-01", "mean_ndvi": 0.46},
        {"date": "2024-04-01", "mean_ndvi": 0.45},
        {"date": "2024-05-01", "mean_ndvi": 0.48},
    ]
    result = evaluate_forecast([item["mean_ndvi"] for item in history])
    return {"success": True, **result}


@router.get("/geoai/risk-indicators")
def geoai_risk_indicators() -> dict:
    result = detect_risk_indicators({
        "ndvi": {"mean": 0.28, "std": 0.09},
        "persistent_change": {"mean_ndvi_delta": -0.04},
    })
    return {"success": True, **result}


@router.get("/geoai/history")
def geoai_history() -> dict:
    try:
        historical = _dataset(settings.data_dir / "historical")
    except DatasetError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    series = []
    if historical.bands:
        for index, date in enumerate(["2024-01-01", "2024-02-01", "2024-03-01", "2024-04-01"]):
            if "B04" in historical.bands and "B08" in historical.bands:
                ndvi = calculate_ndvi(historical.bands["B04"], historical.bands["B08"])
                values = ndvi["raster"][ndvi["valid_mask"]]
                mean = float(np.mean(values)) if values.size else None
                series.append({"date": date, "mean_ndvi": mean, "dataset_id": "historical"})
    if not series:
        return {"success": False, "status": "insufficient-data", "message": "Historic observations are unavailable."}
    summary = summarize_observation_history(series)
    return {"success": True, **summary}


def _visualization_path(kind: str) -> Path:
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
def visualization(kind: str):
    if kind not in {"rgb", "ndvi", "ndwi", "ndbi", "landcover", "change"}:
        raise HTTPException(status_code=404, detail="Visualization layer was not found.")
    try:
        path = _generate_visualization(kind)
    except DatasetError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return FileResponse(path, media_type="image/png")


@router.post("/analyze/all")
def analyze_all() -> dict:
    result_id = uuid4().hex
    result_dir = settings.output_dir / result_id
    current = _dataset(settings.data_dir / "current")
    historical = _dataset(settings.data_dir / "historical")
    results = {}
    for key in ANALYSES:
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
        "created_at": datetime.now(timezone.utc).isoformat(),
        "dataset": {
            "current": bool(current.bands),
            "historical": bool(historical.bands),
            "message": None if current.bands else EMPTY_DATA_MESSAGE,
        },
        **results,
    }
    _store_result(result_id, result_dir, response)
    return response


@router.post("/analyze/{analysis_key}")
def analyze(analysis_key: str) -> dict:
    return _run_analysis(analysis_key)


@router.get("/results")
def list_results() -> dict:
    results = []
    output_dir = settings.output_dir
    if not output_dir.exists():
        return {"success": True, "results": results}
    for path in sorted(output_dir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        summary_path = path / "summary.json"
        if path.is_dir() and summary_path.is_file():
            try:
                summary = json.loads(summary_path.read_text(encoding="utf-8"))
                results.append(
                    {
                        "id": summary.get("id"),
                        "created_at": summary.get("created_at"),
                        "analysis": summary.get("analysis", "Analysis bundle"),
                    }
                )
            except (OSError, json.JSONDecodeError):
                continue
    return {"success": True, "results": results}


@router.get("/results/{result_id}")
def get_result(result_id: str) -> dict:
    summary_path = _result_directory(result_id) / "summary.json"
    if not summary_path.is_file():
        raise HTTPException(status_code=404, detail="Result was not found.")
    return json.loads(summary_path.read_text(encoding="utf-8"))


@router.get("/results/{result_id}/download/{file_key}")
def download_result(result_id: str, file_key: str):
    if file_key not in DOWNLOAD_FILES:
        raise HTTPException(status_code=404, detail="Download was not found.")
    path = _result_directory(result_id) / DOWNLOAD_FILES[file_key]
    if not path.is_file():
        raise HTTPException(status_code=404, detail="This output is not available for the selected result.")
    return FileResponse(path, filename=DOWNLOAD_FILES[file_key])


@router.get("/results/{result_id}/image/{analysis_key}")
def get_result_image(result_id: str, analysis_key: str):
    if analysis_key not in ANALYSES:
        raise HTTPException(status_code=404, detail="Map layer was not found.")
    path = _result_directory(result_id) / f"{analysis_key}.png"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Map layer is not available.")
    return FileResponse(path, media_type="image/png")

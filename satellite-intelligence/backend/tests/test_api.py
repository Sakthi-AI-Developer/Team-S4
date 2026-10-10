from dataclasses import replace
import json
import os

import httpx
import numpy as np
import rasterio
from rasterio.io import MemoryFile
from rasterio.transform import from_origin
from fastapi.testclient import TestClient

import api.routes as routes
from config import settings
from main import app
from persistence.manager import PersistenceManager
from processing.data_provider import LocalDataProvider

client = TestClient(app)


def configure_roots(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    for name in ("current", "historical", "outputs"):
        (data_dir / name).mkdir(parents=True)
    test_settings = replace(settings, data_dir=data_dir, output_dir=data_dir / "outputs")
    monkeypatch.setattr(routes, "settings", test_settings)
    monkeypatch.setattr(routes, "data_provider", LocalDataProvider(data_dir))
    monkeypatch.setattr(routes, "persistence_manager", PersistenceManager(test_settings))
    return data_dir


def test_api_health():
    response = client.get("/api/health", headers={"X-Request-ID": "health-check-123"})
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.headers["x-request-id"] == "health-check-123"


def test_readiness_returns_503_without_exposing_dependency_errors(tmp_path, monkeypatch):
    from sqlalchemy.exc import SQLAlchemyError

    from persistence.artifacts import SupabaseArtifactStore
    from persistence.database import PersistenceRepository

    cloud_settings = replace(
        settings,
        database_url="postgresql+psycopg://configured",
        supabase_url="https://project.example.test",
        supabase_anon_key="public-key",
        supabase_service_role_key="private-key",
        supabase_storage_bucket="test-private-bucket",
        require_auth=True,
    )
    repository = PersistenceRepository(f"sqlite:///{tmp_path / 'health.sqlite'}")

    def fail_database_connection():
        raise SQLAlchemyError("postgres://user:password@private-host")

    def fail_storage_request(_request):
        return httpx.Response(503, text="service-role-key and signed-url must not be returned")

    monkeypatch.setattr(repository, "check_connection", fail_database_connection)
    storage_client = httpx.Client(transport=httpx.MockTransport(fail_storage_request))
    storage = SupabaseArtifactStore(
        "https://project.example.test",
        "private-key",
        "test-private-bucket",
        client=storage_client,
    )
    manager = PersistenceManager(
        cloud_settings,
        repository=repository,
        artifact_store=storage,
    )
    monkeypatch.setattr(routes, "persistence_manager", manager)
    monkeypatch.setattr(routes, "validate_runtime_settings", lambda: [])

    health = client.get("/api/health")
    readiness = client.get("/api/ready")

    assert health.status_code == 200
    assert readiness.status_code == 503
    assert readiness.json()["status"] == "unavailable"
    assert readiness.json()["checks"] == {
        "configuration": "ok",
        "database": "unavailable",
        "storage": "unavailable",
    }
    serialized = readiness.text
    assert "password" not in serialized
    assert "private-host" not in serialized
    assert "private-key" not in serialized
    assert "signed-url" not in serialized

def test_production_frontend_cors_preflight():
    origin = "https://team-s4-ten.vercel.app"
    response = client.options(
        "/api/health",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "accept,idempotency-key",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == origin
    assert "idempotency-key" in response.headers["access-control-allow-headers"].lower()


def test_production_frontend_cors_health_request():
    origin = "https://team-s4-ten.vercel.app"
    response = client.get("/api/health", headers={"Origin": origin})
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == origin
    assert response.headers["access-control-expose-headers"] == "X-Request-ID"


def test_validation_errors_do_not_echo_input_values():
    private_marker = "private-input-marker"
    response = client.post(
        "/api/satellite/search",
        json={
            "aoi": {"type": "Polygon", "coordinates": []},
            "start_date": {"value": private_marker},
            "end_date": "2024-01-01",
        },
    )

    assert response.status_code == 422
    assert private_marker not in response.text
    assert response.json()["detail"] == "Request validation failed."


def test_unhandled_error_responses_preserve_cors_for_allowed_frontend_origin(monkeypatch):
    origin = "https://team-s4-ten.vercel.app"

    def fail_readiness(_configuration_issues):
        raise RuntimeError("Simulated internal failure.")

    monkeypatch.setattr(routes.persistence_manager, "readiness", fail_readiness)
    api_client = TestClient(app, raise_server_exceptions=False)

    response = api_client.get("/api/ready", headers={"Origin": origin})

    assert response.status_code == 500
    assert response.headers["access-control-allow-origin"] == origin


def test_dataset_endpoint_reports_empty_current_data(tmp_path, monkeypatch):
    configure_roots(tmp_path, monkeypatch)
    response = client.get("/api/dataset")
    assert response.status_code == 200
    assert response.json()["current"]["available"] is False
    assert "backend/data/current" in response.json()["message"]


def test_invalid_analysis_is_rejected():
    response = client.post("/api/analyze/not-a-real-analysis")
    assert response.status_code == 404
    assert response.json()["detail"] == "The requested analysis is not available."


def test_analyze_all_reports_unavailable_analyses_without_crashing(tmp_path, monkeypatch):
    configure_roots(tmp_path, monkeypatch)
    response = client.post("/api/analyze/all")
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["dataset"]["current"] is False
    assert all(body[key]["success"] is False for key in ("ndvi", "ndwi", "ndbi", "landcover", "change_detection"))


def write_test_dataset(directory, reflectance):
    directory.mkdir(parents=True, exist_ok=True)
    bands = {
        "B02": 0.1,
        "B03": 0.3,
        "B04": reflectance,
        "B08": 0.6,
        "B11": 0.7,
    }
    for code, value in bands.items():
        with rasterio.open(
            directory / f"{code}.tif",
            "w",
            driver="GTiff",
            height=2,
            width=2,
            count=1,
            dtype="float32",
            crs="EPSG:32644",
            transform=from_origin(500000, 3000000, 10, 10),
            nodata=-9999,
        ) as output:
            output.write(np.full((2, 2), value, dtype=np.float32), 1)


def make_geotiff_bytes(width=2, height=2, crs="EPSG:32644", value=0.25):
    memory_file = MemoryFile()
    with memory_file.open(
        driver="GTiff",
        width=width,
        height=height,
        count=1,
        dtype="float32",
        crs=crs,
        transform=from_origin(500000, 3000000, 10, 10),
        nodata=-9999,
    ) as raster:
        raster.write(np.full((height, width), value, dtype=np.float32), 1)
    data = memory_file.read()
    memory_file.close()
    return data


def test_upload_band_saves_geotiff_and_refreshes_dataset(tmp_path, monkeypatch):
    data_dir = configure_roots(tmp_path, monkeypatch)
    response = client.post(
        "/api/dataset/current/upload",
        files={"file": ("S2_B04_2026.tif", make_geotiff_bytes(), "image/tiff")},
    )
    assert response.status_code == 201
    assert response.json()["band"] == "B04"
    assert (data_dir / "current" / "B04.tif").is_file()
    dataset = client.get("/api/dataset").json()
    assert dataset["current"]["bands"][0]["code"] == "B04"


def test_upload_rejects_duplicate_without_replacing_band(tmp_path, monkeypatch):
    data_dir = configure_roots(tmp_path, monkeypatch)
    destination = data_dir / "current" / "B04.tif"
    original = make_geotiff_bytes(value=0.2)
    destination.write_bytes(original)
    response = client.post(
        "/api/dataset/current/upload",
        files={"file": ("replacement_B04.tif", make_geotiff_bytes(value=0.9), "image/tiff")},
    )
    assert response.status_code == 409
    assert destination.read_bytes() == original


def test_upload_rejects_invalid_filename_extension_and_corrupt_raster(tmp_path, monkeypatch):
    configure_roots(tmp_path, monkeypatch)
    invalid_band = client.post(
        "/api/dataset/current/upload",
        files={"file": ("image.tif", make_geotiff_bytes(), "image/tiff")},
    )
    assert invalid_band.status_code == 422
    unsupported = client.post(
        "/api/dataset/current/upload",
        files={"file": ("B04.png", b"not a TIFF", "image/png")},
    )
    assert unsupported.status_code == 415
    corrupt = client.post(
        "/api/dataset/current/upload",
        files={"file": ("B04.tif", b"not a TIFF", "image/tiff")},
    )
    assert corrupt.status_code == 422


def test_upload_rejects_georeferencing_mismatch(tmp_path, monkeypatch):
    data_dir = configure_roots(tmp_path, monkeypatch)
    (data_dir / "current" / "B02.tif").write_bytes(make_geotiff_bytes())
    response = client.post(
        "/api/dataset/current/upload",
        files={"file": ("B04.tif", make_geotiff_bytes(width=1, height=1), "image/tiff")},
    )
    assert response.status_code == 422
    assert "dimensions differ" in response.json()["detail"]
    assert not (data_dir / "current" / "B04.tif").exists()


def test_upload_rejects_files_over_configured_limit(tmp_path, monkeypatch):
    data_dir = configure_roots(tmp_path, monkeypatch)
    monkeypatch.setattr(routes, "settings", replace(routes.settings, max_upload_bytes=10))
    response = client.post(
        "/api/dataset/current/upload",
        files={"file": ("B04.tif", make_geotiff_bytes(), "image/tiff")},
    )
    assert response.status_code == 413
    assert not list((data_dir / "current").glob("*.tif"))


def test_upload_rejects_rasters_over_pixel_limit(tmp_path, monkeypatch):
    data_dir = configure_roots(tmp_path, monkeypatch)
    monkeypatch.setattr(routes, "settings", replace(routes.settings, max_raster_pixels=3))

    response = client.post(
        "/api/dataset/current/upload",
        files={"file": ("B04.tif", make_geotiff_bytes(width=2, height=2), "image/tiff")},
    )

    assert response.status_code == 413
    assert "configured limit is 3 pixels" in response.json()["detail"]
    assert not list((data_dir / "current").glob("*.tif"))


def test_upload_rejects_source_file_over_raster_file_limit(tmp_path, monkeypatch):
    data_dir = configure_roots(tmp_path, monkeypatch)
    monkeypatch.setattr(routes, "settings", replace(routes.settings, max_raster_bytes=10))

    response = client.post(
        "/api/dataset/current/upload",
        files={"file": ("B04.tif", make_geotiff_bytes(), "image/tiff")},
    )

    assert response.status_code == 413
    assert "raster file-size limit" in response.json()["detail"]
    assert not list((data_dir / "current").glob("*.tif"))


def test_analysis_admission_returns_retryable_backpressure(tmp_path, monkeypatch):
    from threading import BoundedSemaphore

    data_dir = configure_roots(tmp_path, monkeypatch)
    slots = BoundedSemaphore(1)
    assert slots.acquire(blocking=False)
    monkeypatch.setattr(routes, "analysis_slots", slots)

    response = client.post("/api/analyze/ndvi", headers={"Idempotency-Key": "busy-test-key-123"})

    assert response.status_code == 503
    assert response.headers["retry-after"] == "1"
    assert "capacity is busy" in response.json()["detail"]
    assert not list((data_dir / "outputs").iterdir())


def test_expired_analysis_deadline_fails_without_success_artifacts(tmp_path, monkeypatch):
    data_dir = configure_roots(tmp_path, monkeypatch)
    monkeypatch.setattr(
        routes,
        "settings",
        replace(routes.settings, max_analysis_seconds=1),
    )
    clock_values = iter((100.0, 102.0))
    monkeypatch.setattr(routes, "monotonic", lambda: next(clock_values))

    response = client.post("/api/analyze/ndvi")

    assert response.status_code == 504
    assert response.json()["detail"] == "Analysis exceeded its configured processing time limit."
    assert response.headers.get("x-processing-job-id")
    assert not list((data_dir / "outputs").iterdir())


def test_resource_limits_reject_invalid_values(tmp_path, monkeypatch):
    import config as runtime_config

    invalid_settings = replace(
        settings,
        data_dir=tmp_path / "data",
        output_dir=tmp_path / "outputs",
        satellite_cache_dir=tmp_path / "cache",
        max_raster_bytes=0,
        max_raster_pixels=0,
        max_input_array_bytes=0,
        max_analysis_seconds=3601,
        max_concurrent_analyses=0,
        database_pool_size=10,
        database_max_overflow=1,
    )
    monkeypatch.setattr(runtime_config, "settings", invalid_settings)

    issues = runtime_config.validate_runtime_settings()

    assert any("MAX_RASTER_PIXELS must be positive" in issue for issue in issues)
    assert any("MAX_RASTER_BYTES must be positive" in issue for issue in issues)
    assert any("MAX_INPUT_ARRAY_BYTES must be positive" in issue for issue in issues)
    assert any("MAX_ANALYSIS_SECONDS must be between 1 and 3600" in issue for issue in issues)
    assert any("MAX_CONCURRENT_ANALYSES must be between 1 and 8" in issue for issue in issues)
    assert any("must not exceed 10 connections per process" in issue for issue in issues)


def test_results_are_paginated_and_recent_page_is_bounded(tmp_path, monkeypatch):
    data_dir = configure_roots(tmp_path, monkeypatch)
    for ordinal in range(1, 4):
        result_id = str(ordinal) * 32
        directory = data_dir / "outputs" / result_id
        directory.mkdir()
        (directory / "summary.json").write_text(
            json.dumps(
                {
                    "id": result_id,
                    "analysis": f"Analysis {ordinal}",
                    "created_at": f"2026-01-0{ordinal}T00:00:00+00:00",
                }
            ),
            encoding="utf-8",
        )
        os.utime(directory, (ordinal, ordinal))

    first = client.get("/api/results?limit=2")
    second = client.get("/api/results?limit=2&offset=2")
    too_many = client.get("/api/results?limit=101")

    assert first.status_code == 200
    assert [item["id"] for item in first.json()["results"]] == ["3" * 32, "2" * 32]
    assert first.json()["has_more"] is True
    assert first.json()["next_offset"] == 2
    assert [item["id"] for item in second.json()["results"]] == ["1" * 32]
    assert second.json()["has_more"] is False
    assert second.json()["next_offset"] is None
    assert too_many.status_code == 422


def test_analyze_all_writes_georeferenced_outputs(tmp_path, monkeypatch):
    data_dir = configure_roots(tmp_path, monkeypatch)
    current_dir = data_dir / "current"
    historical_dir = data_dir / "historical"
    output_dir = data_dir / "outputs"
    write_test_dataset(current_dir, 0.2)
    write_test_dataset(historical_dir, 0.3)

    response = client.post("/api/analyze/all")

    assert response.status_code == 200
    result = response.json()
    for key in ("ndvi", "ndwi", "ndbi", "landcover", "change_detection"):
        assert result[key]["success"] is True
    result_id = result["id"]
    raster_response = client.get(f"/api/results/{result_id}/download/ndvi")
    assert raster_response.status_code == 200
    image_response = client.get(f"/api/results/{result_id}/image/ndvi")
    assert image_response.status_code == 200
    assert image_response.headers["content-type"] == "image/png"
    assert result["ndvi"]["bounds"] is not None
    raster_path = output_dir / result_id / "ndvi.tif"
    with rasterio.open(raster_path) as raster:
        assert raster.crs.to_string() == "EPSG:32644"
        assert raster.transform == from_origin(500000, 3000000, 10, 10)
        np.testing.assert_allclose(raster.read(1), np.full((2, 2), 0.5), atol=1e-6)
    assert client.get(f"/api/results/{result_id}").status_code == 200


def test_analyze_all_idempotency_key_prevents_reprocessing(tmp_path, monkeypatch):
    from persistence.database import PersistenceRepository

    data_dir = configure_roots(tmp_path, monkeypatch)
    write_test_dataset(data_dir / "current", 0.2)
    write_test_dataset(data_dir / "historical", 0.3)
    repository = PersistenceRepository(f"sqlite:///{tmp_path / 'idempotency.sqlite'}")
    repository.create_schema_for_tests()
    manager = PersistenceManager(routes.settings, repository=repository)
    monkeypatch.setattr(routes, "persistence_manager", manager)
    headers = {"Idempotency-Key": "repeat-complete-analysis-123"}

    first = client.post("/api/analyze/all", headers=headers)
    second = client.post("/api/analyze/all", headers=headers)

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["id"] == first.json()["id"]
    assert second.json()["job_id"] == first.json()["job_id"]
    assert [item["id"] for item in repository.list_analyses(limit=10)] == [first.json()["id"]]
    result_directories = [
        path for path in (data_dir / "outputs").iterdir()
        if path.name == first.json()["id"]
    ]
    assert len(result_directories) == 1
    repository.close()


def test_change_analysis_route_aliases_to_change_detection(tmp_path, monkeypatch):
    configure_roots(tmp_path, monkeypatch)
    response = client.post("/api/analyze/change")
    assert response.status_code == 422
    assert "Historical imagery" in response.json()["detail"]

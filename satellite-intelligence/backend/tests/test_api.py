from dataclasses import replace

import numpy as np
import rasterio
from rasterio.io import MemoryFile
from rasterio.transform import from_origin
from fastapi.testclient import TestClient

import api.routes as routes
from config import settings
from main import app
from processing.data_provider import LocalDataProvider

client = TestClient(app)


def configure_roots(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    for name in ("current", "historical", "outputs"):
        (data_dir / name).mkdir(parents=True)
    monkeypatch.setattr(
        routes,
        "settings",
        replace(settings, data_dir=data_dir, output_dir=data_dir / "outputs"),
    )
    monkeypatch.setattr(routes, "data_provider", LocalDataProvider(data_dir))
    return data_dir


def test_api_health():
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"

def test_production_frontend_cors_preflight():
    origin = "https://team-s4-ten.vercel.app"
    response = client.options(
        "/api/health",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "accept",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == origin


def test_production_frontend_cors_health_request():
    origin = "https://team-s4-ten.vercel.app"
    response = client.get("/api/health", headers={"Origin": origin})
    assert response.status_code == 200
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


def test_change_analysis_route_aliases_to_change_detection(tmp_path, monkeypatch):
    configure_roots(tmp_path, monkeypatch)
    response = client.post("/api/analyze/change")
    assert response.status_code == 422
    assert "Historical imagery" in response.json()["detail"]

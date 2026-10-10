from dataclasses import replace
from datetime import date

import pytest
import rasterio
from fastapi.testclient import TestClient

import api.routes as routes
import satellite.live_provider as live_provider
from config import settings
from main import app
from satellite.cache import CacheManager
from satellite.mock_provider import MockSatelliteProvider
from satellite.models import GeoJSONPolygon, SatelliteSearchRequest

client = TestClient(app)


def _sample_aoi() -> dict:
    return {
        "type": "Polygon",
        "coordinates": [[
            [-1.0, 50.0],
            [-1.0, 51.0],
            [0.0, 51.0],
            [0.0, 50.0],
            [-1.0, 50.0],
        ]],
    }


def _configure_provider(monkeypatch, tmp_path, provider_name: str = "mock"):
    data_dir = tmp_path / "data"
    for name in ("current", "historical"):
        (data_dir / name).mkdir(parents=True)
    cache_dir = data_dir / "live"
    cache_dir.mkdir(parents=True)
    monkeypatch.setattr(
        routes,
        "settings",
        replace(
            settings,
            data_dir=data_dir,
            output_dir=data_dir / "outputs",
            satellite_provider=provider_name,
            copernicus_client_id=None,
            copernicus_client_secret=None,
            satellite_cache_dir=cache_dir,
        ),
    )
    monkeypatch.setattr(routes, "cache_manager", CacheManager(cache_dir))
    return data_dir


def test_aoi_validation_accepts_valid_polygon():
    polygon = GeoJSONPolygon(**_sample_aoi())
    assert polygon.bounds == (-1.0, 50.0, 0.0, 51.0)


def test_aoi_validation_rejects_unclosed_polygon():
    bad = {"type": "Polygon", "coordinates": [[[-1.0, 50.0], [-1.0, 51.0], [0.0, 51.0], [0.0, 50.0]]]}
    with pytest.raises(ValueError, match="closed"):
        GeoJSONPolygon(**bad)


def test_search_request_rejects_inverted_dates():
    with pytest.raises(ValueError, match="start_date"):
        SatelliteSearchRequest(
            aoi=GeoJSONPolygon(**_sample_aoi()),
            start_date=date.fromisoformat("2024-05-02"),
            end_date=date.fromisoformat("2024-05-01"),
            max_cloud_cover=20,
        )


def test_mock_provider_search_and_download(tmp_path):
    cache_dir = tmp_path / "cache"
    provider = MockSatelliteProvider(CacheManager(cache_dir))
    results = provider.search(None)
    assert results and results[0]["provider"] == "mock"
    assert results[0]["metadata"]["data_classification"] == "synthetic"
    assert results[0]["acquisition_date"] is None
    assert results[0]["cloud_cover"] is None
    download = provider.download(results[0]["product_id"])
    assert download["success"] is True
    assert download["data_classification"] == "synthetic"
    assert (cache_dir / results[0]["product_id"] / "B04.tif").exists()
    with rasterio.open(cache_dir / results[0]["product_id"] / "B04.tif") as raster:
        assert raster.crs.to_string().startswith('LOCAL_CS["Synthetic demo grid"')
        assert raster.tags()["SATELLITE_VISION_DATA_KIND"] == "synthetic"


def test_cache_manager_rejects_escape(tmp_path):
    manager = CacheManager(tmp_path / "safe")
    with pytest.raises(ValueError, match="escape"):
        manager.product_dir("../outside")


def test_satellite_search_endpoint_returns_503_when_live_provider_unconfigured(tmp_path, monkeypatch):
    _configure_provider(monkeypatch, tmp_path, provider_name="live")
    response = client.post(
        "/api/satellite/search",
        json={
            "aoi": _sample_aoi(),
            "start_date": "2024-01-01",
            "end_date": "2024-01-10",
            "max_cloud_cover": 20,
        },
    )
    assert response.status_code == 503
    assert "configured" in response.json()["detail"].lower()


def test_configured_live_provider_does_not_claim_unimplemented_search_succeeded(
    tmp_path, monkeypatch
):
    _configure_provider(monkeypatch, tmp_path, provider_name="live")
    monkeypatch.setattr(
        live_provider,
        "settings",
        replace(
            live_provider.settings,
            copernicus_client_id="configured-client-id",
            copernicus_client_secret="configured-client-secret",
        ),
    )

    response = client.post(
        "/api/satellite/search",
        json={
            "aoi": _sample_aoi(),
            "start_date": "2024-01-01",
            "end_date": "2024-01-10",
            "max_cloud_cover": 20,
        },
    )

    assert response.status_code == 501
    assert "not implemented" in response.json()["detail"]


def test_satellite_download_endpoint_uses_mock_provider(tmp_path, monkeypatch):
    _configure_provider(monkeypatch, tmp_path, provider_name="mock")
    search_response = client.post(
        "/api/satellite/search",
        json={
            "aoi": _sample_aoi(),
            "start_date": "2024-01-01",
            "end_date": "2024-01-10",
            "max_cloud_cover": 20,
        },
    )
    product_id = search_response.json()["results"][0]["product_id"]
    response = client.post("/api/satellite/download", json={"product_id": product_id})
    assert response.status_code == 200
    assert response.json()["download"]["success"] is True

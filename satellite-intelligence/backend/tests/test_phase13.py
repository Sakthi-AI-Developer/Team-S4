from dataclasses import replace

import numpy as np
import pytest
import rasterio
from fastapi.testclient import TestClient
from rasterio.transform import from_origin

import api.routes as routes
import auth
from config import settings
from geoai.spatial_analysis import _summarize_index
from main import app
from persistence.manager import PersistenceManager
from processing.data_provider import LocalDataProvider
from processing.preprocessing import raster_metadata
from satellite.models import GeoJSONPolygon

client = TestClient(app)


def configure_data(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    for name in ("current", "historical", "outputs"):
        (data_dir / name).mkdir(parents=True)
    test_settings = replace(
        settings,
        data_dir=data_dir,
        output_dir=data_dir / "outputs",
        database_url=None,
        supabase_url=None,
        supabase_service_role_key=None,
        supabase_anon_key=None,
        require_auth=False,
    )
    monkeypatch.setattr(routes, "settings", test_settings)
    monkeypatch.setattr(routes, "data_provider", LocalDataProvider(data_dir))
    monkeypatch.setattr(
        routes, "persistence_manager", PersistenceManager(test_settings)
    )
    monkeypatch.setattr(
        auth,
        "settings",
        replace(
            auth.settings,
            database_url=None,
            supabase_url=None,
            supabase_service_role_key=None,
            supabase_anon_key=None,
            require_auth=False,
        ),
    )
    return data_dir


def write_dataset(directory, red, nir, acquisition_date=None, synthetic=True):
    directory.mkdir(parents=True, exist_ok=True)
    reflectance = {
        "B02": 0.1,
        "B03": 0.3,
        "B04": red,
        "B08": nir,
        "B11": 0.7,
    }
    for code, value in reflectance.items():
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
            tags = {"SOURCE": "synthetic test fixture"}
            if acquisition_date is not None:
                tags["ACQUISITION_DATE"] = acquisition_date
            if synthetic:
                tags["SATELLITE_VISION_DATA_KIND"] = "synthetic"
            output.update_tags(**tags)


def test_metadata_tagged_observations_are_used_without_invented_dates(
    tmp_path, monkeypatch
):
    data_dir = configure_data(tmp_path, monkeypatch)
    write_dataset(data_dir / "historical", red=0.4, nir=0.5, acquisition_date="2022-04-05")
    write_dataset(data_dir / "current", red=0.2, nir=0.6, acquisition_date="2024-04-05")

    history = client.get("/api/geoai/history").json()
    forecast = client.post("/api/geoai/vegetation-forecast", json={}).json()
    evaluation = client.get("/api/geoai/model-evaluation").json()

    assert history["success"] is True
    assert history["data_classification"] == "synthetic"
    assert history["series"][0]["date"] == "2022-04-05"
    assert history["series"][1]["date"] == "2024-04-05"
    assert forecast["success"] is True
    assert forecast["training_observation_count"] == 2
    assert forecast["prediction_interval"]["support"] is False
    assert forecast["evaluation"]["status"] == "insufficient-data"
    assert evaluation["success"] is False
    assert evaluation["metrics"] == {}


def test_untagged_inputs_do_not_become_temporal_observations(tmp_path, monkeypatch):
    data_dir = configure_data(tmp_path, monkeypatch)
    write_dataset(data_dir / "historical", red=0.4, nir=0.5, synthetic=False)
    write_dataset(data_dir / "current", red=0.2, nir=0.6, synthetic=False)

    history = client.get("/api/geoai/history").json()
    forecast = client.post("/api/geoai/vegetation-forecast", json={}).json()

    assert history["success"] is False
    assert history["status"] == "insufficient-data"
    assert history["series"] == []
    assert any("no valid acquisition date" in item for item in history["observation_warnings"])
    assert forecast["success"] is False
    assert "forecast_dates" not in forecast


def test_risk_screening_uses_current_raster_evidence_not_constants(
    tmp_path, monkeypatch
):
    data_dir = configure_data(tmp_path, monkeypatch)
    write_dataset(data_dir / "current", red=0.2, nir=0.6, synthetic=False)

    response = client.get("/api/geoai/risk-indicators")

    assert response.status_code == 200
    result = response.json()
    assert result["success"] is True
    assert result["count"] == 0
    assert result["indicators"] == []
    assert result["evidence_sources"]["current"]["acquisition_date"] is None
    assert any(
        "Acquisition date is not recorded"
        in warning
        for warning in result["evidence_sources"]["current"]["quality_warnings"]
    )


def test_analysis_response_carries_input_provenance(tmp_path, monkeypatch):
    data_dir = configure_data(tmp_path, monkeypatch)
    write_dataset(data_dir / "current", red=0.2, nir=0.6, acquisition_date="2025-07-18")

    response = client.post("/api/analyze/ndvi")

    assert response.status_code == 200
    result = response.json()["result"]
    assert result["provenance"]["data_classification"] == "synthetic"
    source = result["provenance"]["datasets"]["current"]
    assert source["acquisition_date"] == "2025-07-18"
    assert source["platform"] is None
    assert source["sensor"] is None
    assert source["bands"]["B04"]["width"] == 2
    assert "ndvi-v1" == result["provenance"]["processing"]["version"]


def test_demo_is_deterministic_synthetic_and_does_not_persist_user_results(
    tmp_path, monkeypatch
):
    data_dir = configure_data(tmp_path, monkeypatch)

    response = client.get("/api/geoai/demo")

    assert response.status_code == 200
    result = response.json()
    assert result["data_classification"] == "synthetic"
    assert result["acquisition_date"] is None
    assert result["geographic_coverage"] is None
    assert "not satellite imagery" in result["message"]
    assert result["results"]["ndvi"]["statistics"]["valid_pixels"] == 16
    assert result["provenance"]["ndvi"]["data_classification"] == "synthetic"
    assert not list((data_dir / "outputs").iterdir())


def test_demo_endpoint_still_requires_auth_when_authentication_is_enabled(
    monkeypatch,
):
    monkeypatch.setattr(
        auth,
        "settings",
        replace(
            auth.settings,
            database_url="postgresql://configured",
            supabase_url="https://project.example.test",
            supabase_anon_key="public-key",
            supabase_service_role_key="server-only",
            require_auth=True,
        ),
    )

    response = client.get("/api/geoai/demo")

    assert response.status_code == 401


def test_spatial_summary_covers_partial_edge_blocks():
    values = np.ones((21, 21), dtype=np.float32)
    result = _summarize_index("NDVI", values, np.ones_like(values, dtype=bool), 10)

    assert result["grid_cells"] == 9
    assert any(
        item["row"] == 20 and item["col"] == 20
        for item in result["hotspots"]
    )
    assert result["valid_pixels"] == 441


def test_spatial_analysis_does_not_silently_ignore_aoi(tmp_path, monkeypatch):
    configure_data(tmp_path, monkeypatch)
    response = client.post(
        "/api/geoai/spatial-analysis",
        json={
            "dataset_id": "current",
            "aoi": {
                "type": "Polygon",
                "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]],
            },
        },
    )

    assert response.status_code == 422
    assert "AOI clipping is not implemented" in response.json()["detail"]


def test_invalid_or_self_intersecting_aoi_is_rejected():
    self_intersecting = {
        "type": "Polygon",
        "coordinates": [[[0, 0], [1, 1], [0, 1], [1, 0], [0, 0]]],
    }
    with pytest.raises(ValueError, match="invalid"):
        GeoJSONPolygon(**self_intersecting)


def test_raster_metadata_omits_nonfinite_geographic_bounds():
    from processing.preprocessing import RasterBand
    from rasterio.transform import Affine

    band = RasterBand(
        code="B04",
        data=np.ones((1, 1), dtype=np.float32),
        valid=np.ones((1, 1), dtype=bool),
        profile={"nodata": None},
        crs="EPSG:4326",
        transform=Affine(float("nan"), 0, 0, 0, 1, 0),
        width=1,
        height=1,
        resolution=(1, 1),
    )

    metadata = raster_metadata(band)
    assert metadata is not None
    assert metadata["bounds"] is None

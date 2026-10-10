from dataclasses import replace
import json

import numpy as np
import pytest
import rasterio
from fastapi.testclient import TestClient
from pyproj import CRS
from rasterio.io import MemoryFile
from rasterio.transform import Affine, from_origin
from rasterio.warp import transform_bounds

import api.routes as routes
import main as application
import processing.satellite_analysis as satellite_analysis
from auth import AuthenticatedUser
from config import settings
from main import app
from persistence.artifacts import ArtifactStorageError, LocalArtifactStore
from persistence.database import PersistenceRepository
from persistence.manager import PersistenceManager
from processing.preprocessing import DatasetError
from processing.scene_change import run_ndvi_change_detection
from processing.satellite_analysis import run_satellite_analysis, validate_band_mapping


def scene_bytes(include_nir=True, *, acquisition_date=None, platform=None, sensor=None):
    bands = np.asarray(
        [
            [[2, 0, 1, 2], [2, 1, 2, 0], [3, 2, 1, 3], [0, 1, 3, 2]],
            [[8, 0, 3, 8], [6, 4, 8, 0], [3, 8, 5, 5], [0, 4, 6, 8]],
            [[4, 0, 1, 7], [3, 1, 6, 0], [8, 4, 2, 3], [0, 2, 7, 5]],
            [[1, 0, 8, 2], [9, 3, 1, 0], [2, 6, 4, 1], [0, 9, 3, 8]],
        ],
        dtype=np.float32,
    )
    bands[0, 1, 1] = -9999
    codes = ("B04", "B08", "B03", "B11")
    if not include_nir:
        bands = bands[[0, 2, 3]]
        codes = ("B04", "B03", "B11")
    with MemoryFile() as memory_file:
        with memory_file.open(
            driver="GTiff",
            width=4,
            height=4,
            count=len(codes),
            dtype="float32",
            nodata=-9999,
            crs="EPSG:4326",
            transform=from_origin(18, 13, 0.01, 0.01),
        ) as destination:
            destination.write(bands)
            for index, code in enumerate(codes, start=1):
                destination.set_band_description(index, code)
            destination.scales = (0.1,) * len(codes)
            destination.offsets = (0.1,) + (0.0,) * (len(codes) - 1)
            tags = {
                key: value
                for key, value in (
                    ("ACQUISITION_DATE", acquisition_date),
                    ("PLATFORM", platform),
                    ("SENSOR", sensor),
                )
                if value is not None
            }
            if tags:
                destination.update_tags(**tags)
        return memory_file.read()


@pytest.fixture
def analysis_backend(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    output_dir = data_dir / "outputs"
    output_dir.mkdir(parents=True)
    local_settings = replace(
        settings,
        data_dir=data_dir,
        output_dir=output_dir,
        database_url=None,
        supabase_url=None,
        supabase_anon_key=None,
        supabase_service_role_key=None,
        require_auth=False,
        max_imagery_pixels=100,
    )
    repository = PersistenceRepository(f"sqlite:///{tmp_path / 'analysis.sqlite'}")
    repository.create_schema_for_tests()
    manager = PersistenceManager(
        local_settings,
        repository=repository,
        artifact_store=LocalArtifactStore(output_dir),
    )
    monkeypatch.setattr(routes, "settings", local_settings)
    monkeypatch.setattr(routes, "persistence_manager", manager)
    monkeypatch.setattr(application, "settings", local_settings)
    app.dependency_overrides[routes.current_user] = lambda: AuthenticatedUser("owner-1")
    yield repository, manager, local_settings
    app.dependency_overrides.pop(routes.current_user, None)
    manager.close()


@pytest.fixture
def synthetic_scene(tmp_path):
    path = tmp_path / "scene.tif"
    bands = np.asarray(
        [
            [
                [2, 0, 1, 2],
                [2, 1, 2, 0],
                [3, 2, 1, 3],
                [0, 1, 3, 2],
            ],
            [
                [8, 0, 3, 8],
                [6, 4, 8, 0],
                [3, 8, 5, 5],
                [0, 4, 6, 8],
            ],
            [
                [4, 0, 1, 7],
                [3, 1, 6, 0],
                [8, 4, 2, 3],
                [0, 2, 7, 5],
            ],
            [
                [1, 0, 8, 2],
                [9, 3, 1, 0],
                [2, 6, 4, 1],
                [0, 9, 3, 8],
            ],
        ],
        dtype=np.float32,
    )
    bands[0, 1, 1] = -9999
    bands[1, 2, 2] = np.nan
    bands[1, 3, 3] = np.inf
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=4,
        height=4,
        count=4,
        dtype="float32",
        nodata=-9999,
        crs="EPSG:4326",
        transform=from_origin(18, 13, 0.01, 0.01),
    ) as destination:
        destination.write(bands)
        for index, code in enumerate(("B04", "B08", "B03", "B11"), start=1):
            destination.set_band_description(index, code)
        destination.scales = (0.1, 0.1, 0.1, 0.1)
        destination.offsets = (0.1, 0.0, 0.0, 0.0)
    metadata = {
        "band_count": 4,
        "bands": [
            {"index": index, "code": code}
            for index, code in enumerate(("B04", "B08", "B03", "B11"), start=1)
        ],
    }
    return path, metadata


def run_analysis(path, output, analysis, metadata, **kwargs):
    return run_satellite_analysis(
        path,
        output,
        analysis=analysis,
        scene_metadata=metadata,
        max_pixels=100,
        max_output_bytes=512 * 1024 * 1024,
        **kwargs,
    )


def test_ndvi_applies_scale_and_offset_masks_invalid_values_and_preserves_georeferencing(
    synthetic_scene, tmp_path
):
    source, metadata = synthetic_scene
    result = run_analysis(source, tmp_path / "ndvi", "ndvi", metadata)

    assert result["formula"] == "(NIR - Red) / (NIR + Red)"
    assert result["bands"] == {"red": 1, "nir": 2}
    assert result["statistics"]["valid_pixels"] == 13
    assert result["statistics"]["min"] == pytest.approx(-1.0)
    assert result["statistics"]["max"] > result["statistics"]["min"]
    assert "not a universal" in result["interpretation_note"]
    with rasterio.open(tmp_path / "ndvi" / "ndvi.tif") as output:
        assert output.crs.to_string() == "EPSG:4326"
        assert output.transform == from_origin(18, 13, 0.01, 0.01)
        assert (output.width, output.height) == (4, 4)
        values = output.read(1, masked=True)
        assert np.ma.count(values) == 13
        assert values[0, 0] == pytest.approx((0.8 - 0.3) / (0.8 + 0.3))
        assert np.ma.is_masked(values[1, 1])
        assert np.ma.is_masked(values[2, 2])
        assert np.ma.is_masked(values[3, 3])
        assert result["statistics"]["mean"] == pytest.approx(
            float(np.mean(values.compressed())),
            abs=1e-6,
        )
        assert result["statistics"]["median"] == pytest.approx(
            np.median(values.compressed()),
            abs=0.001,
        )
    assert (tmp_path / "ndvi" / "preview.png").is_file()


def test_mcfeeters_ndwi_uses_green_and_nir_bands(synthetic_scene, tmp_path):
    source, metadata = synthetic_scene
    result = run_analysis(source, tmp_path / "ndwi", "ndwi", metadata)

    assert result["formula"] == "(Green - NIR) / (Green + NIR)"
    assert result["bands"] == {"green": 3, "nir": 2}
    assert "McFeeters" in result["interpretation_note"]
    with rasterio.open(tmp_path / "ndwi" / "ndwi.tif") as output:
        assert output.read(1, masked=True)[0, 0] == pytest.approx((0.4 - 0.8) / (0.4 + 0.8))


def test_index_masks_zero_denominators(synthetic_scene, tmp_path):
    source_path, metadata = synthetic_scene
    zero_scale_path = tmp_path / "zero-scale.tif"
    with rasterio.open(source_path) as source:
        with rasterio.open(zero_scale_path, "w", **source.profile) as destination:
            destination.write(source.read())
            for index, description in enumerate(source.descriptions, start=1):
                destination.set_band_description(index, description)
            destination.scales = (1.0, 1.0, 1.0, 1.0)
            destination.offsets = (0.0, 0.0, 0.0, 0.0)

    result = run_analysis(
        zero_scale_path,
        tmp_path / "zero-denominators",
        "ndvi",
        metadata,
    )

    assert result["statistics"]["valid_pixels"] == 10
    with rasterio.open(tmp_path / "zero-denominators" / "ndvi.tif") as output:
        values = output.read(1, masked=True)
        assert np.ma.is_masked(values[0, 1])
        assert np.ma.is_masked(values[1, 3])
        assert np.ma.is_masked(values[3, 0])


def test_index_requires_unambiguous_band_identity_or_explicit_mapping(
    synthetic_scene, tmp_path
):
    source, metadata = synthetic_scene
    metadata["bands"] = [{"index": 1, "code": "B04"}]
    with rasterio.open(source) as raster:
        with pytest.raises(DatasetError, match="select the matching source band numbers"):
            validate_band_mapping(raster, "ndvi", metadata, None)
        assert validate_band_mapping(
            raster,
            "ndvi",
            metadata,
            {"red": 1, "nir": 2},
        ) == {"red": 1, "nir": 2}
        with pytest.raises(DatasetError, match="different source band"):
            validate_band_mapping(
                raster,
                "ndvi",
                metadata,
                {"red": 1, "nir": 1},
            )
        ambiguous_metadata = {
            "bands": [
                {"index": 1, "code": "B04"},
                {"index": 2, "code": "B08"},
                {"index": 4, "code": "B04"},
            ]
        }
        with pytest.raises(DatasetError, match="identity is ambiguous"):
            validate_band_mapping(raster, "ndvi", ambiguous_metadata, None)


def test_kmeans_is_deterministic_and_emits_neutral_georeferenced_classes(
    synthetic_scene, tmp_path, monkeypatch
):
    monkeypatch.setattr(satellite_analysis, "WINDOW_SIZE", 2)
    source, metadata = synthetic_scene
    first = run_analysis(
        source,
        tmp_path / "classes-a",
        "kmeans",
        metadata,
        feature_bands=[1, 2, 3],
        cluster_count=3,
        random_seed=7,
    )
    second = run_analysis(
        source,
        tmp_path / "classes-b",
        "kmeans",
        metadata,
        feature_bands=[1, 2, 3],
        cluster_count=3,
        random_seed=7,
    )

    assert [item["label"] for item in first["classes"]] == [
        "Class 1",
        "Class 2",
        "Class 3",
    ]
    assert sum(item["pixel_count"] for item in first["classes"]) == 13
    assert sum(item["proportion"] for item in first["classes"]) == pytest.approx(1.0)
    with rasterio.open(tmp_path / "classes-a" / "classification.tif") as classified:
        first_classes = classified.read(1)
        assert classified.crs.to_string() == "EPSG:4326"
        assert classified.transform == from_origin(18, 13, 0.01, 0.01)
        assert set(np.unique(first_classes)) <= {0, 1, 2, 3}
        assert np.count_nonzero(first_classes == 0) == 3
    with rasterio.open(tmp_path / "classes-b" / "classification.tif") as classified:
        np.testing.assert_array_equal(first_classes, classified.read(1))
    assert first["interpretation_note"] == second["interpretation_note"]


def test_analysis_rejects_oversized_raster_and_invalid_cluster_count(synthetic_scene, tmp_path):
    source, metadata = synthetic_scene
    with pytest.raises(DatasetError, match="analysis limit"):
        run_satellite_analysis(
            source,
            tmp_path / "too-large",
            analysis="ndvi",
            scene_metadata=metadata,
            max_pixels=10,
        )
    with pytest.raises(DatasetError, match="artifact size limit"):
        run_satellite_analysis(
            source,
            tmp_path / "artifact-too-large",
            analysis="ndvi",
            scene_metadata=metadata,
            max_pixels=100,
            max_output_bytes=20,
        )
    with pytest.raises(DatasetError, match="Cluster count"):
        run_satellite_analysis(
            source,
            tmp_path / "bad-clusters",
            analysis="kmeans",
            scene_metadata=metadata,
            cluster_count=1,
            max_pixels=100,
        )


def test_analysis_rejects_rasters_without_crs(synthetic_scene, tmp_path):
    source_path, metadata = synthetic_scene
    unreferenced_path = tmp_path / "unreferenced.tif"
    with rasterio.open(source_path) as source:
        profile = source.profile.copy()
        profile.pop("crs", None)
        with rasterio.open(unreferenced_path, "w", **profile) as destination:
            destination.write(source.read())
            for index, description in enumerate(source.descriptions, start=1):
                destination.set_band_description(index, description)
    with pytest.raises(DatasetError, match="no coordinate reference system"):
        run_analysis(unreferenced_path, tmp_path / "no-crs", "ndvi", metadata)


def test_analysis_warps_preview_bounds_to_geographic_coordinates(synthetic_scene, tmp_path):
    source_path, metadata = synthetic_scene
    projected_path = tmp_path / "projected.tif"
    with rasterio.open(source_path) as source:
        profile = source.profile.copy()
        profile.update(
            crs="EPSG:3857",
            transform=from_origin(2_000_000, 6_000_000, 10, 10),
        )
        with rasterio.open(projected_path, "w", **profile) as destination:
            destination.write(source.read())
            for index, description in enumerate(source.descriptions, start=1):
                destination.set_band_description(index, description)
            destination.scales = source.scales
            destination.offsets = source.offsets

    result = run_analysis(projected_path, tmp_path / "projected-result", "ndvi", metadata)

    assert result["crs"] == "EPSG:3857"
    west, south, east, north = result["bounds"]
    assert 17 < west < east < 19
    assert 46 < south < north < 49
    with rasterio.open(tmp_path / "projected-result" / "ndvi.tif") as output:
        assert output.crs.to_string() == "EPSG:3857"
        assert output.transform == from_origin(2_000_000, 6_000_000, 10, 10)


def test_scene_analysis_api_persists_and_serves_owner_scoped_artifacts(analysis_backend):
    repository, _manager, _local_settings = analysis_backend
    client = TestClient(app)
    ingested = client.post(
        "/api/imagery/ingest",
        files={"file": ("scene.tif", scene_bytes(), "image/tiff")},
    )
    assert ingested.status_code == 201
    scene_id = ingested.json()["id"]

    catalog = client.get(
        "/api/imagery/analysis-types",
        params={"scene_id": scene_id},
    )
    assert catalog.status_code == 200
    available_types = {item["id"]: item for item in catalog.json()["analysis_types"]}
    assert available_types["ndvi"]["available"] is True
    assert available_types["ndwi"]["available"] is True
    assert available_types["kmeans"]["available"] is True

    response = client.post(
        f"/api/imagery/scenes/{scene_id}/analyses",
        json={"analysis_type": "ndvi"},
    )
    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "completed"
    assert result["scene"]["id"] == scene_id
    assert result["result"]["statistics"]["valid_pixels"] == 15
    assert {item["artifact_name"] for item in result["artifacts"]} == {
        "ndvi.tif",
        "preview.png",
        "summary.json",
    }
    artifact_records = repository.get_artifacts(result["id"], "owner-1")
    assert {item["artifact_name"] for item in artifact_records} == {
        "ndvi.tif",
        "preview.png",
        "summary.json",
    }
    assert all(item["size_bytes"] > 0 for item in artifact_records)
    retrieved = client.get(f"/api/imagery/analyses/{result['id']}")
    raster = client.get(
        f"/api/results/{result['id']}/artifacts/ndvi.tif/download"
    )
    assert retrieved.status_code == 200
    assert retrieved.json()["result"]["statistics"] == result["result"]["statistics"]
    assert raster.status_code == 200
    assert raster.headers["content-type"] in {"image/tiff", "application/octet-stream"}

    duplicate = client.post(
        f"/api/imagery/scenes/{scene_id}/analyses",
        json={"analysis_type": "ndvi"},
    )
    assert duplicate.status_code == 200
    assert duplicate.json()["id"] == result["id"]
    assert duplicate.json()["duplicate"] is True

    classification = client.post(
        f"/api/imagery/scenes/{scene_id}/analyses",
        json={
            "analysis_type": "kmeans",
            "feature_bands": [1, 2, 3],
            "cluster_count": 3,
            "random_seed": 7,
        },
    )
    assert classification.status_code == 200
    assert classification.json()["status"] == "completed"
    assert sum(
        item["pixel_count"] for item in classification.json()["result"]["classes"]
    ) == 15

    app.dependency_overrides[routes.current_user] = lambda: AuthenticatedUser("owner-2")
    assert client.get(f"/api/imagery/analyses/{result['id']}").status_code == 404
    assert client.get(
        f"/api/results/{result['id']}/artifacts/ndvi.tif/download"
    ).status_code == 404
    assert client.post(
        f"/api/imagery/scenes/{scene_id}/analyses",
        json={"analysis_type": "ndvi"},
    ).status_code == 404
    app.dependency_overrides[routes.current_user] = lambda: AuthenticatedUser("owner-1")


def test_scene_analysis_reports_missing_bands_and_invalid_mapping(analysis_backend):
    _repository, _manager, _local_settings = analysis_backend
    client = TestClient(app)
    scene_id = client.post(
        "/api/imagery/ingest",
        files={"file": ("scene.tif", scene_bytes(), "image/tiff")},
    ).json()["id"]

    missing_band = client.post(
        f"/api/imagery/scenes/{scene_id}/analyses",
        json={"analysis_type": "ndvi", "band_mapping": {"red": 1, "nir": 65}},
    )
    assert missing_band.status_code == 422
    assert "between 1 and 4" in missing_band.json()["detail"]
    wrong_roles = client.post(
        f"/api/imagery/scenes/{scene_id}/analyses",
        json={"analysis_type": "ndvi", "band_mapping": {"green": 3, "nir": 2}},
    )
    assert wrong_roles.status_code == 422
    assert "exactly: red, nir" in wrong_roles.json()["detail"]
    bad_body = client.post(
        f"/api/imagery/scenes/{scene_id}/analyses",
        json={"analysis_type": "kmeans", "cluster_count": 99},
    )
    assert bad_body.status_code == 422

    missing_nir_scene = client.post(
        "/api/imagery/ingest",
        files={"file": ("red-green-scene.tif", scene_bytes(include_nir=False), "image/tiff")},
    )
    assert missing_nir_scene.status_code == 201
    missing_nir = client.post(
        f"/api/imagery/scenes/{missing_nir_scene.json()['id']}/analyses",
        json={"analysis_type": "ndvi"},
    )
    assert missing_nir.status_code == 422
    assert "Near infrared" in missing_nir.json()["detail"]


def test_scene_analysis_storage_failure_is_recorded_and_same_request_can_retry(
    analysis_backend, monkeypatch
):
    repository, manager, _local_settings = analysis_backend
    client = TestClient(app)
    scene_id = client.post(
        "/api/imagery/ingest",
        files={"file": ("scene.tif", scene_bytes(), "image/tiff")},
    ).json()["id"]
    original_put_file = manager.artifact_store.put_file
    successful_uploads = []

    def fail_after_partial_upload(key, path, media_type):
        if successful_uploads:
            raise ArtifactStorageError("private storage unavailable")
        successful_uploads.append(key)
        return original_put_file(key, path, media_type)

    monkeypatch.setattr(manager.artifact_store, "put_file", fail_after_partial_upload)
    failed = client.post(
        f"/api/imagery/scenes/{scene_id}/analyses",
        json={"analysis_type": "ndvi"},
    )
    assert failed.status_code == 503
    assert "private storage unavailable" in failed.json()["detail"]
    job_id = failed.headers["x-processing-job-id"]
    failed_job = manager.get_job(job_id, "owner-1")
    assert failed_job["status"] == "failed"
    failed_record = manager.get_analysis(failed_job["analysis_id"], "owner-1")
    assert failed_record["status"] == "failed"
    assert "private storage unavailable" not in json.dumps(failed_record)
    failed_status = client.get(f"/api/imagery/analyses/{failed_job['analysis_id']}")
    assert failed_status.status_code == 200
    assert failed_status.json()["status"] == "failed"
    assert repository.get_artifacts(failed_job["analysis_id"], "owner-1") == []
    assert not (manager.settings.output_dir / successful_uploads[0]).exists()
    app.dependency_overrides.pop(routes.current_user, None)
    assert client.get(f"/api/results/{failed_job['analysis_id']}").status_code == 404
    app.dependency_overrides[routes.current_user] = lambda: AuthenticatedUser("owner-1")

    monkeypatch.setattr(manager.artifact_store, "put_file", original_put_file)
    retried = client.post(
        f"/api/imagery/scenes/{scene_id}/analyses",
        json={"analysis_type": "ndvi"},
    )
    assert retried.status_code == 200
    assert retried.json()["id"] == failed_job["analysis_id"]
    assert repository.get_analysis(retried.json()["id"], "owner-1")["status"] == "completed"


def test_scene_analysis_database_completion_failure_does_not_report_success(
    analysis_backend, monkeypatch
):
    repository, manager, _local_settings = analysis_backend
    client = TestClient(app)
    scene_id = client.post(
        "/api/imagery/ingest",
        files={"file": ("scene.tif", scene_bytes(), "image/tiff")},
    ).json()["id"]

    def fail_completion(*_args, **_kwargs):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(repository, "complete_analysis", fail_completion)
    response = client.post(
        f"/api/imagery/scenes/{scene_id}/analyses",
        json={"analysis_type": "ndvi"},
    )

    assert response.status_code == 503
    assert "database unavailable" not in response.text
    job_id = response.headers["x-processing-job-id"]
    job = manager.get_job(job_id, "owner-1")
    assert job["status"] == "failed"
    assert manager.get_analysis(job["analysis_id"], "owner-1")["status"] == "failed"


def test_ndvi_change_detection_calculates_thresholded_differences_and_area(tmp_path):
    transform = from_origin(300000, 1500000, 10, 10)
    baseline_path = tmp_path / "baseline.tif"
    comparison_path = tmp_path / "comparison.tif"
    baseline_red = np.asarray([[1, 1, 1], [1, -9999, 1]], dtype=np.float32)
    baseline_nir = np.full((2, 3), 3, dtype=np.float32)
    comparison_red = np.asarray([[1, 1, 1], [3, 1, 1]], dtype=np.float32)
    comparison_nir = np.asarray([[1, 3, 7], [5, 3, 7]], dtype=np.float32)

    for path, red, nir in (
        (baseline_path, baseline_red, baseline_nir),
        (comparison_path, comparison_red, comparison_nir),
    ):
        with rasterio.open(
            path,
            "w",
            driver="GTiff",
            width=3,
            height=2,
            count=2,
            dtype="float32",
            nodata=-9999,
            crs="EPSG:32644",
            transform=transform,
        ) as destination:
            destination.write(red, 1)
            destination.write(nir, 2)
            destination.set_band_description(1, "B04")
            destination.set_band_description(2, "B08")

    metadata = {
        "bands": [
            {"index": 1, "code": "B04"},
            {"index": 2, "code": "B08"},
        ]
    }
    output = tmp_path / "change"
    result = run_ndvi_change_detection(
        baseline_path,
        comparison_path,
        output,
        baseline_metadata=metadata,
        comparison_metadata=metadata,
        baseline_band_mapping={"red": 1, "nir": 2},
        comparison_band_mapping={"red": 1, "nir": 2},
        threshold=0.25,
        max_pixels=100,
        max_output_bytes=1_000_000,
    )

    assert result["statistics"]["valid_pixels"] == 5
    assert result["statistics"]["min"] == pytest.approx(-0.5)
    assert result["statistics"]["max"] == pytest.approx(0.25)
    assert result["statistics"]["mean"] == pytest.approx(-0.05)
    assert result["statistics"]["median"] == pytest.approx(0)
    assert result["valid_comparison_pixels"] == 5
    assert result["excluded_pixels"] == 1
    assert result["changed_pixels"] == 4
    assert result["unchanged_pixels"] == 1
    assert result["positive_change_pixels"] == 2
    assert result["negative_change_pixels"] == 2
    assert result["valid_comparison_area_square_metres"] == pytest.approx(500)
    assert result["changed_area_square_metres"] == pytest.approx(400)
    with rasterio.open(output / "ndvi-difference.tif") as difference:
        assert difference.crs.to_string() == "EPSG:32644"
        assert difference.transform == transform
        assert (difference.width, difference.height) == (3, 2)
        np.testing.assert_allclose(
            difference.read(1, masked=True).compressed(),
            [-0.5, 0, 0.25, -0.25, 0.25],
            atol=1e-6,
        )
    with rasterio.open(output / "change-mask.tif") as mask:
        values = mask.read(1, masked=True)
        assert values[0, 0] == 1
        assert values[0, 2] == 2
        assert values[1, 0] == 1
        assert values[1, 2] == 2
        assert np.ma.is_masked(values[1, 1])
        assert values.data[1, 1] == 255


def test_scene_change_api_persists_private_outputs_and_enforces_scene_ownership(
    analysis_backend, tmp_path
):
    _repository, manager, _local_settings = analysis_backend
    client = TestClient(app)
    baseline = client.post(
        "/api/imagery/ingest",
        files={
            "file": (
                "baseline.tif",
                scene_bytes(
                    acquisition_date="2024-01-01",
                    platform="Sentinel-2A",
                    sensor="MSI",
                ),
                "image/tiff",
            )
        },
    )
    comparison = client.post(
        "/api/imagery/ingest",
        files={
            "file": (
                "comparison.tif",
                scene_bytes(
                    acquisition_date="2024-02-01",
                    platform="Sentinel-2B",
                    sensor="MSI",
                ),
                "image/tiff",
            )
        },
    )
    assert baseline.status_code == comparison.status_code == 201
    baseline_id = baseline.json()["id"]
    comparison_id = comparison.json()["id"]
    payload = {
        "baseline_scene_id": baseline_id,
        "comparison_scene_id": comparison_id,
        "baseline_band_mapping": {"red": 1, "nir": 2},
        "comparison_band_mapping": {"red": 1, "nir": 2},
        "threshold": 0.1,
    }

    response = client.post("/api/imagery/change-detection", json=payload)
    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "completed"
    assert result["baseline"]["acquisition_date"] == "2024-01-01"
    assert result["comparison"]["acquisition_date"] == "2024-02-01"
    assert result["result"]["formula"] == "comparison-date NDVI - baseline-date NDVI"
    assert result["result"]["valid_comparison_pixels"] > 0
    assert [item["artifact_name"] for item in result["artifacts"]] == [
        "preview.png",
        "ndvi-difference.tif",
        "change-mask.tif",
        "summary.json",
    ]

    duplicate = client.post("/api/imagery/change-detection", json=payload)
    assert duplicate.status_code == 200
    assert duplicate.json()["id"] == result["id"]
    assert duplicate.json()["duplicate"] is True
    retrieved = client.get(f"/api/imagery/analyses/{result['id']}")
    assert retrieved.status_code == 200
    assert retrieved.json()["result"]["statistics"] == result["result"]["statistics"]
    raster = client.get(
        f"/api/results/{result['id']}/artifacts/ndvi-difference.tif/download"
    )
    assert raster.status_code == 200
    assert raster.headers["content-type"] in {"image/tiff", "application/octet-stream"}
    downloaded_path = tmp_path / "downloaded-change.tif"
    downloaded_path.write_bytes(raster.content)
    with rasterio.open(downloaded_path) as output:
        assert output.crs.to_string() == result["result"]["alignment"]["baseline_crs"]
        assert output.transform == Affine(*result["result"]["alignment"]["baseline_transform"])

    app.dependency_overrides[routes.current_user] = lambda: AuthenticatedUser("owner-2")
    assert client.post("/api/imagery/change-detection", json=payload).status_code == 404
    assert client.get(f"/api/imagery/analyses/{result['id']}").status_code == 404
    assert client.get(
        f"/api/results/{result['id']}/artifacts/ndvi-difference.tif/download"
    ).status_code == 404
    app.dependency_overrides[routes.current_user] = lambda: AuthenticatedUser("owner-1")


def test_scene_change_api_rejects_invalid_dates_sensors_and_thresholds(analysis_backend):
    _repository, _manager, _local_settings = analysis_backend
    client = TestClient(app)
    earlier = client.post(
        "/api/imagery/ingest",
        files={
            "file": (
                "earlier.tif",
                scene_bytes(
                    acquisition_date="2024-01-01",
                    platform="Sentinel-2A",
                    sensor="MSI",
                ),
                "image/tiff",
            )
        },
    ).json()["id"]
    later = client.post(
        "/api/imagery/ingest",
        files={
            "file": (
                "later.tif",
                scene_bytes(
                    acquisition_date="2024-02-01",
                    platform="Sentinel-2B",
                    sensor="MSI",
                ),
                "image/tiff",
            )
        },
    ).json()["id"]
    undated = client.post(
        "/api/imagery/ingest",
        files={"file": ("undated.tif", scene_bytes(), "image/tiff")},
    ).json()["id"]

    valid_mapping = {"red": 1, "nir": 2}
    base_payload = {
        "baseline_scene_id": earlier,
        "comparison_scene_id": later,
        "baseline_band_mapping": valid_mapping,
        "comparison_band_mapping": valid_mapping,
    }
    reversed_dates = client.post(
        "/api/imagery/change-detection",
        json={**base_payload, "baseline_scene_id": later, "comparison_scene_id": earlier},
    )
    assert reversed_dates.status_code == 422
    no_date = client.post(
        "/api/imagery/change-detection",
        json={**base_payload, "comparison_scene_id": undated},
    )
    assert no_date.status_code == 422
    assert "valid acquisition date" in no_date.json()["detail"]
    incompatible = client.post(
        "/api/imagery/ingest",
        files={
            "file": (
                "incompatible.tif",
                scene_bytes(
                    acquisition_date="2024-03-01",
                    platform="Landsat 8",
                    sensor="OLI",
                ),
                "image/tiff",
            )
        },
    ).json()["id"]
    wrong_sensor = client.post(
        "/api/imagery/change-detection",
        json={**base_payload, "comparison_scene_id": incompatible},
    )
    assert wrong_sensor.status_code == 422
    assert "incompatible" in wrong_sensor.json()["detail"]
    wrong_band = client.post(
        "/api/imagery/change-detection",
        json={
            **base_payload,
            "baseline_band_mapping": {"red": 3, "nir": 2},
        },
    )
    assert wrong_band.status_code == 422
    assert "labelled B03" in wrong_band.json()["detail"]
    wrong_threshold = client.post(
        "/api/imagery/change-detection",
        json={**base_payload, "threshold": 0},
    )
    assert wrong_threshold.status_code == 422


def test_scene_change_detection_reprojects_comparison_to_baseline_grid(tmp_path):
    baseline_path = tmp_path / "baseline-grid.tif"
    comparison_path = tmp_path / "finer-grid.tif"
    baseline_transform = from_origin(300000, 1500000, 10, 10)
    west, south, east, north = transform_bounds(
        "EPSG:32644",
        "EPSG:3857",
        300000,
        1499980,
        300020,
        1500000,
        densify_pts=21,
    )
    padding_x = (east - west) * 0.05
    padding_y = (north - south) * 0.05
    comparison_transform = from_origin(
        west - padding_x,
        north + padding_y,
        (east - west + 2 * padding_x) / 4,
        (north - south + 2 * padding_y) / 4,
    )
    for path, width, height, transform, red_value, nir_value in (
        (baseline_path, 2, 2, baseline_transform, 1, 3),
        (comparison_path, 4, 4, comparison_transform, 1, 1),
    ):
        with rasterio.open(
            path,
            "w",
            driver="GTiff",
            width=width,
            height=height,
            count=2,
            dtype="float32",
            nodata=-9999,
            crs="EPSG:3857" if path == comparison_path else "EPSG:32644",
            transform=transform,
        ) as destination:
            destination.write(np.full((height, width), red_value, dtype=np.float32), 1)
            destination.write(np.full((height, width), nir_value, dtype=np.float32), 2)
            destination.set_band_description(1, "B04")
            destination.set_band_description(2, "B08")

    metadata = {
        "bands": [
            {"index": 1, "code": "B04"},
            {"index": 2, "code": "B08"},
        ]
    }
    output = tmp_path / "aligned-change"
    result = run_ndvi_change_detection(
        baseline_path,
        comparison_path,
        output,
        baseline_metadata=metadata,
        comparison_metadata=metadata,
        baseline_band_mapping={"red": 1, "nir": 2},
        comparison_band_mapping={"red": 1, "nir": 2},
        threshold=0.5,
        max_pixels=100,
        max_output_bytes=1_000_000,
    )

    assert result["alignment"]["target"] == "baseline scene grid"
    assert result["alignment"]["comparison_was_reprojected_or_resampled"] is True
    assert result["alignment"]["baseline_crs"] == "EPSG:32644"
    assert result["valid_comparison_pixels"] == 4
    assert result["statistics"]["mean"] == pytest.approx(-0.5)
    with rasterio.open(output / "ndvi-difference.tif") as difference:
        assert (difference.width, difference.height) == (2, 2)
        assert difference.transform == baseline_transform
        np.testing.assert_allclose(difference.read(1), np.full((2, 2), -0.5), atol=1e-6)


def test_geographic_scene_change_area_uses_geodesic_measure_and_enforces_limits(tmp_path):
    baseline_path = tmp_path / "geographic-baseline.tif"
    comparison_path = tmp_path / "geographic-comparison.tif"
    transform = from_origin(18, 13, 0.01, 0.01)
    for path, nir_value in ((baseline_path, 3), (comparison_path, 1)):
        with rasterio.open(
            path,
            "w",
            driver="GTiff",
            width=1,
            height=1,
            count=2,
            dtype="float32",
            nodata=-9999,
            crs="EPSG:4326",
            transform=transform,
        ) as destination:
            destination.write(np.asarray([[1]], dtype=np.float32), 1)
            destination.write(np.asarray([[nir_value]], dtype=np.float32), 2)
            destination.set_band_description(1, "B04")
            destination.set_band_description(2, "B08")
    metadata = {
        "bands": [
            {"index": 1, "code": "B04"},
            {"index": 2, "code": "B08"},
        ]
    }
    options = {
        "baseline_metadata": metadata,
        "comparison_metadata": metadata,
        "baseline_band_mapping": {"red": 1, "nir": 2},
        "comparison_band_mapping": {"red": 1, "nir": 2},
        "threshold": 0.1,
        "max_pixels": 1,
        "max_output_bytes": 1_000_000,
    }
    result = run_ndvi_change_detection(
        baseline_path,
        comparison_path,
        tmp_path / "geographic-change",
        **options,
    )
    geod = CRS.from_epsg(4326).get_geod()
    expected_area, _ = geod.polygon_area_perimeter(
        [18, 18.01, 18.01, 18],
        [13, 13, 12.99, 12.99],
    )
    assert result["valid_comparison_area_square_metres"] == pytest.approx(
        abs(expected_area),
        rel=1e-6,
    )
    assert result["changed_area_square_metres"] == pytest.approx(abs(expected_area), rel=1e-6)
    with pytest.raises(DatasetError, match="configured 0-pixel comparison limit"):
        run_ndvi_change_detection(
            baseline_path,
            comparison_path,
            tmp_path / "too-many-pixels",
            **{**options, "max_pixels": 0},
        )
    with pytest.raises(DatasetError, match="artifact limit"):
        run_ndvi_change_detection(
            baseline_path,
            comparison_path,
            tmp_path / "too-many-output-bytes",
            **{**options, "max_output_bytes": 1},
        )


def test_scene_change_artifact_upload_failure_is_retryable_without_false_success(
    analysis_backend, monkeypatch
):
    _repository, manager, _local_settings = analysis_backend
    client = TestClient(app)
    baseline = client.post(
        "/api/imagery/ingest",
        files={
            "file": (
                "baseline.tif",
                scene_bytes(acquisition_date="2024-01-01"),
                "image/tiff",
            )
        },
    ).json()["id"]
    comparison = client.post(
        "/api/imagery/ingest",
        files={
            "file": (
                "comparison.tif",
                scene_bytes(acquisition_date="2024-02-01"),
                "image/tiff",
            )
        },
    ).json()["id"]
    payload = {
        "baseline_scene_id": baseline,
        "comparison_scene_id": comparison,
        "baseline_band_mapping": {"red": 1, "nir": 2},
        "comparison_band_mapping": {"red": 1, "nir": 2},
    }
    original_put_file = manager.artifact_store.put_file
    calls = []

    def fail_during_upload(key, path, media_type):
        calls.append(key)
        if len(calls) == 2:
            raise ArtifactStorageError("private storage temporarily unavailable")
        return original_put_file(key, path, media_type)

    monkeypatch.setattr(manager.artifact_store, "put_file", fail_during_upload)
    failed = client.post("/api/imagery/change-detection", json=payload)
    assert failed.status_code == 503
    assert "could not be persisted" in failed.json()["detail"]
    job = manager.get_job(failed.headers["x-processing-job-id"], "owner-1")
    assert job["status"] == "failed"
    record = manager.get_analysis(job["analysis_id"], "owner-1")
    assert record["status"] == "failed"
    assert record.get("summary") is None

    monkeypatch.setattr(manager.artifact_store, "put_file", original_put_file)
    retried = client.post("/api/imagery/change-detection", json=payload)
    assert retried.status_code == 200
    assert retried.json()["id"] == job["analysis_id"]
    assert retried.json()["status"] == "completed"
    assert manager.get_analysis(job["analysis_id"], "owner-1")["status"] == "completed"


def test_scene_change_database_failure_records_failed_job_without_success(
    analysis_backend, monkeypatch
):
    repository, manager, _local_settings = analysis_backend
    client = TestClient(app)
    baseline = client.post(
        "/api/imagery/ingest",
        files={
            "file": (
                "baseline.tif",
                scene_bytes(acquisition_date="2024-01-01"),
                "image/tiff",
            )
        },
    ).json()["id"]
    comparison = client.post(
        "/api/imagery/ingest",
        files={
            "file": (
                "comparison.tif",
                scene_bytes(acquisition_date="2024-02-01"),
                "image/tiff",
            )
        },
    ).json()["id"]

    def fail_completion(*_args, **_kwargs):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(repository, "complete_analysis", fail_completion)
    response = client.post(
        "/api/imagery/change-detection",
        json={
            "baseline_scene_id": baseline,
            "comparison_scene_id": comparison,
            "baseline_band_mapping": {"red": 1, "nir": 2},
            "comparison_band_mapping": {"red": 1, "nir": 2},
        },
    )
    assert response.status_code == 503
    assert "database unavailable" not in response.text
    job = manager.get_job(response.headers["x-processing-job-id"], "owner-1")
    assert job["status"] == "failed"
    assert manager.get_analysis(job["analysis_id"], "owner-1")["status"] == "failed"
    assert client.get(f"/api/imagery/analyses/{job['analysis_id']}").json()["status"] == "failed"

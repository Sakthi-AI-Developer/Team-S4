from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
import rasterio
from PIL import Image
from fastapi.testclient import TestClient
from rasterio.io import MemoryFile
from rasterio.transform import from_origin
from rasterio.warp import transform_bounds

import api.routes as routes
import main as application
from auth import AuthenticatedUser
from config import settings
from main import app
from persistence.artifacts import ArtifactStorageError, LocalArtifactStore
from persistence.database import PersistenceRepository
from persistence.manager import PersistenceManager
from processing.imagery import ImageryValidationError, inspect_geotiff


def synthetic_geotiff(
    *,
    crs: str | None = "EPSG:4326",
    acquisition_date: str | None = "2024-02-03",
) -> bytes:
    """Create a tiny synthetic raster fixture; it is not satellite observation data."""
    with MemoryFile() as memory_file:
        profile = {
            "driver": "GTiff",
            "width": 8,
            "height": 6,
            "count": 3,
            "dtype": "uint16",
            "transform": from_origin(18.0, 13.0, 0.01, 0.01),
        }
        if crs:
            profile["crs"] = crs
        with memory_file.open(**profile) as destination:
            destination.write(
                np.stack(
                    [
                        np.arange(48, dtype=np.uint16).reshape(6, 8) + offset
                        for offset in (100, 200, 300)
                    ]
                )
            )
            destination.set_band_description(1, "B04")
            destination.set_band_description(2, "B03")
            destination.set_band_description(3, "B02")
            if acquisition_date is not None:
                destination.update_tags(ACQUISITION_DATE=acquisition_date)
        return memory_file.read()


@pytest.fixture
def imagery_backend(tmp_path, monkeypatch):
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
    )
    repository = PersistenceRepository(f"sqlite:///{tmp_path / 'imagery.sqlite'}")
    repository.create_schema_for_tests()
    manager = PersistenceManager(
        local_settings,
        repository=repository,
        artifact_store=LocalArtifactStore(output_dir),
    )
    monkeypatch.setattr(routes, "settings", local_settings)
    monkeypatch.setattr(routes, "persistence_manager", manager)
    monkeypatch.setattr(application, "settings", local_settings)
    app.dependency_overrides[routes.current_user] = lambda: AuthenticatedUser("user-1")
    yield repository, manager, output_dir
    app.dependency_overrides.pop(routes.current_user, None)
    manager.close()


def test_inspect_geotiff_extracts_real_raster_metadata_and_bounded_preview(tmp_path):
    source_path = tmp_path / "scene.tif"
    preview_path = tmp_path / "preview.png"
    source_path.write_bytes(synthetic_geotiff())

    metadata = inspect_geotiff(
        source_path,
        preview_path,
        original_filename="S2A_MSIL2A_20240203T101021_scene.tif",
        sha256="a" * 64,
        max_pixels=100,
    )

    assert metadata["acquisition_date"] == "2024-02-03"
    assert metadata["crs"] == "EPSG:4326"
    assert metadata["extent_wgs84"] == pytest.approx([18.0, 12.94, 18.08, 13.0])
    assert metadata["resolution"] == pytest.approx([0.01, 0.01])
    assert [band["code"] for band in metadata["bands"]] == ["B04", "B03", "B02"]
    assert metadata["preview"]["width"] == 8
    assert metadata["preview"]["height"] == 6
    assert metadata["preview"]["crs"] == "EPSG:4326"
    assert metadata["preview"]["bounds_wgs84"] == pytest.approx(
        metadata["extent_wgs84"]
    )
    assert metadata["preview"]["cloud_mask_applied"] is False
    assert metadata["provenance_verified"] is False


def test_inspect_geotiff_warps_projected_source_preview_to_its_wgs84_bounds(tmp_path):
    source_path = tmp_path / "projected-scene.tif"
    preview_path = tmp_path / "preview.png"
    with MemoryFile() as memory_file:
        with memory_file.open(
            driver="GTiff",
            width=8,
            height=6,
            count=3,
            dtype="uint16",
            crs="EPSG:3857",
            transform=from_origin(200_000, 1_000_000, 100, 100),
        ) as destination:
            destination.write(
                np.stack(
                    [
                        np.arange(48, dtype=np.uint16).reshape(6, 8) + offset
                        for offset in (100, 200, 300)
                    ]
                )
            )
        source_path.write_bytes(memory_file.read())

    metadata = inspect_geotiff(
        source_path,
        preview_path,
        original_filename="projected-scene.tif",
        sha256="b" * 64,
        max_pixels=100,
    )

    with rasterio.open(source_path) as source:
        expected_bounds = transform_bounds(
            source.crs,
            "EPSG:4326",
            *source.bounds,
            densify_pts=21,
        )
    assert metadata["preview"]["crs"] == "EPSG:4326"
    assert metadata["preview"]["bounds_wgs84"] == pytest.approx(expected_bounds, abs=1e-4)
    with Image.open(preview_path) as preview:
        assert len(preview.getbands()) == 4
        assert preview.width <= 512
        assert preview.height <= 512
    with rasterio.open(preview_path) as preview:
        assert preview.count == 4


def test_inspect_geotiff_preserves_nodata_transparency_in_map_preview(tmp_path):
    source_path = tmp_path / "nodata-scene.tif"
    preview_path = tmp_path / "preview.png"
    bands = np.full((3, 6, 8), 100, dtype=np.uint16)
    bands[:, 0, 0] = 0
    with MemoryFile() as memory_file:
        with memory_file.open(
            driver="GTiff",
            width=8,
            height=6,
            count=3,
            dtype="uint16",
            nodata=0,
            crs="EPSG:4326",
            transform=from_origin(18.0, 13.0, 0.01, 0.01),
        ) as destination:
            destination.write(bands)
        source_path.write_bytes(memory_file.read())

    inspect_geotiff(
        source_path,
        preview_path,
        original_filename="nodata-scene.tif",
        sha256="c" * 64,
        max_pixels=100,
    )

    with Image.open(preview_path) as preview:
        alpha = np.asarray(preview)[..., 3]
    assert alpha[0, 0] == 0
    assert alpha[2, 2] == 255


def test_inspect_geotiff_rejects_missing_crs_and_pixel_over_limit(tmp_path):
    no_crs = tmp_path / "no-crs.tif"
    preview_path = tmp_path / "preview.png"
    no_crs.write_bytes(synthetic_geotiff(crs=None))

    with pytest.raises(ImageryValidationError, match="no coordinate reference system"):
        inspect_geotiff(no_crs, preview_path, original_filename="no-crs.tif", sha256="a" * 64, max_pixels=100)

    valid = tmp_path / "valid.tif"
    valid.write_bytes(synthetic_geotiff())
    with pytest.raises(ImageryValidationError, match="pixel imagery limit"):
        inspect_geotiff(valid, preview_path, original_filename="valid.tif", sha256="a" * 64, max_pixels=10)


def test_inspect_geotiff_rejects_corrupt_file(tmp_path):
    source_path = tmp_path / "corrupt.tif"
    source_path.write_bytes(b"not a TIFF")

    with pytest.raises(ImageryValidationError, match="corrupt or is not a readable"):
        inspect_geotiff(
            source_path,
            tmp_path / "preview.png",
            original_filename="corrupt.tif",
            sha256="a" * 64,
            max_pixels=100,
        )


def test_missing_acquisition_date_stays_unknown_unless_source_name_encodes_it(tmp_path):
    source_path = tmp_path / "scene.tif"
    preview_path = tmp_path / "preview.png"
    source_path.write_bytes(synthetic_geotiff(acquisition_date=None))

    with_date = inspect_geotiff(
        source_path,
        preview_path,
        original_filename="LC08_L2SP_012034_20240203_20240210_02_T1.tif",
        sha256="a" * 64,
        max_pixels=100,
    )
    without_date = inspect_geotiff(
        source_path,
        preview_path,
        original_filename="unknown-source.tif",
        sha256="a" * 64,
        max_pixels=100,
    )

    assert with_date["acquisition_date"] == "2024-02-03"
    assert without_date["acquisition_date"] is None


@pytest.mark.parametrize(
    ("filename", "content_type", "expected_status"),
    [
        ("scene.jp2", "image/jp2", 415),
        ("scene.tif", "application/zip", 415),
    ],
)
def test_ingest_rejects_unsupported_file_types(
    imagery_backend, filename, content_type, expected_status
):
    response = TestClient(app).post(
        "/api/imagery/ingest",
        files={"file": (filename, b"not-a-supported-upload", content_type)},
    )

    assert response.status_code == expected_status


def test_ingest_persists_metadata_and_private_retrievable_artifacts(imagery_backend):
    repository, _manager, output_dir = imagery_backend
    response = TestClient(app).post(
        "/api/imagery/ingest",
        files={"file": ("scene.tif", synthetic_geotiff(), "image/tiff")},
    )

    assert response.status_code == 201
    result = response.json()
    assert result["analysis"] == "imagery_ingestion"
    assert result["status"] == "completed"
    assert result["metadata"]["acquisition_date"] == "2024-02-03"
    assert result["persistence"]["mode"] == "local-only"
    record = repository.get_analysis(result["id"], "user-1")
    assert record is not None
    assert record["summary"]["metadata"]["sha256"] == result["metadata"]["sha256"]
    assert {item["artifact_name"] for item in repository.get_artifacts(result["id"], "user-1")} == {
        "preview.png",
        "source.tif",
        "summary.json",
    }

    scene = TestClient(app).get(f"/api/imagery/scenes/{result['id']}")
    preview = TestClient(app).get(
        f"/api/results/{result['id']}/artifacts/preview.png/download"
    )
    original = (output_dir / "results" / result["id"] / "source.tif").read_bytes()
    assert scene.status_code == 200
    assert scene.json()["metadata"] == result["metadata"]
    assert preview.status_code == 200
    assert preview.headers["content-type"].startswith("image/png")
    assert original == synthetic_geotiff()


def test_imagery_scenes_are_isolated_by_authenticated_owner(imagery_backend):
    result = TestClient(app).post(
        "/api/imagery/ingest",
        files={"file": ("private.tif", synthetic_geotiff(), "image/tiff")},
    ).json()
    app.dependency_overrides[routes.current_user] = lambda: AuthenticatedUser("user-2")

    scene = TestClient(app).get(f"/api/imagery/scenes/{result['id']}")
    artifacts = TestClient(app).get(
        f"/api/results/{result['id']}/artifacts/preview.png/download"
    )
    listing = TestClient(app).get("/api/imagery/scenes")

    assert scene.status_code == 404
    assert artifacts.status_code == 404
    assert listing.json()["scenes"] == []


def test_duplicate_ingestion_returns_existing_scene_without_duplicate_artifacts(imagery_backend):
    repository, _manager, _output_dir = imagery_backend
    payload = synthetic_geotiff()
    client = TestClient(app)

    first = client.post(
        "/api/imagery/ingest",
        files={"file": ("scene.tif", payload, "image/tiff")},
    )
    second = client.post(
        "/api/imagery/ingest",
        files={"file": ("renamed-scene.tif", payload, "image/tiff")},
    )

    assert first.status_code == 201
    assert second.status_code == 201
    assert second.json()["id"] == first.json()["id"]
    assert second.json()["duplicate"] is True
    assert len(repository.list_analyses_by_type("imagery_ingestion", "user-1")) == 1
    assert len(repository.get_artifacts(first.json()["id"], "user-1")) == 3


def test_unauthenticated_imagery_upload_is_rejected(imagery_backend, monkeypatch):
    local_settings = replace(routes.settings, require_auth=True)
    monkeypatch.setattr(routes, "settings", local_settings)
    monkeypatch.setattr(application, "settings", local_settings)
    app.dependency_overrides.pop(routes.current_user, None)

    response = TestClient(app).post(
        "/api/imagery/ingest",
        files={"file": ("scene.tif", synthetic_geotiff(), "image/tiff")},
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "A valid Supabase sign-in is required."


def test_ingest_enforces_configured_upload_size_limit(imagery_backend, monkeypatch):
    local_settings = replace(routes.settings, max_upload_bytes=16)
    monkeypatch.setattr(routes, "settings", local_settings)
    response = TestClient(app).post(
        "/api/imagery/ingest",
        files={"file": ("scene.tif", synthetic_geotiff(), "image/tiff")},
    )

    assert response.status_code == 413
    assert "imagery limit" in response.json()["detail"]


def test_storage_failure_marks_job_failed_and_same_scene_retry_completes(
    imagery_backend, monkeypatch
):
    repository, manager, output_dir = imagery_backend

    class FailOnceStore:
        name = "local"
        configured = True

        def __init__(self):
            self.local = LocalArtifactStore(output_dir)
            self.failed = False

        def put_file(self, key: str, path: Path, media_type: str) -> bool:
            if not self.failed:
                self.failed = True
                raise ArtifactStorageError("temporary storage failure")
            return self.local.put_file(key, path, media_type)

        def open_file(self, key: str):
            return self.local.open_file(key)

        def delete_objects(self, keys: list[str]) -> None:
            self.local.delete_objects(keys)

        def create_signed_url(self, key: str, expires_in: int) -> str:
            raise ArtifactStorageError("not supported")

        def check(self) -> None:
            self.local.check()

    manager.artifact_store = FailOnceStore()
    client = TestClient(app)
    payload = synthetic_geotiff()
    failed = client.post(
        "/api/imagery/ingest",
        files={"file": ("scene.tif", payload, "image/tiff")},
    )
    record = repository.list_analyses_by_type("imagery_ingestion", "user-1")
    assert failed.status_code == 503
    assert len(record) == 0
    # Failed records are omitted from the completed-scene listing; retry uses the
    # stable content hash to resume the existing job rather than creating another.
    analysis_id = next(
        directory.name
        for directory in output_dir.iterdir()
        if directory.is_dir() and (directory / "summary.json").exists()
    )
    registered = repository.get_analysis(analysis_id, "user-1")
    assert registered["status"] == "failed"

    retried = client.post(
        "/api/imagery/ingest",
        files={"file": ("scene.tif", payload, "image/tiff")},
    )

    assert retried.status_code == 201
    assert retried.json()["id"] == analysis_id
    assert repository.get_analysis(analysis_id, "user-1")["status"] == "completed"
    assert len(repository.list_analyses_by_type("imagery_ingestion", "user-1")) == 1


def test_job_registration_database_failure_is_safely_reported(
    imagery_backend, monkeypatch
):
    _repository, manager, _output_dir = imagery_backend

    def fail_start_job(*_args, **_kwargs):
        raise RuntimeError("private database connection detail")

    monkeypatch.setattr(manager, "start_job", fail_start_job)
    response = TestClient(app).post(
        "/api/imagery/ingest",
        files={"file": ("scene.tif", synthetic_geotiff(), "image/tiff")},
    )

    assert response.status_code == 503
    assert "metadata storage is temporarily unavailable" in response.json()["detail"]
    assert "private database connection detail" not in response.text

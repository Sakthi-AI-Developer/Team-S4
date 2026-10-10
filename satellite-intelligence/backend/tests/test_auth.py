from dataclasses import replace
import hashlib
from pathlib import Path
import re

import httpx
import numpy as np
import pytest
from fastapi import HTTPException
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from rasterio.io import MemoryFile
from rasterio.transform import from_origin
from sqlalchemy import create_engine, text

import api.routes as routes
import auth as authentication
import config as runtime_config
import main as application
from config import is_valid_supabase_url, settings
from main import app
from persistence.database import PersistenceRepository
from processing.data_provider import LocalDataProvider


class AuthResponse:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self._body = body

    def json(self):
        return self._body


def _auth_settings(**changes):
    return replace(
        settings,
        require_auth=True,
        supabase_url="https://project.example.test",
        supabase_anon_key="public-anon-key",
        supabase_service_role_key="server-only-key",
        **changes,
    )


def test_valid_access_token_uses_supabase_user_endpoint(monkeypatch):
    configured = _auth_settings()
    monkeypatch.setattr(authentication, "settings", configured)
    requests = []

    def get(url, headers, **kwargs):
        requests.append((url, headers, kwargs))
        return AuthResponse(200, {"id": "verified-user-id", "email": "verified@example.test"})

    monkeypatch.setattr(authentication.httpx, "get", get)

    user = authentication.verify_access_token("header.payload.signature")

    assert user.id == "verified-user-id"
    assert user.email == "verified@example.test"
    assert requests[0][0] == "https://project.example.test/auth/v1/user"
    assert requests[0][1]["Authorization"] == "Bearer header.payload.signature"
    assert requests[0][1]["apikey"] == "public-anon-key"
    assert requests[0][2]["timeout"] == 8
    assert requests[0][2]["follow_redirects"] is False
    assert requests[0][1]["Authorization"] == "Bearer " + "header.payload.signature"


def test_supabase_url_requires_https_except_for_loopback():
    assert is_valid_supabase_url("https://project.supabase.co")
    assert is_valid_supabase_url("https://project.supabase.co/")
    assert is_valid_supabase_url("http://localhost:54321")
    assert is_valid_supabase_url("http://127.0.0.1:54321")
    assert is_valid_supabase_url("http://[::1]:54321")
    assert not is_valid_supabase_url("http://project.supabase.co")
    assert not is_valid_supabase_url("https://user:password@project.supabase.co")
    assert not is_valid_supabase_url("https://project.supabase.co/auth/v1")
    assert not is_valid_supabase_url("https://project.supabase.co?redirect=elsewhere")
    assert not is_valid_supabase_url("https://project.supabase.co#fragment")


def test_insecure_supabase_auth_url_fails_before_forwarding_access_token(monkeypatch):
    monkeypatch.setattr(
        authentication,
        "settings",
        replace(_auth_settings(), supabase_url="http://project.example.test"),
    )

    def unexpected_request(*_args, **_kwargs):
        pytest.fail("An access token must not be sent to a cleartext remote URL.")

    monkeypatch.setattr(authentication.httpx, "get", unexpected_request)
    with pytest.raises(HTTPException) as error:
        authentication.verify_access_token("opaque-access-token")
    assert error.value.status_code == 503


def test_malformed_expired_and_incorrectly_signed_tokens_are_rejected(monkeypatch):
    monkeypatch.setattr(authentication, "settings", _auth_settings())
    rejected_tokens = {
        "malformed-token": "Malformed token",
        "expired.header.signature": "Expired token",
        "invalid-signature.header.signature": "Incorrect signature",
        "wrong-issuer.header.signature": "Unexpected issuer",
        "wrong-audience.header.signature": "Unexpected audience",
    }
    requested = []

    def get(url, headers, **kwargs):
        assert url.endswith("/auth/v1/user")
        assert kwargs["timeout"] == 8
        requested.append(headers["Authorization"])
        return AuthResponse(401, {"message": "Invalid JWT"})

    monkeypatch.setattr(authentication.httpx, "get", get)

    for token in rejected_tokens:
        with pytest.raises(HTTPException) as error:
            authentication.verify_access_token(token)
        assert error.value.status_code == 401

    assert requested == [f"Bearer {token}" for token in rejected_tokens]


    assert requested == ["Bearer " + token for token in rejected_tokens]


def test_authentication_configuration_and_upstream_failures_are_explicit(monkeypatch):
    monkeypatch.setattr(
        authentication,
        "settings",
        replace(_auth_settings(), supabase_anon_key=None),
    )
    with pytest.raises(HTTPException) as missing_configuration:
        authentication.verify_access_token("header.payload.signature")
    assert missing_configuration.value.status_code == 503

    monkeypatch.setattr(authentication, "settings", _auth_settings())

    def unavailable(url, **kwargs):
        assert url.endswith("/auth/v1/user")
        assert kwargs["timeout"] == 8
        raise httpx.ConnectError("connection unavailable")

    monkeypatch.setattr(authentication.httpx, "get", unavailable)
    with pytest.raises(HTTPException) as service_unavailable:
        authentication.verify_access_token("header.payload.signature")
    assert service_unavailable.value.status_code == 503


def test_configured_persistence_always_requires_authentication():
    demo_settings = replace(
        settings,
        require_auth=False,
        database_url=None,
        supabase_url=None,
        supabase_anon_key=None,
        supabase_service_role_key=None,
    )
    cloud_settings = replace(demo_settings, database_url="postgresql://db.example.test/app")

    assert demo_settings.authentication_required is False
    assert cloud_settings.authentication_required is True
    assert replace(demo_settings, supabase_url="https://project.example.test").authentication_required


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("true", True),
        ("1", True),
        ("yes", True),
        ("false", False),
        ("0", False),
        ("no", False),
    ],
)
def test_require_auth_environment_value_is_parsed_explicitly(
    monkeypatch, value, expected
):
    monkeypatch.setenv("REQUIRE_AUTH", value)

    assert runtime_config._boolean_setting("REQUIRE_AUTH", default=False) is expected


def test_invalid_require_auth_environment_value_fails_closed(monkeypatch):
    monkeypatch.setenv("REQUIRE_AUTH", "treu")

    with pytest.raises(ValueError, match="REQUIRE_AUTH must be"):
        runtime_config._boolean_setting("REQUIRE_AUTH", default=False)


def test_require_auth_defaults_to_local_demo_mode_when_unset(monkeypatch):
    monkeypatch.delenv("REQUIRE_AUTH", raising=False)

    assert runtime_config._boolean_setting("REQUIRE_AUTH", default=False) is False


def test_authenticated_middleware_protects_api_routes_and_preserves_cors(
    tmp_path, monkeypatch
):
    configured = _auth_settings(
        data_dir=tmp_path / "data",
        output_dir=tmp_path / "outputs",
        cors_origins=["https://frontend.example.test"],
    )
    configured.data_dir.mkdir()
    configured.output_dir.mkdir()
    monkeypatch.setattr(application, "settings", configured)
    monkeypatch.setattr(routes, "settings", configured)
    monkeypatch.setattr(authentication, "settings", configured)
    monkeypatch.setattr(routes, "data_provider", LocalDataProvider(configured.data_dir))

    def auth_endpoint(url, headers, **kwargs):
        assert url.endswith("/auth/v1/user")
        assert headers["apikey"] == "public-anon-key"
        assert kwargs["timeout"] == 8
        if headers["Authorization"].endswith("invalid-token"):
            return AuthResponse(401, {})
        return AuthResponse(200, {"id": "user-one"})

    monkeypatch.setattr(authentication.httpx, "get", auth_endpoint)
    client = TestClient(app)

    unauthorized = client.get(
        "/api/geoai/status",
        headers={"Origin": "https://frontend.example.test"},
    )
    assert unauthorized.status_code == 401
    assert unauthorized.headers["access-control-allow-origin"] == "https://frontend.example.test"
    assert client.get("/api/geoai/history").status_code == 401
    assert client.get(
        "/api/geoai/history",
        headers={"Authorization": "Bearer invalid-token"},
    ).status_code == 401
    assert client.get("/api/health").status_code == 200
    assert client.get("/api/ready").status_code == 200
    assert client.get("/api/auth/status").json()["authentication_required"] is True
    assert client.get(
        "/api/geoai/status",
        headers={"Authorization": "Bearer user-one-token"},
    ).status_code == 200
    denied_delete = client.delete(
        "/api/satellite/cache/test-product",
        headers={"Authorization": "Bearer user-one-token"},
    )
    assert denied_delete.status_code == 200
    assert denied_delete.json()["deleted"] is False


def test_authentication_middleware_protects_every_private_api_route(monkeypatch):
    configured = _auth_settings()
    monkeypatch.setattr(application, "settings", configured)
    monkeypatch.setattr(routes, "settings", configured)
    monkeypatch.setattr(authentication, "settings", configured)

    def reject_token(_url, **_kwargs):
        return AuthResponse(401, {"message": "Invalid JWT"})

    monkeypatch.setattr(authentication.httpx, "get", reject_token)
    client = TestClient(app)
    public_routes = {
        ("GET", "/api/health"),
        ("GET", "/api/ready"),
        ("GET", "/api/auth/status"),
    }
    checked_routes = set()

    for path, operations in app.openapi()["paths"].items():
        for method in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
            if method.lower() not in operations:
                continue
            if (method, path) in public_routes:
                continue
            request_path = re.sub(r"\{[^{}]+\}", "test", path)
            checked_routes.add((method, path))

            missing = client.request(method, request_path)
            malformed = client.request(
                method,
                request_path,
                headers={"Authorization": "Bearer malformed-token"},
            )

            assert missing.status_code == 401, f"{method} {path} accepted no token"
            assert malformed.status_code == 401, (
                f"{method} {path} accepted an invalid token"
            )

    assert len(checked_routes) >= 30


def _geotiff_bytes():
    memory_file = MemoryFile()
    with memory_file.open(
        driver="GTiff",
        width=2,
        height=2,
        count=1,
        dtype="float32",
        crs="EPSG:32644",
        transform=from_origin(500000, 3000000, 10, 10),
        nodata=-9999,
    ) as raster:
        raster.write(np.full((2, 2), 0.25, dtype=np.float32), 1)
    contents = memory_file.read()
    memory_file.close()
    return contents


def test_authenticated_dataset_uploads_are_isolated_per_user(tmp_path, monkeypatch):
    configured = _auth_settings(
        data_dir=tmp_path / "data",
        output_dir=tmp_path / "outputs",
    )
    configured.data_dir.mkdir()
    configured.output_dir.mkdir()
    monkeypatch.setattr(application, "settings", configured)
    monkeypatch.setattr(routes, "settings", configured)
    monkeypatch.setattr(authentication, "settings", configured)
    monkeypatch.setattr(routes, "data_provider", LocalDataProvider(configured.data_dir))

    def get(url, headers, **kwargs):
        assert url.endswith("/auth/v1/user")
        assert kwargs["timeout"] == 8
        token = headers["Authorization"].removeprefix("Bearer ")
        user_id = {"user-one-token": "user-one", "user-two-token": "user-two"}.get(token)
        return AuthResponse(200, {"id": user_id}) if user_id else AuthResponse(401, {})

    monkeypatch.setattr(authentication.httpx, "get", get)
    client = TestClient(app)

    response = client.post(
        "/api/dataset/current/upload",
        files={"file": ("Sentinel_B04.tif", _geotiff_bytes(), "image/tiff")},
        headers={"Authorization": "Bearer user-one-token"},
    )
    assert response.status_code == 201
    owner_path = (
        configured.data_dir
        / "users"
        / hashlib.sha256(b"user-one").hexdigest()
        / "current"
        / "B04.tif"
    )
    assert owner_path.is_file()

    owner_dataset = client.get(
        "/api/dataset",
        headers={"Authorization": "Bearer user-one-token"},
    )
    other_dataset = client.get(
        "/api/dataset",
        headers={"Authorization": "Bearer user-two-token"},
    )
    assert owner_dataset.status_code == 200
    assert owner_dataset.json()["current"]["available"] is True
    assert other_dataset.status_code == 200
    assert other_dataset.json()["current"]["available"] is False


def test_authenticated_satellite_cache_delete_is_user_scoped(tmp_path, monkeypatch):
    configured = _auth_settings(
        data_dir=tmp_path / "data",
        output_dir=tmp_path / "outputs",
        satellite_cache_dir=tmp_path / "cache",
    )
    configured.data_dir.mkdir()
    configured.output_dir.mkdir()
    monkeypatch.setattr(routes, "settings", configured)

    first_context = authentication.authenticated_user_id.set("user-one")
    try:
        routes._request_cache_manager().write_metadata(
            "product-one",
            {"product_id": "product-one"},
        )
    finally:
        authentication.authenticated_user_id.reset(first_context)

    second_context = authentication.authenticated_user_id.set("user-two")
    try:
        other_user_cache = routes._request_cache_manager()
        assert other_user_cache.list_products() == []
        assert other_user_cache.delete_product("product-one") is False
    finally:
        authentication.authenticated_user_id.reset(second_context)

    first_context = authentication.authenticated_user_id.set("user-one")
    try:
        assert routes._request_cache_manager().delete_product("product-one") is True
    finally:
        authentication.authenticated_user_id.reset(first_context)


def test_ownerless_legacy_records_are_not_visible_to_authenticated_users(tmp_path):
    repository = PersistenceRepository(f"sqlite:///{tmp_path / 'legacy.sqlite'}")
    repository.create_schema_for_tests()
    analysis_id = "a" * 32
    job_id = "b" * 32
    repository.create_job(analysis_id, "ndvi", {}, job_id)
    repository.complete_analysis(
        analysis_id,
        job_id,
        {"id": analysis_id},
        [{
            "artifact_name": "summary.json",
            "object_key": f"results/{analysis_id}/summary.json",
            "media_type": "application/json",
            "size_bytes": 12,
        }],
    )

    assert repository.get_analysis(analysis_id, "authenticated-user") is None
    assert repository.get_job(job_id, "authenticated-user") is None
    assert repository.get_artifacts(analysis_id, "authenticated-user") == []
    repository.close()


def test_owner_migration_preserves_legacy_rows_without_assigning_an_owner(
    tmp_path, monkeypatch
):
    database_path = tmp_path / "migration.sqlite"
    database_url = f"sqlite:///{database_path.as_posix()}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    backend_dir = Path(__file__).resolve().parents[1]
    alembic_config = Config(str(backend_dir / "alembic.ini"))
    alembic_config.set_main_option("script_location", str(backend_dir / "alembic"))
    command.upgrade(alembic_config, "0001_persistent_analysis")

    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO analyses "
                "(id, analysis, input_parameters, status, created_at) "
                "VALUES (:id, 'ndvi', '{}', 'completed', '2026-01-01T00:00:00')"
            ),
            {"id": "c" * 32},
        )
        connection.execute(
            text(
                "INSERT INTO processing_jobs "
                "(id, analysis_id, status, input_parameters, created_at) "
                "VALUES (:id, :analysis_id, 'completed', '{}', '2026-01-01T00:00:00')"
            ),
            {"id": "d" * 32, "analysis_id": "c" * 32},
        )
        connection.execute(
            text(
                "INSERT INTO analysis_artifacts "
                "(analysis_id, artifact_name, object_key, media_type, size_bytes) "
                "VALUES (:analysis_id, 'summary.json', 'results/legacy/summary.json', "
                "'application/json', 12)"
            ),
            {"analysis_id": "c" * 32},
        )
    engine.dispose()

    command.upgrade(alembic_config, "head")

    migrated = create_engine(database_url)
    with migrated.connect() as connection:
        analysis = connection.execute(
            text(
                "SELECT owner_id, status, idempotency_scope, idempotency_key "
                "FROM analyses WHERE id = :id"
            ),
            {"id": "c" * 32},
        ).one()
        artifact = connection.execute(
            text(
                "SELECT bucket_name, artifact_type, job_id, artifact_metadata "
                "FROM analysis_artifacts WHERE analysis_id = :id"
            ),
            {"id": "c" * 32},
        ).one()
        analysis_indexes = {
            row[1]
            for row in connection.exec_driver_sql("PRAGMA index_list('analyses')")
        }
        analysis_index_columns = {
            row[1]: [
                column[2]
                for column in connection.exec_driver_sql(
                    f"PRAGMA index_info('{row[1]}')"
                )
            ]
            for row in connection.exec_driver_sql("PRAGMA index_list('analyses')")
        }

    assert analysis.owner_id is None
    assert analysis.status == "completed"
    assert analysis.idempotency_scope is None
    assert analysis.idempotency_key is None
    assert "uq_analyses_idempotency_scope_key" in analysis_indexes
    assert analysis_index_columns["ix_analyses_owner_status_created"] == [
        "owner_id",
        "status",
        "created_at",
    ]
    assert artifact.bucket_name is None
    assert artifact.artifact_type == "file"
    assert artifact.job_id is None
    assert artifact.artifact_metadata in ("{}", {})
    migrated.dispose()

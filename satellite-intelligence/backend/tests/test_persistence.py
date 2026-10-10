from __future__ import annotations

from dataclasses import replace
import json

import httpx
import pytest
from fastapi.testclient import TestClient

import auth as authentication
import api.routes as routes
import config as runtime_config
import main as application
from config import settings
from main import app
from persistence.artifacts import ArtifactStorageError, SupabaseArtifactStore
from persistence.database import PersistenceRepository
from persistence.manager import PersistenceManager
from tests.test_api import configure_roots, write_test_dataset


class MemorySupabase:
    def __init__(self):
        self.objects: dict[str, tuple[bytes, str]] = {}
        self.bucket_exists = False
        self.bucket_public = False
        self.put_calls = 0
        self.fail_on_call: int | None = None
        self.fail_on_calls: set[int] = set()
        self.accept_then_fail_calls: set[int] = set()
        self.fail_get = False
        self.fail_head = False
        self.headers: list[dict[str, str]] = []
        self.bucket_create_calls = 0
        self.delete_calls: list[str] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.headers.append(dict(request.headers))
        path = request.url.path
        if path.endswith("/storage/v1/bucket/test-results"):
            if self.fail_head:
                return httpx.Response(503)
            if not self.bucket_exists:
                return httpx.Response(404)
            return httpx.Response(
                200,
                json={"name": "test-results", "public": self.bucket_public},
            )
        if path.endswith("/storage/v1/bucket") and request.method == "POST":
            self.bucket_create_calls += 1
            self.bucket_exists = True
            self.bucket_public = json.loads(request.content)["public"]
            return httpx.Response(200, json={"name": "test-results", "public": False})
        if "/storage/v1/object/sign/" in path:
            return httpx.Response(
                200,
                json={
                    "signedURL": path.replace("/storage/v1", "/storage/v1")
                    + "?token=mock-short-lived-token"
                },
            )
        if path.endswith("/storage/v1/object/test-results") and request.method == "DELETE":
            for key in json.loads(request.content)["prefixes"]:
                self.delete_calls.append(key)
                self.objects.pop(key, None)
            return httpx.Response(200, json={"message": "deleted"})
        if path.endswith("/storage/v1/object/list/test-results") and request.method == "POST":
            body = json.loads(request.content)
            prefix = body["prefix"]
            entries = {}
            for key in self.objects:
                if not key.startswith(prefix + "/"):
                    continue
                remainder = key[len(prefix) + 1 :]
                name = remainder.split("/", 1)[0]
                is_folder = "/" in remainder
                entries[name] = {
                    "name": name,
                    "id": None if is_folder else "mock-object-id",
                }
            ordered = [entries[name] for name in sorted(entries)]
            offset = body["offset"]
            return httpx.Response(
                200,
                json=ordered[offset : offset + body["limit"]],
            )
        prefix = "/storage/v1/object/test-results/"
        if path.startswith(prefix):
            key = path[len(prefix) :]
            if request.method == "POST":
                self.put_calls += 1
                if self.put_calls in self.accept_then_fail_calls:
                    self.objects[key] = (request.content, request.headers["content-type"])
                    return httpx.Response(503)
                if self.put_calls == self.fail_on_call or self.put_calls in self.fail_on_calls:
                    return httpx.Response(503)
                if key in self.objects:
                    return httpx.Response(409)
                self.objects[key] = (request.content, request.headers["content-type"])
                return httpx.Response(200, json={"Key": key})
            if request.method == "GET":
                if self.fail_get:
                    return httpx.Response(503)
                if key not in self.objects:
                    return httpx.Response(404)
                body, media_type = self.objects[key]
                return httpx.Response(200, content=body, headers={"content-type": media_type})
        return httpx.Response(404)


def _repository(tmp_path):
    repository = PersistenceRepository(f"sqlite:///{tmp_path / 'analysis.sqlite'}")
    repository.create_schema_for_tests()
    return repository


def _supabase(service: MemorySupabase):
    return SupabaseArtifactStore(
        url="https://project.example.test",
        service_role_key="test-service-role-key",
        bucket="test-results",
        client=httpx.Client(
            transport=httpx.MockTransport(service.handle),
            headers={
                "apikey": "test-service-role-key",
                "Authorization": "Bearer test-service-role-key",
            },
        ),
    )


def test_analysis_and_job_records_survive_repository_restart(tmp_path):
    repository = _repository(tmp_path)
    analysis_id = "a" * 32
    job_id = "b" * 32
    repository.create_job(analysis_id, "ndvi", {"dataset_id": "current"}, job_id)
    repository.complete_analysis(
        analysis_id,
        job_id,
        {"id": analysis_id, "success": True},
        [{"artifact_name": "summary.json", "object_key": f"results/{analysis_id}/summary.json",
          "media_type": "application/json", "size_bytes": 26}],
    )
    repository.close()

    reopened = PersistenceRepository(f"sqlite:///{tmp_path / 'analysis.sqlite'}")
    analysis = reopened.get_analysis(analysis_id)
    assert analysis is not None
    assert analysis["summary"]["success"] is True
    job = reopened.get_job(job_id)
    assert job is not None
    assert job["status"] == "completed"
    assert [item["status"] for item in job["history"]] == ["queued", "running", "completed"]
    assert reopened.get_artifacts(analysis_id)[0]["object_key"] == f"results/{analysis_id}/summary.json"
    artifact_record = reopened.get_artifacts(analysis_id)[0]
    assert artifact_record["job_id"] == job_id
    assert artifact_record["artifact_type"] == "file"
    assert artifact_record["bucket_name"] is None
    assert artifact_record["created_at"]
    reopened.close()


def test_job_failure_transition_and_interrupted_recovery(tmp_path):
    repository = _repository(tmp_path)
    failed_analysis, failed_job = "c" * 32, "d" * 32
    repository.create_job(failed_analysis, "ndvi", {"analysis_key": "ndvi"}, failed_job)
    repository.transition_job(failed_job, "failed", "Input raster is invalid.")
    failed = repository.get_job(failed_job)
    assert failed is not None
    assert failed["status"] == "failed"
    assert failed["error"] == "Input raster is invalid."
    assert failed["history"][-1]["detail"] == "Input raster is invalid."

    running_analysis, running_job = "e" * 32, "f" * 32
    repository.create_job(running_analysis, "all", {"analysis_keys": ["ndvi"]}, running_job)
    assert repository.mark_interrupted_jobs() == 1
    interrupted = repository.get_job(running_job)
    assert interrupted is not None
    assert interrupted["status"] == "failed"
    assert interrupted["error"] == "Backend restarted before processing completed."
    with pytest.raises(ValueError, match="Invalid processing job transition"):
        repository.transition_job(failed_job, "completed")
    repository.close()


def test_analysis_history_query_bounds_pages_and_filters_owner(tmp_path):
    repository = _repository(tmp_path)
    rows = []
    for ordinal in range(3):
        analysis_id = f"{ordinal + 1:032x}"
        job_id = f"{ordinal + 101:032x}"
        repository.create_job(
            analysis_id,
            "ndvi",
            {},
            job_id,
            owner_id="owner-a" if ordinal < 2 else "owner-b",
        )
        repository.complete_analysis(analysis_id, job_id, {"id": analysis_id}, [])
        rows.append(analysis_id)

    first_page = repository.list_analyses("owner-a", limit=1)
    second_page = repository.list_analyses("owner-a", limit=1, offset=1)
    other_owner = repository.list_analyses("owner-b", limit=1)

    assert len(first_page) == 2
    assert len(second_page) == 1
    assert all(record["owner_id"] == "owner-a" for record in first_page)
    assert second_page[0]["id"] != first_page[0]["id"]
    assert len(other_owner) == 1
    assert other_owner[0]["id"] == rows[2]
    with pytest.raises(ValueError, match="pagination"):
        repository.list_analyses("owner-a", limit=101)
    repository.close()


def test_idempotency_replay_of_active_job_returns_conflict(tmp_path, monkeypatch):
    repository = _repository(tmp_path)
    manager = PersistenceManager(
        replace(routes.settings, output_dir=tmp_path / "outputs"),
        repository=repository,
    )
    monkeypatch.setattr(routes, "persistence_manager", manager)
    input_parameters = {"analysis_key": "ndvi"}
    started = manager.start_job(
        "e" * 32,
        "ndvi",
        input_parameters,
        idempotency_key="active-request-key-0001",
    )

    with pytest.raises(routes.HTTPException) as error:
        routes._existing_analysis_response(
            started.analysis_id,
            started.job_id,
            "ndvi",
            input_parameters,
            None,
        )

    assert error.value.status_code == 409
    assert "still processing" in error.value.detail
    job = repository.get_job(started.job_id)
    assert job is not None
    assert job["status"] == "running"
    repository.close()


def test_supabase_bucket_is_created_private_and_artifacts_round_trip(tmp_path):
    source = tmp_path / "map.png"
    source.write_bytes(b"test-map-png")
    client = MemorySupabase()
    store = _supabase(client)
    key = "results/" + "a" * 32 + "/map.png"
    store.put_file(key, source, "image/png")
    assert store.put_file(key, source, "image/png") is False

    with store.open_file(key) as artifact:
        assert artifact.read() == b"test-map-png"
    signed_url = store.create_signed_url(key, 300)
    store.delete_objects([key])
    assert signed_url.endswith("?token=mock-short-lived-token")
    assert client.bucket_exists is True
    assert client.bucket_public is False
    assert client.bucket_create_calls == 1
    assert client.put_calls == 2
    assert all(
        header.get("authorization") == "Bearer test-service-role-key"
        for header in client.headers
    )
    assert not client.objects


def test_existing_private_supabase_bucket_is_reused(tmp_path):
    client = MemorySupabase()
    client.bucket_exists = True
    source = tmp_path / "report.json"
    source.write_text('{"valid": true}', encoding="utf-8")

    _supabase(client).put_file("results/" + "a" * 32 + "/report.json", source, "application/json")

    assert client.bucket_create_calls == 0
    assert len(client.objects) == 1


def test_transient_storage_upload_failure_retries_without_duplicate_objects(tmp_path):
    client = MemorySupabase()
    client.fail_on_call = 1
    source = tmp_path / "summary.json"
    source.write_text('{"ok": true}', encoding="utf-8")
    key = "results/" + "a" * 32 + "/summary.json"

    created = _supabase(client).put_file(key, source, "application/json")

    assert created is True
    assert client.put_calls == 2
    assert len(client.objects) == 1
    assert client.objects[key][0] == source.read_bytes()


def test_storage_retry_after_ambiguous_success_is_idempotent(tmp_path):
    client = MemorySupabase()
    client.accept_then_fail_calls = {1}
    source = tmp_path / "summary.json"
    source.write_text('{"ok": true}', encoding="utf-8")
    key = "results/" + "a" * 32 + "/summary.json"

    created = _supabase(client).put_file(key, source, "application/json")

    assert created is False
    assert client.put_calls == 2
    assert client.objects[key][0] == source.read_bytes()


def test_existing_public_supabase_bucket_is_rejected():
    client = MemorySupabase()
    client.bucket_exists = True
    client.bucket_public = True

    with pytest.raises(ArtifactStorageError, match="bucket is public"):
        _supabase(client).check()


def test_invalid_artifact_object_paths_are_rejected(tmp_path):
    source = tmp_path / "safe.png"
    source.write_bytes(b"png")

    with pytest.raises(ValueError, match="Invalid artifact object path"):
        _supabase(MemorySupabase()).put_file("../private.png", source, "image/png")


def test_storage_health_check_does_not_create_a_missing_bucket():
    client = MemorySupabase()

    with pytest.raises(ArtifactStorageError, match="bucket does not exist"):
        _supabase(client).check()

    assert client.bucket_exists is False
    assert client.bucket_create_calls == 0


def test_consistency_check_reports_missing_and_unreferenced_objects_without_deleting(tmp_path):
    from persistence.consistency import check_artifact_consistency

    repository = _repository(tmp_path)
    analysis_id = "a" * 32
    job_id = "b" * 32
    repository.create_job(analysis_id, "ndvi", {}, job_id)
    repository.complete_analysis(
        analysis_id,
        job_id,
        {"id": analysis_id},
        [
            {
                "artifact_name": "missing.json",
                "object_key": f"results/{analysis_id}/missing.json",
                "media_type": "application/json",
                "size_bytes": 10,
                "bucket_name": "test-results",
                "job_id": job_id,
            }
        ],
    )
    client = MemorySupabase()
    client.bucket_exists = True
    unreferenced_key = f"results/{analysis_id}/unreferenced.png"
    client.objects[unreferenced_key] = (b"png", "image/png")

    report = check_artifact_consistency(
        repository,
        _supabase(client),
        "test-results",
        limit=10,
    )

    assert report["missing_objects"] == [f"results/{analysis_id}/missing.json"]
    assert report["unreferenced_objects"] == [unreferenced_key]
    assert report["truncated"] is False
    assert client.delete_calls == []
    assert unreferenced_key in client.objects
    repository.close()


def test_artifact_upload_never_overwrites_an_existing_object(tmp_path):
    key = "results/" + "a" * 32 + "/summary.json"
    source = tmp_path / "summary.json"
    source.write_text('{"new": true}', encoding="utf-8")
    client = MemorySupabase()
    client.bucket_exists = True
    client.objects[key] = (b'{"existing": true}', "application/json")

    with pytest.raises(ArtifactStorageError, match="already exists"):
        _supabase(client).put_file(key, source, "application/json")

    assert client.objects[key][0] == b'{"existing": true}'


def test_missing_supabase_credentials_leave_artifacts_local(tmp_path):
    manager = PersistenceManager(
        replace(
            settings,
            output_dir=tmp_path,
            database_url=None,
            supabase_url=None,
            supabase_service_role_key=None,
        )
    )

    status = manager.status()

    assert status["cloud_persistence_active"] is False
    assert status["artifact_storage_provider"] == "local"
    assert "Cloud persistence is disabled" in status["message"]


def test_generated_artifacts_over_size_limit_are_not_uploaded(tmp_path):
    output_dir = tmp_path / "outputs"
    result_id, job_id = "3" * 32, "4" * 32
    result_dir = output_dir / result_id
    result_dir.mkdir(parents=True)
    (result_dir / "summary.json").write_text('{"ok": true}', encoding="utf-8")
    repository = _repository(tmp_path)
    repository.create_job(result_id, "ndvi", {}, job_id)
    client = MemorySupabase()
    manager = PersistenceManager(
        replace(settings, output_dir=output_dir, max_artifact_bytes=5),
        repository=repository,
        artifact_store=_supabase(client),
    )

    with pytest.raises(ValueError, match="exceeds the .* byte limit"):
        manager.persist_result(result_id, job_id, {"success": True}, result_dir)

    assert not client.objects
    assert repository.get_artifacts(result_id) == []
    repository.close()


def test_storage_failure_cleans_uploaded_prefix_and_marks_job_failed(tmp_path):
    output_dir = tmp_path / "outputs"
    output_dir.mkdir()
    result_id, job_id = "1" * 32, "2" * 32
    result_dir = output_dir / result_id
    result_dir.mkdir()
    artifact = result_dir / "summary.json"
    artifact.write_text('{"success": true}', encoding="utf-8")
    (result_dir / "ndvi.png").write_bytes(b"map")
    repository = _repository(tmp_path)
    repository.create_job(result_id, "ndvi", {"analysis_key": "ndvi"}, job_id)
    client = MemorySupabase()
    client.fail_on_calls = {2, 3, 4}
    manager = PersistenceManager(
        replace(settings, output_dir=output_dir),
        repository=repository,
        artifact_store=_supabase(client),
    )

    with pytest.raises(ArtifactStorageError, match="HTTP 503"):
        manager.persist_result(result_id, job_id, {"success": True}, result_dir)
    manager.fail_job(job_id, "Supabase Storage upload failed.")
    job = repository.get_job(job_id)
    assert job is not None
    assert job["status"] == "failed"
    assert not client.objects
    repository.close()


def test_local_mode_is_explicitly_not_cloud_persistent(tmp_path):
    manager = PersistenceManager(replace(settings, output_dir=tmp_path, database_url=None))
    status = manager.status()
    assert status["cloud_persistence_active"] is False
    assert status["artifact_storage_provider"] == "local"
    assert status["metadata_database_configured"] is False


def test_app_startup_and_health_work_without_cloud_credentials(tmp_path, monkeypatch):
    manager = PersistenceManager(replace(settings, output_dir=tmp_path, database_url=None))
    monkeypatch.setattr(application, "persistence_manager", manager)
    monkeypatch.setattr(routes, "persistence_manager", manager)

    with TestClient(app) as api_client:
        health = api_client.get("/api/health")
        readiness = api_client.get("/api/ready")
        status = api_client.get("/api/persistence/status")

    assert health.status_code == 200
    assert health.json()["service"] == "satellite-intelligence-api"
    assert readiness.status_code == 200
    assert readiness.json()["checks"] == {
        "configuration": "ok",
        "database": "not_required",
        "schema": "not_required",
        "storage": "not_required",
    }
    assert status.status_code == 200
    assert status.json()["cloud_persistence_active"] is False


def test_partial_supabase_credentials_fall_back_to_local_with_clear_status(tmp_path):
    manager = PersistenceManager(
        replace(
            settings,
            output_dir=tmp_path,
            database_url=None,
            supabase_url="https://project.example.test",
            supabase_service_role_key=None,
        )
    )
    status = manager.status()
    assert status["cloud_persistence_active"] is False
    assert status["artifact_storage_provider"] == "local"
    assert "SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY" in status["message"]


def test_insecure_supabase_storage_url_never_uses_service_role_key_over_http(tmp_path):
    configured = replace(
        settings,
        database_url=f"sqlite:///{tmp_path / 'insecure-url.sqlite'}",
        supabase_url="http://storage.example.test",
        supabase_service_role_key="server-only-test-key",
        require_auth=True,
        output_dir=tmp_path / "outputs",
    )
    manager = PersistenceManager(configured)
    try:
        assert manager.artifact_store.name == "local"
        assert manager.storage_configuration_error is not None
        assert "HTTPS" in manager.storage_configuration_error
    finally:
        manager.close()


def test_authenticated_cloud_mode_fails_closed_when_storage_is_not_configured(tmp_path):
    result_id = "5" * 32
    result_dir = tmp_path / result_id
    result_dir.mkdir()
    (result_dir / "summary.json").write_text('{"success": true}', encoding="utf-8")
    manager = PersistenceManager(
        replace(
            settings,
            output_dir=tmp_path,
            database_url=None,
            supabase_url=None,
            supabase_service_role_key=None,
            require_auth=True,
        )
    )

    with pytest.raises(ArtifactStorageError, match="SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY"):
        manager.persist_result(result_id, "6" * 32, {"success": True}, result_dir)


def test_runtime_validation_requires_server_storage_key_for_authenticated_mode(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        runtime_config,
        "settings",
        replace(
            settings,
            data_dir=tmp_path / "data",
            output_dir=tmp_path / "outputs",
            satellite_cache_dir=tmp_path / "cache",
            require_auth=True,
            supabase_url="https://project.example.test",
            supabase_anon_key="test-anon-key",
            supabase_service_role_key=None,
        ),
    )

    issues = runtime_config.validate_runtime_settings()

    assert any("SUPABASE_SERVICE_ROLE_KEY" in issue for issue in issues)


def test_unreachable_cloud_services_are_reported_inactive(tmp_path, monkeypatch):
    repository = _repository(tmp_path)
    client = MemorySupabase()
    client.fail_head = True
    manager = PersistenceManager(
        replace(settings, output_dir=tmp_path),
        repository=repository,
        artifact_store=_supabase(client),
    )

    def fail_database_connection():
        from sqlalchemy.exc import SQLAlchemyError
        raise SQLAlchemyError("Simulated database outage.")

    monkeypatch.setattr(repository, "check_connection", fail_database_connection)
    status = manager.status()

    assert status["cloud_persistence_active"] is False
    assert status["metadata_database_connected"] is False
    assert status["artifact_storage_available"] is False
    assert status["database_error"] == "Database connection failed."
    assert status["storage_error"] == "Supabase Storage health check failed."
    assert "database and object storage are unreachable" in status["message"]
    repository.close()


def test_cloud_persistence_requires_reachable_postgres_and_supabase(tmp_path, monkeypatch):
    repository = _repository(tmp_path)
    monkeypatch.setattr(repository.engine.dialect, "name", "postgresql")
    client = MemorySupabase()
    client.bucket_exists = True
    manager = PersistenceManager(
        replace(settings, output_dir=tmp_path),
        repository=repository,
        artifact_store=_supabase(client),
    )

    status = manager.status()

    assert status["cloud_persistence_active"] is True
    assert status["metadata_database_provider"] == "postgresql"
    assert client.bucket_create_calls == 0
    repository.close()


def test_supabase_authentication_and_analysis_artifact_ownership(tmp_path, monkeypatch):
    data_dir = configure_roots(tmp_path, monkeypatch)
    write_test_dataset(data_dir / "current", 0.2)
    repository = _repository(tmp_path)
    manager = PersistenceManager(
        replace(routes.settings, output_dir=routes.settings.output_dir),
        repository=repository,
    )
    monkeypatch.setattr(routes, "persistence_manager", manager)
    monkeypatch.setattr(
        authentication,
        "settings",
        replace(
            settings,
            require_auth=True,
            supabase_url="https://project.example.test",
            supabase_anon_key="test-anon-key",
        ),
    )

    class AuthResponse:
        def __init__(self, status_code, body):
            self.status_code = status_code
            self._body = body

        def json(self):
            return self._body

    def verify_token(_url, headers, **_kwargs):
        token = headers["Authorization"].removeprefix("Bearer ")
        if token == "user-one-token":
            return AuthResponse(200, {"id": "user-one", "email": "one@example.test"})
        if token == "user-two-token":
            return AuthResponse(200, {"id": "user-two", "email": "two@example.test"})
        return AuthResponse(401, {})

    monkeypatch.setattr(authentication.httpx, "get", verify_token)
    api_client = TestClient(app)
    protected_result_paths = [
        ("/api/results", None),
        ("/api/results/" + "a" * 32, None),
        ("/api/analyses/" + "a" * 32, None),
        ("/api/jobs/" + "b" * 32, None),
        ("/api/results/" + "a" * 32 + "/artifacts", None),
        (
            "/api/results/" + "a" * 32 + "/artifacts/summary.json/download",
            None,
        ),
        (
            "/api/results/" + "a" * 32 + "/signed-download",
            {"artifact_name": "summary.json"},
        ),
        ("/api/results/" + "a" * 32 + "/download/ndvi", None),
        ("/api/results/" + "a" * 32 + "/image/ndvi", None),
    ]
    for path, params in protected_result_paths:
        assert api_client.get(path, params=params).status_code == 401
        assert api_client.get(
            path,
            params=params,
            headers={"Authorization": "Bearer malformed-token"},
        ).status_code == 401
    assert api_client.get("/api/results").status_code == 401
    assert api_client.get(
        "/api/results", headers={"Authorization": "Bearer invalid-token"}
    ).status_code == 401

    empty_owner_list = api_client.get(
        "/api/results?offset=0",
        headers={"Authorization": "Bearer user-one-token"},
    )
    assert empty_owner_list.status_code == 200
    assert empty_owner_list.json() == {
        "success": True,
        "results": [],
        "has_more": False,
        "next_offset": None,
    }

    created = api_client.post(
        "/api/analyze/ndvi",
        headers={
            "Authorization": "Bearer user-one-token",
            "Idempotency-Key": "shared-request-key-0001",
        },
    )
    assert created.status_code == 200
    analysis_id = created.json()["id"]
    other_user_created = api_client.post(
        "/api/analyze/ndvi",
        headers={
            "Authorization": "Bearer user-two-token",
            "Idempotency-Key": "shared-request-key-0001",
        },
    )
    assert other_user_created.status_code == 200
    assert other_user_created.json()["id"] != analysis_id
    other_analysis_id = other_user_created.json()["id"]
    job_id = created.json()["job_id"]

    owner_list = api_client.get(
        "/api/results",
        headers={"Authorization": "Bearer user-one-token"},
    )
    other_owner_list = api_client.get(
        "/api/results",
        headers={"Authorization": "Bearer user-two-token"},
    )
    assert {item["id"] for item in owner_list.json()["results"]} == {analysis_id}
    assert {item["id"] for item in other_owner_list.json()["results"]} == {
        other_analysis_id
    }

    own_analysis = api_client.get(
        f"/api/analyses/{analysis_id}",
        headers={"Authorization": "Bearer user-one-token"},
    )
    other_analysis = api_client.get(
        f"/api/analyses/{analysis_id}",
        headers={"Authorization": "Bearer user-two-token"},
    )
    assert own_analysis.status_code == 200
    assert other_analysis.status_code == 404

    own_result = api_client.get(
        f"/api/results/{analysis_id}",
        headers={"Authorization": "Bearer user-one-token"},
    )
    assert own_result.status_code == 200
    own_download = api_client.get(
        f"/api/results/{analysis_id}/download/ndvi",
        headers={"Authorization": "Bearer user-one-token"},
    )
    assert own_download.status_code == 200
    own_image = api_client.get(
        f"/api/results/{analysis_id}/image/ndvi",
        headers={"Authorization": "Bearer user-one-token"},
    )
    assert own_image.status_code == 200
    own_artifacts = api_client.get(
        f"/api/results/{analysis_id}/artifacts",
        headers={"Authorization": "Bearer user-one-token"},
    )
    assert own_artifacts.status_code == 200
    assert own_artifacts.json()["artifacts"]
    artifact_name = own_artifacts.json()["artifacts"][0]["artifact_name"]
    other_job = api_client.get(
        f"/api/jobs/{job_id}",
        headers={"Authorization": "Bearer user-two-token"},
    )
    other_artifacts = api_client.get(
        f"/api/results/{analysis_id}/artifacts",
        headers={"Authorization": "Bearer user-two-token"},
    )
    other_artifact_download = api_client.get(
        f"/api/results/{analysis_id}/artifacts/{artifact_name}/download",
        headers={"Authorization": "Bearer user-two-token"},
    )
    other_signed_download = api_client.get(
        f"/api/results/{analysis_id}/signed-download",
        params={"artifact_name": artifact_name},
        headers={"Authorization": "Bearer user-two-token"},
    )
    other_result = api_client.get(
        f"/api/results/{analysis_id}",
        headers={"Authorization": "Bearer user-two-token"},
    )
    other_download = api_client.get(
        f"/api/results/{analysis_id}/download/ndvi",
        headers={"Authorization": "Bearer user-two-token"},
    )
    other_image = api_client.get(
        f"/api/results/{analysis_id}/image/ndvi",
        headers={"Authorization": "Bearer user-two-token"},
    )
    own_job = api_client.get(
        f"/api/jobs/{job_id}",
        headers={"Authorization": "Bearer user-one-token"},
    )
    assert own_job.status_code == 200
    assert other_result.status_code == 404
    assert other_job.status_code == 404
    assert other_artifacts.status_code == 200
    assert other_artifacts.json()["artifacts"] == []
    assert other_artifact_download.status_code == 404
    assert other_signed_download.status_code == 404
    assert other_download.status_code == 404
    assert other_image.status_code == 404
    assert api_client.delete(
        f"/api/jobs/{job_id}",
        headers={"Authorization": "Bearer user-two-token"},
    ).status_code == 405
    assert api_client.delete(
        f"/api/results/{analysis_id}/artifacts/{artifact_name}/download",
        headers={"Authorization": "Bearer user-two-token"},
    ).status_code == 405
    repository.close()


def test_analysis_api_persists_job_metadata_and_streams_supabase_artifacts(tmp_path, monkeypatch):
    data_dir = configure_roots(tmp_path, monkeypatch)
    write_test_dataset(data_dir / "current", 0.2)
    output_dir = routes.settings.output_dir
    repository = _repository(tmp_path)
    client_s3 = MemorySupabase()
    monkeypatch.setattr(
        routes,
        "persistence_manager",
        PersistenceManager(
            replace(
                routes.settings,
                output_dir=output_dir,
                supabase_storage_bucket="test-results",
            ),
            repository=repository,
            artifact_store=_supabase(client_s3),
        ),
    )
    api_client = TestClient(app)

    idempotency_headers = {"Idempotency-Key": "analysis-request-key-0001"}
    response = api_client.post("/api/analyze/ndvi", headers=idempotency_headers)
    assert response.status_code == 200
    payload = response.json()
    analysis_id, job_id = payload["id"], payload["job_id"]
    upload_calls = client_s3.put_calls
    replay = api_client.post("/api/analyze/ndvi", headers=idempotency_headers)
    assert replay.status_code == 200
    assert replay.json() == payload
    assert client_s3.put_calls == upload_calls
    mismatched_replay = api_client.post(
        "/api/analyze/ndwi",
        headers=idempotency_headers,
    )
    assert mismatched_replay.status_code == 409
    assert "different analysis request" in mismatched_replay.json()["detail"]
    analysis = repository.get_analysis(analysis_id)
    job = repository.get_job(job_id)
    assert analysis is not None
    assert job is not None
    assert analysis["input_parameters"]["analysis_key"] == "ndvi"
    assert job["status"] == "completed"
    assert len(repository.get_artifacts(analysis_id)) == 3
    artifact_metadata = repository.get_artifacts(analysis_id)
    raster_artifact = next(item for item in artifact_metadata if item["artifact_name"] == "ndvi.tif")
    assert raster_artifact["artifact_type"] == "raster"
    assert raster_artifact["bucket_name"] == "test-results"
    assert raster_artifact["created_at"]
    assert routes.persistence_manager.status()["cloud_persistence_active"] is False
    assert routes.persistence_manager.status()["metadata_database_provider"] == "sqlite"
    assert api_client.get(f"/api/analyses/{analysis_id}").json()["status"] == "completed"
    assert api_client.get(f"/api/jobs/{job_id}").json()["history"][-1]["status"] == "completed"

    client_s3.fail_get = True
    unavailable = api_client.get(f"/api/results/{analysis_id}/image/ndvi")
    assert unavailable.status_code == 503
    assert "storage is temporarily unavailable" in unavailable.json()["detail"]
    client_s3.fail_get = False
    image = api_client.get(f"/api/results/{analysis_id}/image/ndvi")
    assert image.status_code == 200
    assert image.headers["content-type"] == "image/png"
    artifact_list = api_client.get(f"/api/results/{analysis_id}/artifacts")
    assert artifact_list.status_code == 200
    names = {item["artifact_name"] for item in artifact_list.json()["artifacts"]}
    assert {"ndvi.tif", "ndvi.png", "summary.json"} <= names
    assert all(item["download_url"] for item in artifact_list.json()["artifacts"])
    generic_download = api_client.get(
        f"/api/results/{analysis_id}/artifacts/ndvi.tif/download"
    )
    assert generic_download.status_code == 200
    assert generic_download.content.startswith(b"II") or generic_download.content.startswith(b"MM")
    assert api_client.get(
        f"/api/results/{analysis_id}/artifacts/../download"
    ).status_code == 404
    signed = api_client.get(
        f"/api/results/{analysis_id}/signed-download",
        params={"artifact_name": "ndvi.tif"},
    )
    assert signed.status_code == 200
    assert signed.json()["expires_in"] == 300
    assert signed.json()["signed_url"].endswith("?token=mock-short-lived-token")
    download = api_client.get(f"/api/results/{analysis_id}/download/ndvi")
    assert download.status_code == 200
    assert download.content.startswith(b"II") or download.content.startswith(b"MM")
    assert api_client.get(f"/api/results/{analysis_id}").json()["id"] == analysis_id
    assert api_client.get(f"/api/results/{'../' + 'a' * 30}").status_code == 404
    assert api_client.get("/api/jobs/" + "../" + "a" * 30).status_code == 404
    repository.close()


def test_failed_analysis_exposes_and_persists_job_error(tmp_path, monkeypatch):
    configure_roots(tmp_path, monkeypatch)
    repository = _repository(tmp_path)
    monkeypatch.setattr(
        routes,
        "persistence_manager",
        PersistenceManager(
            replace(routes.settings, output_dir=routes.settings.output_dir),
            repository=repository,
        ),
    )

    response = TestClient(app).post("/api/analyze/ndvi")

    assert response.status_code == 422
    job_id = response.headers["x-processing-job-id"]
    job = repository.get_job(job_id)
    assert job is not None
    assert job["status"] == "failed"
    assert "B04" in job["error"]
    assert job["history"][-1]["status"] == "failed"
    repository.close()


def test_api_surfaces_artifact_storage_failure_and_marks_job_failed(tmp_path, monkeypatch):
    data_dir = configure_roots(tmp_path, monkeypatch)
    write_test_dataset(data_dir / "current", 0.2)
    repository = _repository(tmp_path)
    client_s3 = MemorySupabase()
    client_s3.fail_on_calls = {2, 3, 4}
    monkeypatch.setattr(
        routes,
        "persistence_manager",
        PersistenceManager(
            replace(routes.settings, output_dir=routes.settings.output_dir),
            repository=repository,
            artifact_store=_supabase(client_s3),
        ),
    )

    response = TestClient(app).post("/api/analyze/ndvi")

    assert response.status_code == 503
    job_id = response.headers["x-processing-job-id"]
    job = repository.get_job(job_id)
    assert job is not None
    assert job["status"] == "failed"
    assert "outputs could not be persisted" in response.json()["detail"]
    assert not client_s3.objects
    repository.close()


def test_api_surfaces_metadata_database_failure_and_cleans_uploaded_artifacts(
    tmp_path, monkeypatch
):
    data_dir = configure_roots(tmp_path, monkeypatch)
    write_test_dataset(data_dir / "current", 0.2)
    repository = _repository(tmp_path)
    client_s3 = MemorySupabase()
    manager = PersistenceManager(
        replace(
            routes.settings,
            output_dir=routes.settings.output_dir,
            supabase_storage_bucket="test-results",
        ),
        repository=repository,
        artifact_store=_supabase(client_s3),
    )

    def fail_metadata_write(*_args, **_kwargs):
        from sqlalchemy.exc import SQLAlchemyError

        raise SQLAlchemyError("Simulated metadata database failure.")

    monkeypatch.setattr(repository, "complete_analysis", fail_metadata_write)
    monkeypatch.setattr(routes, "persistence_manager", manager)

    response = TestClient(app).post("/api/analyze/ndvi")

    assert response.status_code == 503
    assert "metadata storage failed" in response.json()["detail"]
    assert "Local outputs have been retained" in response.json()["detail"]
    job = repository.get_job(response.headers["x-processing-job-id"])
    assert job is not None
    assert job["status"] == "failed"
    assert not client_s3.objects
    repository.close()

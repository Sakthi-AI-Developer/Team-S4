from __future__ import annotations

import mimetypes
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, IO

from config import Settings, is_valid_supabase_url
from persistence.artifacts import (
    ArtifactStorageError,
    ArtifactStore,
    LocalArtifactStore,
    SupabaseArtifactStore,
)
from persistence.database import PersistenceRepository
from sqlalchemy.exc import SQLAlchemyError


ARTIFACT_EXTENSIONS = {".csv", ".json", ".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff"}


@dataclass(frozen=True)
class JobStart:
    analysis_id: str
    job_id: str
    created: bool


class PersistenceManager:
    def __init__(
        self,
        settings: Settings,
        repository: PersistenceRepository | None = None,
        artifact_store: ArtifactStore | None = None,
    ):
        self.settings = settings
        self.repository = repository or (
            PersistenceRepository(
                settings.database_url,
                pool_size=settings.database_pool_size,
                max_overflow=settings.database_max_overflow,
            )
            if settings.database_url
            else None
        )
        self.storage_configuration_error: str | None = None
        self.artifact_store = artifact_store or self._make_artifact_store()

    def _make_artifact_store(self) -> ArtifactStore:
        supabase_url = self.settings.supabase_url
        service_role_key = self.settings.supabase_service_role_key
        if not supabase_url and not service_role_key:
            return LocalArtifactStore(self.settings.output_dir)
        if not supabase_url or not service_role_key:
            self.storage_configuration_error = (
                "Missing Supabase configuration: SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are both required."
            )
            return LocalArtifactStore(self.settings.output_dir)
        if not is_valid_supabase_url(supabase_url):
            self.storage_configuration_error = (
                "SUPABASE_URL must be a valid HTTPS URL; plain HTTP is allowed only for loopback development."
            )
            return LocalArtifactStore(self.settings.output_dir)
        if self.repository is None:
            self.storage_configuration_error = (
                "DATABASE_URL is required to enable persistent Supabase artifact references."
            )
            return LocalArtifactStore(self.settings.output_dir)
        if self.repository.engine.dialect.name != "postgresql":
            self.storage_configuration_error = (
                "Supabase artifact persistence requires PostgreSQL metadata storage."
            )
            return LocalArtifactStore(self.settings.output_dir)
        return SupabaseArtifactStore(
            supabase_url,
            service_role_key,
            self.settings.supabase_storage_bucket,
        )

    @property
    def cloud_persistence_active(self) -> bool:
        return bool(
            self.repository
            and self.repository.engine.dialect.name == "postgresql"
            and self.artifact_store.name == "supabase"
        )

    def status(self) -> dict[str, Any]:
        database_configured = self.repository is not None
        database_provider = (
            self.repository.engine.dialect.name if self.repository is not None else None
        )
        object_storage_configured = self.artifact_store.name == "supabase"
        database_connected = False
        storage_available = False
        database_error = None
        storage_error = None
        if self.repository:
            try:
                self.repository.check_connection()
                database_connected = True
            except SQLAlchemyError:
                database_error = "Database connection failed."
        try:
            self.artifact_store.check()
            storage_available = True
        except ArtifactStorageError:
            storage_error = "Supabase Storage health check failed."
        persistence_active = bool(
            database_connected
            and database_provider == "postgresql"
            and object_storage_configured
            and storage_available
        )
        if persistence_active:
            message = "PostgreSQL metadata and private Supabase Storage are available."
        elif database_error and storage_error:
            message = "The metadata database and object storage are unreachable; cloud persistence is inactive."
        elif database_error:
            message = "The metadata database is unreachable; persistent records are unavailable."
        elif storage_error:
            message = "Supabase Storage is unreachable or misconfigured; cloud persistence is inactive."
        elif self.storage_configuration_error:
            message = self.storage_configuration_error
        elif database_connected and database_provider != "postgresql":
            message = "SQLite metadata is available locally; cloud persistence requires PostgreSQL."
        elif database_configured:
            message = (
                "Metadata database is configured; artifacts are stored on local disk until "
                "Supabase configuration is complete."
            )
        else:
            message = "Cloud persistence is disabled; analysis uses local files and in-memory API processing."
        return {
            "cloud_persistence_active": persistence_active,
            "metadata_database_configured": database_configured,
            "metadata_database_provider": database_provider,
            "metadata_database_connected": database_connected,
            "artifact_storage_configured": object_storage_configured,
            "artifact_storage_available": storage_available,
            "artifact_storage_provider": self.artifact_store.name,
            "artifact_storage_bucket": (
                self.settings.supabase_storage_bucket if object_storage_configured else None
            ),
            "database_error": database_error,
            "storage_error": storage_error,
            "message": message,
        }

    def readiness(self, configuration_issues: list[str]) -> dict[str, Any]:
        authentication_required = self.settings.authentication_required
        configuration_invalid = bool(configuration_issues)
        database_required = bool(self.settings.database_url or authentication_required)
        storage_required = bool(
            authentication_required
            or self.settings.supabase_url
            or self.settings.supabase_service_role_key
        )
        checks = {
            "configuration": "unavailable" if configuration_invalid else "ok",
            "database": "not_required",
            "storage": "not_required",
        }

        if database_required:
            if self.repository is None:
                checks["database"] = "unavailable"
            else:
                try:
                    self.repository.check_connection()
                except SQLAlchemyError:
                    checks["database"] = "unavailable"
                else:
                    checks["database"] = "ok"

        if storage_required:
            if self.storage_configuration_error or self.artifact_store.name != "supabase":
                checks["storage"] = "unavailable"
            else:
                try:
                    self.artifact_store.check()
                except ArtifactStorageError:
                    checks["storage"] = "unavailable"
                else:
                    checks["storage"] = "ok"

        ready = all(value in {"ok", "not_required"} for value in checks.values())
        return {
            "status": "ok" if ready else "unavailable",
            "checks": checks,
        }

    def start_job(
        self,
        analysis_id: str,
        analysis: str,
        input_parameters: dict[str, Any],
        owner_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> JobStart:
        job_id = uuid.uuid4().hex
        if self.repository:
            analysis_id, job_id, created = self.repository.create_job(
                analysis_id,
                analysis,
                input_parameters,
                job_id,
                owner_id=owner_id,
                idempotency_scope=(
                    (owner_id or "public-demo") if idempotency_key else None
                ),
                idempotency_key=idempotency_key,
            )
        else:
            created = True
        return JobStart(analysis_id, job_id, created)

    def fail_job(self, job_id: str, error: str) -> None:
        if self.repository:
            self.repository.transition_job(job_id, "failed", error)

    def persist_result(
        self,
        result_id: str,
        job_id: str,
        summary: dict[str, Any],
        directory: Path,
    ) -> None:
        cloud_storage_requested = bool(
            self.settings.authentication_required
            or self.settings.supabase_url
            or self.settings.supabase_service_role_key
        )
        if cloud_storage_requested and not self.cloud_persistence_active:
            raise ArtifactStorageError(
                self.storage_configuration_error
                or (
                    "Authentication requires SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, "
                    "and PostgreSQL DATABASE_URL for private artifact persistence."
                    if self.settings.authentication_required
                    else "Private Supabase artifact storage and PostgreSQL metadata are not available."
                )
            )
        if not re.fullmatch(r"[0-9a-f]{32}", result_id):
            raise ValueError("Invalid analysis result ID.")
        source_directory = directory.resolve()
        output_directory = self.settings.output_dir.resolve()
        if source_directory.parent != output_directory or not source_directory.is_dir():
            raise ValueError("Analysis output directory is outside the configured output directory.")

        files = sorted(directory.rglob("*"))
        artifacts: list[dict[str, Any]] = []
        uploaded_keys: list[str] = []
        try:
            for path in files:
                if path.is_symlink():
                    raise ValueError("Analysis outputs may not contain symbolic links.")
                if not path.is_file():
                    continue
                name = path.relative_to(directory).as_posix()
                if Path(name).suffix.lower() not in ARTIFACT_EXTENSIONS:
                    raise ValueError("Analysis output has an unsupported artifact file type.")
                resolved_path = path.resolve()
                if source_directory not in resolved_path.parents:
                    raise ValueError("Analysis artifact is outside its result directory.")
                size_bytes = resolved_path.stat().st_size
                if size_bytes <= 0:
                    raise ValueError("Analysis artifacts must not be empty.")
                if size_bytes > self.settings.max_artifact_bytes:
                    raise ValueError(
                        f"Analysis artifact exceeds the {self.settings.max_artifact_bytes} byte limit."
                    )
                key = f"results/{result_id}/{name}"
                media_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
                created = self.artifact_store.put_file(key, resolved_path, media_type)
                if created:
                    uploaded_keys.append(key)
                artifacts.append(
                    {
                        "artifact_name": name,
                        "object_key": key,
                        "media_type": media_type,
                        "size_bytes": size_bytes,
                        "bucket_name": (
                            self.settings.supabase_storage_bucket
                            if self.artifact_store.name == "supabase"
                            else None
                        ),
                        "artifact_type": _artifact_type(Path(name).suffix.lower()),
                        "artifact_metadata": {"extension": Path(name).suffix.lower()},
                    }
                )
            if self.repository:
                self.repository.complete_analysis(result_id, job_id, summary, artifacts)
        except Exception:
            try:
                self.artifact_store.delete_objects(uploaded_keys)
            except ArtifactStorageError as cleanup_error:
                raise ArtifactStorageError(
                    "Artifact persistence failed and uploaded objects could not be fully cleaned up."
                ) from cleanup_error
            raise

    def get_analysis(
        self, result_id: str, owner_id: str | None = None
    ) -> dict[str, Any] | None:
        return self.repository.get_analysis(result_id, owner_id) if self.repository else None

    def list_analyses(
        self, owner_id: str | None = None, limit: int = 50, offset: int = 0
    ) -> list[dict[str, Any]] | None:
        return (
            self.repository.list_analyses(owner_id, limit=limit, offset=offset)
            if self.repository
            else None
        )

    def get_job(self, job_id: str, owner_id: str | None = None) -> dict[str, Any] | None:
        return self.repository.get_job(job_id, owner_id) if self.repository else None

    def get_artifacts(
        self, result_id: str, owner_id: str | None = None
    ) -> list[dict[str, Any]]:
        if not re.fullmatch(r"[0-9a-f]{32}", result_id):
            return []
        if self.repository:
            return self.repository.get_artifacts(result_id, owner_id)
        if self.settings.authentication_required:
            return []

        artifact_directory = self.settings.output_dir / "results" / result_id
        if not artifact_directory.is_dir():
            return []
        return [
            {
                "artifact_name": path.relative_to(artifact_directory).as_posix(),
                "object_key": path.relative_to(self.settings.output_dir).as_posix(),
                "media_type": mimetypes.guess_type(path.name)[0] or "application/octet-stream",
                "size_bytes": path.stat().st_size,
            }
            for path in sorted(artifact_directory.rglob("*"))
            if path.is_file() and not path.is_symlink()
        ]

    def get_artifact(
        self, result_id: str, filename: str, owner_id: str | None = None
    ) -> tuple[IO[bytes], dict[str, Any]] | None:
        artifact = next(
            (
                entry
                for entry in self.get_artifacts(result_id, owner_id)
                if entry["artifact_name"] == filename
            ),
            None,
        )
        if artifact is None:
            return None
        return self.artifact_store.open_file(artifact["object_key"]), artifact

    def create_signed_download_url(
        self,
        result_id: str,
        filename: str,
        owner_id: str | None = None,
        expires_in: int = 300,
    ) -> str | None:
        artifact = next(
            (
                entry
                for entry in self.get_artifacts(result_id, owner_id)
                if entry["artifact_name"] == filename
            ),
            None,
        )
        if artifact is None:
            return None
        return self.artifact_store.create_signed_url(artifact["object_key"], expires_in)

    def mark_interrupted_jobs(self) -> int:
        return self.repository.mark_interrupted_jobs() if self.repository else 0

    def close(self) -> None:
        close_store = getattr(self.artifact_store, "close", None)
        if close_store:
            close_store()
        if self.repository:
            self.repository.close()


def _artifact_type(extension: str) -> str:
    if extension in {".tif", ".tiff"}:
        return "raster"
    if extension == ".json":
        return "report"
    if extension in {".csv", ".pdf"}:
        return "report"
    if extension in {".png", ".jpg", ".jpeg"}:
        return "image"
    return "file"

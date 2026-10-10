from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import create_engine, inspect, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from persistence.models import (
    AnalysisArtifact,
    AnalysisRecord,
    Base,
    JobStatusHistory,
    ProcessingJob,
    utc_now,
)


class PersistenceRepository:
    def __init__(
        self,
        database_url: str,
        pool_size: int = 3,
        max_overflow: int = 1,
        pool_timeout: int = 10,
    ):
        if database_url.startswith("postgres://"):
            database_url = database_url.replace("postgres://", "postgresql+psycopg://", 1)
        elif database_url.startswith("postgresql://") and "+" not in database_url.split("://", 1)[0]:
            database_url = database_url.replace("postgresql://", "postgresql+psycopg://", 1)
        engine_options: dict[str, Any] = {"pool_pre_ping": True}
        if database_url.startswith(("postgresql+", "postgres://", "postgresql://")):
            engine_options.update(
                pool_size=pool_size,
                max_overflow=max_overflow,
                pool_timeout=pool_timeout,
            )
        self.engine: Engine = create_engine(database_url, **engine_options)
        self._sessions = sessionmaker(self.engine, expire_on_commit=False)

    def create_schema_for_tests(self) -> None:
        Base.metadata.create_all(self.engine)

    def check_connection(self) -> None:
        with self.engine.connect() as connection:
            connection.exec_driver_sql("SELECT 1")

    def check_schema(self) -> bool:
        inspector = inspect(self.engine)
        table_names = set(inspector.get_table_names())
        for table in Base.metadata.sorted_tables:
            if table.name not in table_names:
                return False
            actual_columns = {
                column["name"] for column in inspector.get_columns(table.name)
            }
            if not set(table.columns.keys()).issubset(actual_columns):
                return False
        return True

    def create_job(
        self,
        analysis_id: str,
        analysis: str,
        input_parameters: dict[str, Any],
        job_id: str,
        owner_id: str | None = None,
        idempotency_scope: str | None = None,
        idempotency_key: str | None = None,
    ) -> tuple[str, str, bool]:
        if (idempotency_scope is None) != (idempotency_key is None):
            raise ValueError("Idempotency scope and key must be provided together.")
        now = utc_now()
        try:
            with self._sessions.begin() as session:
                session.add(
                    AnalysisRecord(
                        id=analysis_id,
                        analysis=analysis,
                        input_parameters=input_parameters,
                        owner_id=owner_id,
                        idempotency_scope=idempotency_scope,
                        idempotency_key=idempotency_key,
                        status="running",
                        created_at=now,
                    )
                )
                job = ProcessingJob(
                    id=job_id,
                    analysis_id=analysis_id,
                    status="queued",
                    input_parameters=input_parameters,
                    created_at=now,
                )
                session.add(job)
                session.flush()
                session.add(JobStatusHistory(job_id=job_id, status="queued", occurred_at=now))
                job.status = "running"
                job.started_at = now
                session.add(JobStatusHistory(job_id=job_id, status="running", occurred_at=now))
        except IntegrityError:
            if idempotency_scope is None or idempotency_key is None:
                raise
            with self._sessions() as session:
                existing = session.scalar(
                    select(AnalysisRecord).where(
                        AnalysisRecord.idempotency_scope == idempotency_scope,
                        AnalysisRecord.idempotency_key == idempotency_key,
                    )
                )
                existing_job = (
                    session.scalar(
                        select(ProcessingJob)
                        .where(ProcessingJob.analysis_id == existing.id)
                        .order_by(ProcessingJob.created_at.desc())
                    )
                    if existing is not None
                    else None
                )
                if existing is not None and existing_job is not None:
                    return existing.id, existing_job.id, False
            raise
        return analysis_id, job_id, True

    def transition_job(self, job_id: str, status: str, detail: str | None = None) -> None:
        allowed = {"queued": {"running", "failed"}, "running": {"completed", "failed"}}
        with self._sessions.begin() as session:
            job = session.get(ProcessingJob, job_id)
            if job is None:
                raise LookupError("Processing job was not found.")
            if status not in allowed.get(job.status, set()):
                raise ValueError(f"Invalid processing job transition: {job.status} -> {status}.")
            now = utc_now()
            job.status = status
            job.error = detail if status == "failed" else None
            if status in {"completed", "failed"}:
                job.finished_at = now
            analysis = session.get(AnalysisRecord, job.analysis_id)
            if analysis is not None:
                analysis.status = status
                analysis.error = detail if status == "failed" else None
                if status in {"completed", "failed"}:
                    analysis.completed_at = now
            session.add(JobStatusHistory(job_id=job_id, status=status, detail=detail, occurred_at=now))

    def complete_analysis(
        self,
        analysis_id: str,
        job_id: str,
        summary: dict[str, Any],
        artifacts: list[dict[str, Any]],
    ) -> None:
        with self._sessions.begin() as session:
            record = session.get(AnalysisRecord, analysis_id)
            if record is None:
                raise LookupError("Analysis record was not found.")
            job = session.get(ProcessingJob, job_id)
            if job is None or job.analysis_id != analysis_id:
                raise LookupError("Processing job was not found for this analysis.")
            if job.status != "running":
                raise ValueError(f"Cannot complete a processing job in '{job.status}' state.")
            completed_at = utc_now()
            record.summary = summary
            record.status = "completed"
            record.completed_at = completed_at
            job.status = "completed"
            job.finished_at = completed_at
            session.add(
                JobStatusHistory(job_id=job_id, status="completed", occurred_at=completed_at)
            )
            for artifact in artifacts:
                session.add(
                    AnalysisArtifact(
                        analysis_id=analysis_id,
                        artifact_name=artifact["artifact_name"],
                        object_key=artifact["object_key"],
                        media_type=artifact["media_type"],
                        size_bytes=artifact["size_bytes"],
                        bucket_name=artifact.get("bucket_name"),
                        artifact_type=artifact.get("artifact_type", "file"),
                        job_id=job_id,
                        created_at=artifact.get("created_at", completed_at),
                        artifact_metadata=artifact.get("artifact_metadata", {}),
                    )
                )

    def get_analysis(
        self, analysis_id: str, owner_id: str | None = None
    ) -> dict[str, Any] | None:
        with self._sessions() as session:
            record = session.get(AnalysisRecord, analysis_id)
            if record is None or (owner_id is not None and record.owner_id != owner_id):
                return None
            return {
                "id": record.id,
                "analysis": record.analysis,
                "owner_id": record.owner_id,
                "input_parameters": record.input_parameters,
                "status": record.status,
                "error": record.error,
                "summary": record.summary,
                "created_at": record.created_at.isoformat() if record.created_at else None,
                "completed_at": record.completed_at.isoformat() if record.completed_at else None,
            }

    def list_analyses(
        self,
        owner_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        if not 1 <= limit <= 100 or offset < 0:
            raise ValueError("Analysis-list pagination is outside the supported range.")
        with self._sessions() as session:
            query = select(AnalysisRecord).where(AnalysisRecord.status == "completed")
            if owner_id is not None:
                query = query.where(AnalysisRecord.owner_id == owner_id)
            records = session.scalars(
                query.order_by(AnalysisRecord.created_at.desc())
                .limit(limit + 1)
                .offset(offset)
            ).all()
            return [
                {
                    "id": record.id,
                    "created_at": record.created_at.isoformat() if record.created_at else None,
                    "analysis": record.analysis,
                    "status": record.status,
                    "owner_id": record.owner_id,
                }
                for record in records
            ]

    def get_job(self, job_id: str, owner_id: str | None = None) -> dict[str, Any] | None:
        with self._sessions() as session:
            query = select(ProcessingJob).where(ProcessingJob.id == job_id)
            if owner_id is not None:
                query = query.join(AnalysisRecord).where(AnalysisRecord.owner_id == owner_id)
            job = session.scalar(query)
            if job is None:
                return None
            return {
                "id": job.id,
                "analysis_id": job.analysis_id,
                "status": job.status,
                "input_parameters": job.input_parameters,
                "error": job.error,
                "created_at": job.created_at.isoformat() if job.created_at else None,
                "started_at": job.started_at.isoformat() if job.started_at else None,
                "finished_at": job.finished_at.isoformat() if job.finished_at else None,
                "history": [
                    {
                        "status": item.status,
                        "detail": item.detail,
                        "occurred_at": item.occurred_at.isoformat(),
                    }
                    for item in job.status_history
                ],
            }

    def get_artifacts(
        self, analysis_id: str, owner_id: str | None = None
    ) -> list[dict[str, Any]]:
        with self._sessions() as session:
            query = select(AnalysisArtifact).where(AnalysisArtifact.analysis_id == analysis_id)
            if owner_id is not None:
                query = query.join(AnalysisRecord).where(AnalysisRecord.owner_id == owner_id)
            records = session.scalars(query).all()
            return [
                {
                    "artifact_name": record.artifact_name,
                    "object_key": record.object_key,
                    "media_type": record.media_type,
                    "size_bytes": record.size_bytes,
                    "bucket_name": record.bucket_name,
                    "artifact_type": record.artifact_type,
                    "job_id": record.job_id,
                    "created_at": record.created_at.isoformat(),
                    "artifact_metadata": record.artifact_metadata,
                }
                for record in records
            ]

    def list_artifact_object_keys(
        self, bucket_name: str, limit: int
    ) -> tuple[list[str], bool]:
        if not 1 <= limit <= 10_000:
            raise ValueError("Artifact consistency scan limit must be between 1 and 10000.")
        with self._sessions() as session:
            keys = session.scalars(
                select(AnalysisArtifact.object_key)
                .where(AnalysisArtifact.bucket_name == bucket_name)
                .order_by(AnalysisArtifact.id)
                .limit(limit + 1)
            ).all()
        return list(keys[:limit]), len(keys) > limit

    def mark_interrupted_jobs(self) -> int:
        interrupted_at: datetime = utc_now()
        count = 0
        with self._sessions.begin() as session:
            jobs = session.scalars(
                select(ProcessingJob).where(ProcessingJob.status.in_(("queued", "running")))
            ).all()
            for job in jobs:
                job.status = "failed"
                job.error = "Backend restarted before processing completed."
                job.finished_at = interrupted_at
                analysis = session.get(AnalysisRecord, job.analysis_id)
                if analysis is not None:
                    analysis.status = "failed"
                    analysis.error = job.error
                    analysis.completed_at = interrupted_at
                session.add(
                    JobStatusHistory(
                        job_id=job.id,
                        status="failed",
                        detail=job.error,
                        occurred_at=interrupted_at,
                    )
                )
                count += 1
        return count

    def close(self) -> None:
        self.engine.dispose()

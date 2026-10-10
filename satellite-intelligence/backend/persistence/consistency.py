from typing import Any

from persistence.database import PersistenceRepository
from persistence.artifacts import SupabaseArtifactStore


def check_artifact_consistency(
    repository: PersistenceRepository,
    artifact_store: SupabaseArtifactStore,
    bucket_name: str,
    limit: int = 1000,
) -> dict[str, Any]:
    if not 1 <= limit <= 10_000:
        raise ValueError("Consistency scan limit must be between 1 and 10000.")

    metadata_keys, metadata_truncated = repository.list_artifact_object_keys(
        bucket_name, limit
    )
    stored_keys, storage_truncated = artifact_store.list_objects("results", limit)
    metadata_set = set(metadata_keys)
    stored_set = set(stored_keys)
    return {
        "bucket": bucket_name,
        "prefix": "results",
        "metadata_checked": len(metadata_keys),
        "objects_checked": len(stored_keys),
        "missing_objects": sorted(metadata_set - stored_set),
        "unreferenced_objects": sorted(stored_set - metadata_set),
        "truncated": metadata_truncated or storage_truncated,
    }

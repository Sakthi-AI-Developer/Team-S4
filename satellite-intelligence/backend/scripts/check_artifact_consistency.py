from __future__ import annotations

import argparse
import json
import sys

from sqlalchemy.exc import SQLAlchemyError

from config import settings
from persistence.artifacts import ArtifactStorageError, SupabaseArtifactStore
from persistence.consistency import check_artifact_consistency
from persistence.database import PersistenceRepository


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare registered analysis artifacts with private Supabase Storage objects."
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=1000,
        help="Maximum metadata rows and objects scanned (1-10000; default: 1000).",
    )
    parser.add_argument(
        "--delete-unreferenced",
        action="store_true",
        help="Delete reviewed orphan objects under results/; disabled by default.",
    )
    parser.add_argument(
        "--confirm-bucket",
        help="Must exactly match SUPABASE_STORAGE_BUCKET when deletion is enabled.",
    )
    parser.add_argument(
        "--max-delete",
        type=int,
        help="Maximum orphan objects to delete (1-100); required with deletion.",
    )
    args = parser.parse_args()
    if not 1 <= args.limit <= 10_000:
        parser.error("--limit must be between 1 and 10000.")
    if args.delete_unreferenced:
        if args.confirm_bucket != settings.supabase_storage_bucket:
            parser.error("--confirm-bucket must exactly match the configured bucket name.")
        if args.max_delete is None or not 1 <= args.max_delete <= 100:
            parser.error("--max-delete must be between 1 and 100 when deletion is enabled.")
    elif args.confirm_bucket is not None or args.max_delete is not None:
        parser.error("--confirm-bucket and --max-delete require --delete-unreferenced.")
    return args


def main() -> int:
    args = parse_args()
    if not settings.database_url or not settings.supabase_url or not settings.supabase_service_role_key:
        print(json.dumps({"error": "Database and server-side Storage configuration are required."}))
        return 2

    repository = PersistenceRepository(settings.database_url)
    artifact_store = SupabaseArtifactStore(
        settings.supabase_url,
        settings.supabase_service_role_key,
        settings.supabase_storage_bucket,
    )
    try:
        report = check_artifact_consistency(
            repository,
            artifact_store,
            settings.supabase_storage_bucket,
            args.limit,
        )
        if args.delete_unreferenced and report["truncated"]:
            report["cleanup"] = "skipped: scan was truncated; rerun with a larger --limit"
        elif args.delete_unreferenced:
            orphan_keys = report["unreferenced_objects"][: args.max_delete]
            artifact_store.delete_objects(orphan_keys)
            report["deleted_objects"] = orphan_keys
            report["cleanup"] = "completed"
        print(json.dumps(report, indent=2))
        if report["truncated"]:
            return 2
        return 1 if report["missing_objects"] or report["unreferenced_objects"] else 0
    except (ArtifactStorageError, SQLAlchemyError) as exc:
        print(
            json.dumps(
                {
                    "error": "Consistency scan could not complete.",
                    "error_type": type(exc).__name__,
                }
            )
        )
        return 2
    finally:
        artifact_store.close()
        repository.close()


if __name__ == "__main__":
    sys.exit(main())

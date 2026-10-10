from __future__ import annotations

import hashlib
from pathlib import Path
from tempfile import SpooledTemporaryFile
import time
from typing import IO, Protocol
from urllib.parse import quote, urljoin, urlsplit

import httpx

MAX_STORAGE_ATTEMPTS = 3


class ArtifactStorageError(RuntimeError):
    pass


class ArtifactStore(Protocol):
    name: str
    configured: bool

    def put_file(self, key: str, path: Path, media_type: str) -> bool: ...
    def open_file(self, key: str) -> IO[bytes]: ...
    def delete_objects(self, keys: list[str]) -> None: ...
    def create_signed_url(self, key: str, expires_in: int) -> str: ...
    def check(self) -> None: ...


class LocalArtifactStore:
    name = "local"
    configured = True

    def __init__(self, output_dir: Path):
        self.output_dir = output_dir.resolve()

    def _path(self, key: str) -> Path:
        candidate = (self.output_dir / key).resolve()
        if self.output_dir not in candidate.parents:
            raise ValueError("Artifact path is outside the configured output directory.")
        return candidate

    def put_file(self, key: str, path: Path, media_type: str) -> bool:
        destination = self._path(key)
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            with path.open("rb") as source, destination.open("xb") as target:
                while chunk := source.read(1024 * 1024):
                    target.write(chunk)
            return True
        except FileExistsError:
            with path.open("rb") as source, destination.open("rb") as existing:
                if _same_content(source, existing):
                    return False
            raise ArtifactStorageError("An artifact already exists at this path with different content.")

    def open_file(self, key: str) -> IO[bytes]:
        return self._path(key).open("rb")

    def delete_objects(self, keys: list[str]) -> None:
        for key in keys:
            target = self._path(key)
            if target.is_file():
                target.unlink()

    def create_signed_url(self, key: str, expires_in: int) -> str:
        raise ArtifactStorageError("Signed downloads are available only for cloud artifacts.")

    def check(self) -> None:
        return None


class SupabaseArtifactStore:
    name = "supabase"
    configured = True

    def __init__(
        self,
        url: str,
        service_role_key: str,
        bucket: str = "satellite-analysis-results",
        client: httpx.Client | None = None,
    ):
        self.base_url = url.rstrip("/")
        self.bucket = bucket
        self._client = client or httpx.Client(
            timeout=30,
            headers={
                "apikey": service_role_key,
                "Authorization": f"Bearer {service_role_key}",
            },
            follow_redirects=False,
        )
        self._owns_client = client is None
        self._bucket_checked = False

    def _bucket_url(self) -> str:
        return f"{self.base_url}/storage/v1/bucket/{quote(self.bucket, safe='')}"

    def _object_url(self, key: str) -> str:
        safe_key = quote(_validate_object_key(key), safe="/")
        return f"{self.base_url}/storage/v1/object/{quote(self.bucket, safe='')}/{safe_key}"

    def _request(self, method: str, url: str, **kwargs) -> httpx.Response:
        for attempt in range(MAX_STORAGE_ATTEMPTS):
            request_body = kwargs.get("content")
            seek = getattr(request_body, "seek", None)
            if callable(seek):
                seek(0)
            try:
                response = self._client.request(method, url, **kwargs)
            except httpx.HTTPError as exc:
                if attempt + 1 == MAX_STORAGE_ATTEMPTS:
                    raise ArtifactStorageError(
                        "Supabase Storage could not be reached."
                    ) from exc
            else:
                if (
                    not _retryable_status(response.status_code)
                    or attempt + 1 == MAX_STORAGE_ATTEMPTS
                ):
                    return response
            time.sleep(0.05 * (attempt + 1))
        raise ArtifactStorageError("Supabase Storage could not be reached.")

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        if response.is_error:
            raise ArtifactStorageError(
                f"Supabase Storage returned HTTP {response.status_code}."
            )

    def ensure_private_bucket(self) -> None:
        response = self._request("GET", self._bucket_url())
        if response.status_code == 404:
            response = self._request(
                "POST",
                f"{self.base_url}/storage/v1/bucket",
                json={"id": self.bucket, "name": self.bucket, "public": False},
            )
            if response.status_code in {200, 201}:
                self._bucket_checked = True
                return
            if response.status_code not in {400, 409}:
                self._raise_for_status(response)
            response = self._request("GET", self._bucket_url())

        self._raise_for_status(response)
        self._validate_private_bucket(response)

    def put_file(self, key: str, path: Path, media_type: str) -> bool:
        self._ensure_bucket()
        with path.open("rb") as body:
            response = self._request(
                "POST",
                self._object_url(key),
                content=body,
                headers={"Content-Type": media_type, "x-upsert": "false"},
            )
        if response.status_code == 409:
            with path.open("rb") as source, self.open_file(key) as existing:
                if _same_content(source, existing):
                    return False
            raise ArtifactStorageError(
                "An artifact already exists at this path with different content."
            )
        self._raise_for_status(response)
        return True

    def open_file(self, key: str) -> IO[bytes]:
        self._ensure_bucket()
        for attempt in range(MAX_STORAGE_ATTEMPTS):
            spool = SpooledTemporaryFile(max_size=8 * 1024 * 1024, mode="w+b")
            try:
                with self._client.stream("GET", self._object_url(key)) as response:
                    if (
                        _retryable_status(response.status_code)
                        and attempt + 1 < MAX_STORAGE_ATTEMPTS
                    ):
                        retry = True
                    else:
                        retry = False
                        self._raise_for_status(response)
                    if not retry:
                        for chunk in response.iter_bytes(1024 * 1024):
                            spool.write(chunk)
                if retry:
                    spool.close()
                    time.sleep(0.05 * (attempt + 1))
                    continue
                spool.seek(0)
                return spool
            except httpx.HTTPError as exc:
                spool.close()
                if attempt + 1 == MAX_STORAGE_ATTEMPTS:
                    raise ArtifactStorageError(
                        "Supabase Storage could not be reached."
                    ) from exc
                time.sleep(0.05 * (attempt + 1))
            except ArtifactStorageError:
                spool.close()
                raise
        raise ArtifactStorageError("Supabase Storage could not be reached.")

    def delete_objects(self, keys: list[str]) -> None:
        if not keys:
            return
        self._ensure_bucket()
        for start in range(0, len(keys), 100):
            paths = [_validate_object_key(key) for key in keys[start : start + 100]]
            response = self._request(
                "DELETE",
                f"{self.base_url}/storage/v1/object/{quote(self.bucket, safe='')}",
                json={"prefixes": paths},
            )
            self._raise_for_status(response)

    def create_signed_url(self, key: str, expires_in: int) -> str:
        self._ensure_bucket()
        if expires_in < 1 or expires_in > 3600:
            raise ValueError("Signed URL expiry must be between 1 and 3600 seconds.")
        response = self._request(
            "POST",
            f"{self.base_url}/storage/v1/object/sign/{quote(self.bucket, safe='')}/"
            f"{quote(_validate_object_key(key), safe='/')}",
            json={"expiresIn": expires_in},
        )
        self._raise_for_status(response)
        try:
            signed_path = response.json()["signedURL"]
        except (ValueError, KeyError, TypeError) as exc:
            raise ArtifactStorageError("Supabase Storage returned an invalid signed URL.") from exc
        if not isinstance(signed_path, str) or not signed_path:
            raise ArtifactStorageError("Supabase Storage returned an invalid signed URL.")
        if signed_path.startswith(("https://", "http://")):
            signed_parts = urlsplit(signed_path)
            base_parts = urlsplit(self.base_url)
            if signed_parts.scheme != base_parts.scheme or signed_parts.netloc != base_parts.netloc:
                raise ArtifactStorageError("Supabase Storage returned an unexpected signed URL host.")
            return signed_path
        if signed_path.startswith("/storage/v1/"):
            return f"{self.base_url}{signed_path}"
        if signed_path.startswith("/"):
            return f"{self.base_url}/storage/v1{signed_path}"
        return urljoin(f"{self.base_url}/storage/v1/", signed_path)

    def list_objects(self, prefix: str, limit: int) -> tuple[list[str], bool]:
        if prefix != "results" or not 1 <= limit <= 10_000:
            raise ValueError("Consistency scans require the results prefix and a limit from 1 to 10000.")
        self.check()
        pending = [prefix]
        object_keys: list[str] = []
        scanned_entries = 0
        max_scanned_entries = limit * 4
        while pending:
            folder = pending.pop()
            offset = 0
            while True:
                page_limit = min(100, limit + 1 - len(object_keys))
                if page_limit <= 0 or scanned_entries >= max_scanned_entries:
                    return object_keys[:limit], True
                response = self._request(
                    "POST",
                    f"{self.base_url}/storage/v1/object/list/{quote(self.bucket, safe='')}",
                    json={
                        "prefix": folder,
                        "limit": page_limit,
                        "offset": offset,
                        "sortBy": {"column": "name", "order": "asc"},
                    },
                )
                self._raise_for_status(response)
                try:
                    entries = response.json()
                except ValueError as exc:
                    raise ArtifactStorageError(
                        "Supabase Storage returned invalid object-list metadata."
                    ) from exc
                if not isinstance(entries, list):
                    raise ArtifactStorageError(
                        "Supabase Storage returned invalid object-list metadata."
                    )
                scanned_entries += len(entries)
                for entry in entries:
                    if not isinstance(entry, dict) or not isinstance(entry.get("name"), str):
                        raise ArtifactStorageError(
                            "Supabase Storage returned invalid object-list metadata."
                        )
                    key = _validate_object_key(f"{folder}/{entry['name']}")
                    if entry.get("id") is None:
                        pending.append(key)
                    else:
                        object_keys.append(key)
                        if len(object_keys) > limit:
                            return object_keys[:limit], True
                    if scanned_entries >= max_scanned_entries:
                        return object_keys[:limit], True
                if len(entries) < page_limit:
                    break
                offset += len(entries)
        return object_keys, False

    def check(self) -> None:
        response = self._request("GET", self._bucket_url())
        if response.status_code == 404:
            raise ArtifactStorageError("The configured Supabase Storage bucket does not exist.")
        self._raise_for_status(response)
        self._validate_private_bucket(response)

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def _ensure_bucket(self) -> None:
        if not self._bucket_checked:
            self.ensure_private_bucket()

    def _validate_private_bucket(self, response: httpx.Response) -> None:
        try:
            bucket_info = response.json()
        except ValueError as exc:
            raise ArtifactStorageError(
                "Supabase Storage returned invalid bucket metadata."
            ) from exc
        if bucket_info.get("public") is not False:
            raise ArtifactStorageError(
                "The configured Supabase Storage bucket is public; private storage is required."
            )
        self._bucket_checked = True


def _validate_object_key(key: str) -> str:
    if (
        not key
        or key.startswith("/")
        or "\\" in key
        or any(part in {"", ".", ".."} for part in key.split("/"))
        or any(ord(character) < 32 for character in key)
    ):
        raise ValueError("Invalid artifact object path.")
    return key


def _same_content(left: IO[bytes], right: IO[bytes]) -> bool:
    left_hash = hashlib.sha256()
    right_hash = hashlib.sha256()
    while left_chunk := left.read(1024 * 1024):
        left_hash.update(left_chunk)
    while right_chunk := right.read(1024 * 1024):
        right_hash.update(right_chunk)
    return left_hash.digest() == right_hash.digest()


def _retryable_status(status_code: int) -> bool:
    return status_code in {408, 429} or status_code >= 500

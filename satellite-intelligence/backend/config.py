import ipaddress
import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    output_dir: Path
    api_host: str
    api_port: int
    cors_origins: list[str]
    satellite_provider: str
    copernicus_client_id: str | None
    copernicus_client_secret: str | None
    satellite_cache_dir: Path
    satellite_timeout: int
    satellite_max_cloud_cover: int
    database_url: str | None
    supabase_url: str | None
    supabase_service_role_key: str | None
    supabase_anon_key: str | None
    require_auth: bool
    supabase_storage_bucket: str
    max_upload_bytes: int = 512 * 1024 * 1024
    max_raster_bytes: int = 512 * 1024 * 1024
    max_artifact_bytes: int = 512 * 1024 * 1024
    max_raster_pixels: int = 1_000_000
    max_input_array_bytes: int = 128 * 1024 * 1024
    max_analysis_seconds: int = 300
    max_concurrent_analyses: int = 1
    database_pool_size: int = 3
    database_max_overflow: int = 1

    @property
    def authentication_required(self) -> bool:
        return bool(
            self.require_auth
            or self.database_url
            or self.supabase_url
            or self.supabase_anon_key
            or self.supabase_service_role_key
        )


BACKEND_DIR = Path(__file__).resolve().parent
DEFAULT_CORS_ORIGINS = (
    "http://localhost:5173,http://127.0.0.1:5173,"
    "http://localhost:4173,http://127.0.0.1:4173,"
    "https://team-s4-ten.vercel.app"
)
configured_cors_origins = [
    origin.strip()
    for origin in os.getenv(
        "CORS_ORIGINS",
        DEFAULT_CORS_ORIGINS,
    ).split(",")
    if origin.strip()
]


def _boolean_setting(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes"}:
        return True
    if normalized in {"0", "false", "no"}:
        return False
    raise ValueError(f"{name} must be one of: true, false, 1, 0, yes, or no.")


def is_valid_supabase_url(value: str | None) -> bool:
    if not value or value != value.strip():
        return False
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError:
        return False
    if (
        not hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or port == 0
    ):
        return False
    if parsed.scheme == "https":
        return True
    if parsed.scheme != "http":
        return False
    try:
        loopback = ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        loopback = hostname.lower() == "localhost"
    return loopback


settings = Settings(
    data_dir=Path(os.getenv("DATA_DIR", BACKEND_DIR / "data")).expanduser().resolve(),
    output_dir=Path(
        os.getenv("OUTPUT_DIR", BACKEND_DIR / "data" / "outputs")
    ).expanduser().resolve(),
    api_host=os.getenv("API_HOST", "127.0.0.1"),
    api_port=int(os.getenv("API_PORT", "8000")),
    cors_origins=configured_cors_origins,
    satellite_provider=os.getenv("SATELLITE_PROVIDER", "local").strip().lower() or "local",
    copernicus_client_id=os.getenv("COPERNICUS_CLIENT_ID") or None,
    copernicus_client_secret=os.getenv("COPERNICUS_CLIENT_SECRET") or None,
    satellite_cache_dir=Path(os.getenv("SATELLITE_CACHE_DIR", BACKEND_DIR / "data" / "live")).expanduser().resolve(),
    satellite_timeout=int(os.getenv("SATELLITE_TIMEOUT", "30")),
    satellite_max_cloud_cover=int(os.getenv("SATELLITE_MAX_CLOUD_COVER", "20")),
    database_url=os.getenv("DATABASE_URL") or None,
    supabase_url=os.getenv("SUPABASE_URL") or None,
    supabase_service_role_key=os.getenv("SUPABASE_SERVICE_ROLE_KEY") or None,
    supabase_anon_key=os.getenv("SUPABASE_ANON_KEY") or None,
    require_auth=_boolean_setting("REQUIRE_AUTH", default=False),
    supabase_storage_bucket=os.getenv(
        "SUPABASE_STORAGE_BUCKET", "satellite-analysis-results"
    ).strip(),
    max_upload_bytes=int(os.getenv("MAX_UPLOAD_BYTES", str(512 * 1024 * 1024))),
    max_raster_bytes=int(os.getenv("MAX_RASTER_BYTES", str(512 * 1024 * 1024))),
    max_artifact_bytes=int(os.getenv("MAX_ARTIFACT_BYTES", str(512 * 1024 * 1024))),
    max_raster_pixels=int(os.getenv("MAX_RASTER_PIXELS", "1000000")),
    max_input_array_bytes=int(os.getenv("MAX_INPUT_ARRAY_BYTES", str(128 * 1024 * 1024))),
    max_analysis_seconds=int(os.getenv("MAX_ANALYSIS_SECONDS", "300")),
    max_concurrent_analyses=int(os.getenv("MAX_CONCURRENT_ANALYSES", "1")),
    database_pool_size=int(os.getenv("DATABASE_POOL_SIZE", "3")),
    database_max_overflow=int(os.getenv("DATABASE_MAX_OVERFLOW", "1")),
)


def validate_runtime_settings() -> list[str]:
    issues: list[str] = []
    if settings.api_port <= 0 or settings.api_port > 65535:
        issues.append("API_PORT must be an integer between 1 and 65535.")
    if settings.satellite_provider not in {"local", "live", "mock"}:
        issues.append("SATELLITE_PROVIDER must be one of: local, live, or mock.")
    if settings.satellite_timeout <= 0:
        issues.append("SATELLITE_TIMEOUT must be positive.")
    if settings.satellite_max_cloud_cover < 0 or settings.satellite_max_cloud_cover > 100:
        issues.append("SATELLITE_MAX_CLOUD_COVER must be between 0 and 100.")
    if settings.max_upload_bytes <= 0:
        issues.append("MAX_UPLOAD_BYTES must be positive.")
    if settings.max_raster_bytes <= 0:
        issues.append("MAX_RASTER_BYTES must be positive.")
    if settings.max_artifact_bytes <= 0:
        issues.append("MAX_ARTIFACT_BYTES must be positive.")
    if settings.max_raster_pixels <= 0:
        issues.append("MAX_RASTER_PIXELS must be positive.")
    if settings.max_input_array_bytes <= 0:
        issues.append("MAX_INPUT_ARRAY_BYTES must be positive.")
    if settings.max_analysis_seconds < 1 or settings.max_analysis_seconds > 3600:
        issues.append("MAX_ANALYSIS_SECONDS must be between 1 and 3600.")
    if settings.max_concurrent_analyses < 1 or settings.max_concurrent_analyses > 8:
        issues.append("MAX_CONCURRENT_ANALYSES must be between 1 and 8.")
    if settings.database_pool_size < 1 or settings.database_pool_size > 10:
        issues.append("DATABASE_POOL_SIZE must be between 1 and 10.")
    if settings.database_max_overflow < 0 or settings.database_max_overflow > 10:
        issues.append("DATABASE_MAX_OVERFLOW must be between 0 and 10.")
    if settings.database_pool_size + settings.database_max_overflow > 10:
        issues.append(
            "DATABASE_POOL_SIZE plus DATABASE_MAX_OVERFLOW must not exceed 10 connections per process."
        )
    if not settings.supabase_storage_bucket:
        issues.append("SUPABASE_STORAGE_BUCKET must not be empty.")
    if settings.supabase_url and not is_valid_supabase_url(settings.supabase_url):
        issues.append(
            "SUPABASE_URL must be a valid HTTPS URL; plain HTTP is allowed only for loopback development."
        )
    if settings.authentication_required and (
        not settings.supabase_url or not settings.supabase_anon_key
    ):
        issues.append(
            "Authentication requires both SUPABASE_URL and SUPABASE_ANON_KEY."
        )
    if settings.authentication_required and not settings.database_url:
        issues.append("Authentication requires DATABASE_URL for private analysis persistence.")
    if settings.authentication_required and not settings.supabase_service_role_key:
        issues.append(
            "Authentication requires SUPABASE_SERVICE_ROLE_KEY for private artifact storage."
        )
    for path in (settings.data_dir, settings.output_dir, settings.satellite_cache_dir):
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            issues.append(f"Unable to create directory {path}: {exc}")
    return issues


for directory in (settings.data_dir / "current", settings.data_dir / "historical", settings.output_dir, settings.satellite_cache_dir):
    directory.mkdir(parents=True, exist_ok=True)

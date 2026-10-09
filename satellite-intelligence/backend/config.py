import os
from dataclasses import dataclass
from pathlib import Path


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
    max_upload_bytes: int = 512 * 1024 * 1024


BACKEND_DIR = Path(__file__).resolve().parent
PRODUCTION_FRONTEND_ORIGIN = "https://team-s4-ten.vercel.app"
configured_cors_origins = [
    origin.strip()
    for origin in os.getenv(
        "CORS_ORIGINS",
        "http://localhost:5173,http://127.0.0.1:5173,http://localhost:4173,http://127.0.0.1:4173",
    ).split(",")
    if origin.strip()
]
if PRODUCTION_FRONTEND_ORIGIN not in configured_cors_origins:
    configured_cors_origins.append(PRODUCTION_FRONTEND_ORIGIN)

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
    max_upload_bytes=int(os.getenv("MAX_UPLOAD_BYTES", str(512 * 1024 * 1024))),
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
    for path in (settings.data_dir, settings.output_dir, settings.satellite_cache_dir):
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            issues.append(f"Unable to create directory {path}: {exc}")
    return issues


for directory in (settings.data_dir / "current", settings.data_dir / "historical", settings.output_dir, settings.satellite_cache_dir):
    directory.mkdir(parents=True, exist_ok=True)

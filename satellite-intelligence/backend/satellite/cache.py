from __future__ import annotations

import json
import re
from pathlib import Path

import rasterio

from config import settings


class CacheManager:
    def __init__(self, base_dir: Path | str | None = None):
        base_path = Path(base_dir or settings.satellite_cache_dir).expanduser().resolve()
        base_path.mkdir(parents=True, exist_ok=True)
        self.base_dir = base_path

    def _safe_product_id(self, product_id: str) -> str:
        if not product_id or not re.fullmatch(r"[A-Za-z0-9_.-]+", product_id):
            raise ValueError("Invalid satellite product ID.")
        return product_id

    def product_dir(self, product_id: str) -> Path:
        if product_id is None or not isinstance(product_id, str):
            raise ValueError("Invalid satellite product ID.")
        if product_id in {".", ".."} or "/" in product_id or "\\" in product_id:
            raise ValueError("Cache path escapes the configured satellite cache directory.")
        safe_id = self._safe_product_id(product_id)
        target = (self.base_dir / safe_id).resolve()
        if self.base_dir not in target.parents and target != self.base_dir:
            raise ValueError("Cache path escapes the configured satellite cache directory.")
        return target

    def list_products(self) -> list[dict]:
        products = []
        if not self.base_dir.exists():
            return products
        for path in sorted(self.base_dir.iterdir(), key=lambda item: item.name.lower()):
            if not path.is_dir():
                continue
            metadata_path = path / "metadata.json"
            if metadata_path.is_file():
                try:
                    products.append(json.loads(metadata_path.read_text(encoding="utf-8")))
                except (OSError, json.JSONDecodeError):
                    continue
        return products

    def get_product(self, product_id: str) -> dict | None:
        metadata_path = self.product_dir(product_id) / "metadata.json"
        if not metadata_path.is_file():
            return None
        try:
            return json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def delete_product(self, product_id: str) -> bool:
        product_dir = self.product_dir(product_id)
        if not product_dir.exists():
            return False
        for child in sorted(product_dir.iterdir(), reverse=True):
            if child.is_file() or child.is_symlink():
                child.unlink()
            elif child.is_dir():
                for nested in sorted(child.rglob("*"), reverse=True):
                    if nested.is_file() or nested.is_symlink():
                        nested.unlink()
                child.rmdir()
        product_dir.rmdir()
        return True

    def write_metadata(self, product_id: str, payload: dict) -> Path:
        product_dir = self.product_dir(product_id)
        product_dir.mkdir(parents=True, exist_ok=True)
        metadata_path = product_dir / "metadata.json"
        metadata_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return metadata_path

    def validate_cached_product(self, product_id: str, required_bands: tuple[str, ...] = ()) -> dict:
        product_dir = self.product_dir(product_id)
        if not product_dir.exists():
            raise FileNotFoundError("Cached product directory was not found.")
        metadata = self.get_product(product_id) or {}
        missing = []
        for band in required_bands:
            band_path = product_dir / f"{band}.tif"
            if not band_path.is_file():
                missing.append(band)
                continue
            try:
                with rasterio.open(band_path) as src:
                    if src.count < 1 or src.width < 1 or src.height < 1:
                        raise ValueError(f"Raster {band_path.name} is empty or unreadable.")
                    if src.crs is None:
                        raise ValueError(f"Raster {band_path.name} does not contain a CRS.")
                    if src.transform is None:
                        raise ValueError(f"Raster {band_path.name} does not contain a valid transform.")
                    if src.res[0] <= 0 or src.res[1] <= 0:
                        raise ValueError(f"Raster {band_path.name} has invalid resolution values.")
            except (OSError, ValueError, rasterio.errors.RasterioError) as exc:
                raise ValueError(f"Raster {band_path.name} failed validation: {exc}") from exc
        if missing:
            raise ValueError(f"Cached product is missing required bands: {', '.join(missing)}")
        return {"product_id": product_id, "cached": True, "metadata": metadata}

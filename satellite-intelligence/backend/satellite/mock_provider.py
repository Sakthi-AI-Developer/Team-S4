from __future__ import annotations

from datetime import datetime

import numpy as np
import rasterio
from rasterio.transform import from_origin

from config import settings
from satellite.base_provider import SatelliteProvider
from satellite.cache import CacheManager
from satellite.models import SatelliteProduct


class MockSatelliteProvider(SatelliteProvider):
    name = "mock"

    def __init__(self, cache_manager: CacheManager | None = None):
        self.cache_manager = cache_manager or CacheManager(settings.satellite_cache_dir)

    @property
    def configured(self) -> bool:
        return True

    def status(self) -> dict:
        return {
            "provider": self.name,
            "configured": True,
            "mode": "mock",
            "message": "Mock satellite provider is active for automated validation only.",
        }

    def search(self, request, **kwargs) -> list[dict]:
        product_id = f"mock-s2-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
        product = SatelliteProduct(
            product_id=product_id,
            acquisition_date="2024-02-14",
            cloud_cover=12.5,
            platform="Sentinel-2",
            processing_level="L2A",
            spatial_coverage="Mock AOI",
            product_size_mb=18.4,
            available_bands=["B02", "B03", "B04", "B08", "B11"],
            provider=self.name,
            available=True,
            metadata={"source": "mock"},
        )
        return [product.to_dict()]

    def download(self, product_id: str, **kwargs) -> dict:
        product_dir = self.cache_manager.product_dir(product_id)
        product_dir.mkdir(parents=True, exist_ok=True)
        for band in ("B02", "B03", "B04", "B08", "B11"):
            path = product_dir / f"{band}.tif"
            data = np.full((2, 2), 0.2 + (ord(band[1]) % 5) * 0.1, dtype=np.float32)
            with rasterio.open(
                path,
                "w",
                driver="GTiff",
                height=2,
                width=2,
                count=1,
                dtype="float32",
                crs="EPSG:32644",
                transform=from_origin(500000, 3000000, 10, 10),
                nodata=-9999,
            ) as dst:
                dst.write(data, 1)
        payload = {
            "product_id": product_id,
            "provider": self.name,
            "acquisition_date": "2024-02-14",
            "cloud_cover": 12.5,
            "platform": "Sentinel-2",
            "available_bands": ["B02", "B03", "B04", "B08", "B11"],
            "available": True,
            "metadata": {"source": "mock"},
        }
        self.cache_manager.write_metadata(product_id, payload)
        return {
            "success": True,
            "product_id": product_id,
            "provider": self.name,
            "bands": payload["available_bands"],
            "cache_dir": str(product_dir),
        }

    def list_products(self) -> list[dict]:
        return self.cache_manager.list_products()

    def get_product(self, product_id: str) -> dict:
        payload = self.cache_manager.get_product(product_id)
        if payload is None:
            raise KeyError(f"No cached mock product named {product_id}.")
        return payload

from __future__ import annotations

import numpy as np
import rasterio
from rasterio.transform import from_origin

from config import settings
from satellite.base_provider import SatelliteProvider
from satellite.cache import CacheManager
from satellite.models import SatelliteProduct


class MockSatelliteProvider(SatelliteProvider):
    name = "mock"
    PRODUCT_ID = "synthetic-demo-fixture-v1"
    LOCAL_CRS = (
        'LOCAL_CS["Synthetic demo grid",'
        'LOCAL_DATUM["Synthetic local datum",0],UNIT["metre",1],'
        'AXIS["Easting",EAST],AXIS["Northing",NORTH]]'
    )

    def __init__(self, cache_manager: CacheManager | None = None):
        self.cache_manager = cache_manager or CacheManager(settings.satellite_cache_dir)

    @property
    def configured(self) -> bool:
        return True

    def status(self) -> dict:
        return {
            "provider": self.name,
            "configured": True,
            "available": True,
            "mode": "mock",
            "data_classification": "synthetic",
            "message": "A synthetic fixture is available; it is not a live satellite observation.",
        }

    def search(self, request, **kwargs) -> list[dict]:
        product = SatelliteProduct(
            product_id=self.PRODUCT_ID,
            platform="Synthetic demo fixture",
            spatial_coverage="None; the fixture has no real-world coordinates.",
            available_bands=["B02", "B03", "B04", "B08", "B11"],
            provider=self.name,
            available=True,
            metadata={
                "source": "deterministic synthetic fixture",
                "data_classification": "synthetic",
                "acquisition_date": None,
                "cloud_cover": None,
                "geographic_coverage": None,
            },
        )
        return [product.to_dict()]

    def download(self, product_id: str, **kwargs) -> dict:
        if product_id != self.PRODUCT_ID:
            raise KeyError("No synthetic demo fixture was found for that product ID.")
        product_dir = self.cache_manager.product_dir(product_id)
        product_dir.mkdir(parents=True, exist_ok=True)
        if all((product_dir / f"{band}.tif").is_file() for band in ("B02", "B03", "B04", "B08", "B11")):
            return {
                "success": True,
                "product_id": product_id,
                "provider": self.name,
                "bands": ["B02", "B03", "B04", "B08", "B11"],
                "cache_dir": str(product_dir),
                "data_classification": "synthetic",
                "message": "Existing deterministic synthetic fixture reused.",
            }
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
                crs=self.LOCAL_CRS,
                transform=from_origin(0, 2, 1, 1),
                nodata=-9999,
            ) as dst:
                dst.write(data, 1)
                dst.update_tags(
                    SATELLITE_VISION_DATA_KIND="synthetic",
                    SOURCE="deterministic mock satellite fixture",
                )
        payload = {
            "product_id": product_id,
            "provider": self.name,
            "acquisition_date": None,
            "cloud_cover": None,
            "platform": "Synthetic demo fixture",
            "available_bands": ["B02", "B03", "B04", "B08", "B11"],
            "available": True,
            "metadata": {
                "source": "deterministic synthetic fixture",
                "data_classification": "synthetic",
                "geographic_coverage": None,
            },
        }
        self.cache_manager.write_metadata(product_id, payload)
        return {
            "success": True,
            "product_id": product_id,
            "provider": self.name,
            "bands": payload["available_bands"],
            "cache_dir": str(product_dir),
            "data_classification": "synthetic",
        }

    def list_products(self) -> list[dict]:
        return self.cache_manager.list_products()

    def get_product(self, product_id: str) -> dict:
        payload = self.cache_manager.get_product(product_id)
        if payload is None:
            raise KeyError(f"No cached mock product named {product_id}.")
        return payload

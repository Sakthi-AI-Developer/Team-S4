from __future__ import annotations

from config import settings
from satellite.base_provider import SatelliteProvider


class LocalSatelliteProvider(SatelliteProvider):
    name = "local"

    @property
    def configured(self) -> bool:
        return True

    def status(self) -> dict:
        return {
            "provider": self.name,
            "configured": True,
            "mode": "local",
            "message": "Stage-A local dataset mode is active.",
            "cache_dir": str(settings.satellite_cache_dir),
        }

    def search(self, request, **kwargs) -> list[dict]:
        dataset_dir = settings.data_dir / "current"
        available = dataset_dir.exists() and bool(list(dataset_dir.glob("*.tif")))
        if not available:
            return []
        return [{
            "product_id": "local-current",
            "acquisition_date": None,
            "cloud_cover": None,
            "platform": "Local",
            "processing_level": "Stage-A",
            "spatial_coverage": "Current local GeoTIFF dataset",
            "product_size_mb": None,
            "available_bands": ["B02", "B03", "B04", "B08", "B11"],
            "provider": self.name,
            "available": True,
            "metadata": {
                "source": "existing local GeoTIFF dataset",
                "data_classification": "unverified_local_input",
                "acquisition_date": None,
            },
        }]

    def download(self, product_id: str, **kwargs) -> dict:
        if product_id != "local-current":
            raise ValueError("Local dataset mode only supports the current local product.")
        return {
            "product_id": product_id,
            "provider": self.name,
            "source": "local",
            "cached": True,
            "message": "Using the existing local dataset.",
            "bands": ["B02", "B03", "B04", "B08", "B11"],
        }

    def list_products(self) -> list[dict]:
        return self.search(None)

    def get_product(self, product_id: str) -> dict:
        products = self.search(None)
        for item in products:
            if item["product_id"] == product_id:
                return item
        raise KeyError(f"Unknown local product: {product_id}")

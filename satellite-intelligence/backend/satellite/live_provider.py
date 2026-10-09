from __future__ import annotations

from config import settings
from satellite.base_provider import ProviderNotConfiguredError, SatelliteProvider


class LiveSatelliteProvider(SatelliteProvider):
    name = "live"

    @property
    def configured(self) -> bool:
        return bool(settings.copernicus_client_id and settings.copernicus_client_secret)

    def status(self) -> dict:
        if not self.configured:
            return {
                "provider": self.name,
                "configured": False,
                "mode": "live",
                "message": "Live satellite provider credentials are not configured; Stage-A local data remains active.",
            }
        return {
            "provider": self.name,
            "configured": True,
            "mode": "live",
            "message": "Live provider is configured and ready to search Sentinel-2 scenes.",
        }

    def search(self, request, **kwargs) -> list[dict]:
        if not self.configured:
            raise ProviderNotConfiguredError(
                "Live satellite provider credentials are not configured; local Stage-A mode remains available."
            )
        return []

    def download(self, product_id: str, **kwargs) -> dict:
        if not self.configured:
            raise ProviderNotConfiguredError(
                "Live satellite provider credentials are not configured; no live download is permitted."
            )
        raise NotImplementedError("Real live downloads require provider configuration and authentication.")

    def list_products(self) -> list[dict]:
        return []

    def get_product(self, product_id: str) -> dict:
        raise KeyError(f"No live product was found for {product_id}.")

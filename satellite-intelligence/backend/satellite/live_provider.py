from __future__ import annotations

from config import settings
from satellite.base_provider import (
    ProviderCapabilityUnavailableError,
    ProviderNotConfiguredError,
    SatelliteProvider,
)


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
                "available": False,
                "mode": "live",
                "message": "Live satellite provider credentials are not configured; Stage-A local data remains active.",
            }
        return {
            "provider": self.name,
            "configured": True,
            "available": False,
            "mode": "live",
            "message": (
                "Provider credentials are present, but live scene search and download "
                "are not implemented; no live results have been queried."
            ),
        }

    def search(self, request, **kwargs) -> list[dict]:
        if not self.configured:
            raise ProviderNotConfiguredError(
                "Live satellite provider credentials are not configured; local Stage-A mode remains available."
            )
        raise ProviderCapabilityUnavailableError(
            "Live scene search is not implemented; no satellite-provider results were queried."
        )

    def download(self, product_id: str, **kwargs) -> dict:
        if not self.configured:
            raise ProviderNotConfiguredError(
                "Live satellite provider credentials are not configured; no live download is permitted."
            )
        raise ProviderCapabilityUnavailableError(
            "Live satellite downloads are not implemented; no scene was downloaded."
        )

    def list_products(self) -> list[dict]:
        return []

    def get_product(self, product_id: str) -> dict:
        raise KeyError(f"No live product was found for {product_id}.")

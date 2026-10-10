from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class ProviderNotConfiguredError(RuntimeError):
    """Raised when a live provider is unavailable because credentials or setup are missing."""


class ProviderCapabilityUnavailableError(RuntimeError):
    """Raised when a configured provider does not yet implement a requested operation."""


class SatelliteProvider(ABC):
    name = "provider"

    @property
    def configured(self) -> bool:
        return True

    @abstractmethod
    def status(self) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def search(self, request: Any, **kwargs) -> list[dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    def download(self, product_id: str, **kwargs) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def list_products(self) -> list[dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    def get_product(self, product_id: str) -> dict[str, Any]:
        raise NotImplementedError

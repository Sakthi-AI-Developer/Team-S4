from pathlib import Path
from typing import Protocol

from processing.preprocessing import SatelliteDataset, load_dataset


class SatelliteDataProvider(Protocol):
    def load(self, period: str, required: tuple[str, ...] = ()) -> SatelliteDataset: ...


class LocalDataProvider:
    """Stage A provider for pre-downloaded rasters in data/current and data/historical."""

    def __init__(self, data_dir: Path):
        self.data_dir = data_dir.resolve()

    def load(self, period: str, required: tuple[str, ...] = ()) -> SatelliteDataset:
        if period not in {"current", "historical"}:
            raise ValueError("Dataset period must be 'current' or 'historical'.")
        return load_dataset(self.data_dir / period, required)

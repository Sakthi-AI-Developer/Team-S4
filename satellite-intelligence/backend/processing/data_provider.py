from pathlib import Path
from typing import Callable, Protocol

from processing.preprocessing import SatelliteDataset, load_dataset


class SatelliteDataProvider(Protocol):
    def load(
        self,
        period: str,
        required: tuple[str, ...] = (),
        *,
        include_other_bands: bool = True,
        max_pixels: int | None = None,
        max_file_bytes: int | None = None,
        max_input_array_bytes: int | None = None,
        deadline_check: Callable[[], None] | None = None,
    ) -> SatelliteDataset: ...


class LocalDataProvider:
    """Stage A provider for pre-downloaded rasters in data/current and data/historical."""

    def __init__(self, data_dir: Path):
        self.data_dir = data_dir.resolve()

    def load(
        self,
        period: str,
        required: tuple[str, ...] = (),
        *,
        include_other_bands: bool = True,
        max_pixels: int | None = None,
        max_file_bytes: int | None = None,
        max_input_array_bytes: int | None = None,
        deadline_check: Callable[[], None] | None = None,
    ) -> SatelliteDataset:
        if period not in {"current", "historical"}:
            raise ValueError("Dataset period must be 'current' or 'historical'.")
        return load_dataset(
            self.data_dir / period,
            required,
            include_other_bands=include_other_bands,
            max_pixels=max_pixels,
            max_file_bytes=max_file_bytes,
            max_input_array_bytes=max_input_array_bytes,
            deadline_check=deadline_check,
        )

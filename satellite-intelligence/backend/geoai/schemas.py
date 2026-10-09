from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class SpatialAnalysisRequest(BaseModel):
    dataset_id: str = "current"
    include_history: bool = True
    block_size: int = Field(default=10, ge=1, le=256)
    aoi: dict[str, Any] | None = None


class VegetationForecastRequest(BaseModel):
    observations: list[dict[str, Any]] | None = None
    horizon_days: int = Field(default=90, ge=1, le=3650)
    dataset_id: str = "historical"
    lookback_limit: int | None = None


class TransitionRequest(BaseModel):
    current_dataset_id: str = "current"
    historical_dataset_id: str = "historical"
    include_area: bool = True


class GeoAIStatus(BaseModel):
    status: str = "ok"
    available: bool = True
    components: list[str]
    endpoints: list[str]

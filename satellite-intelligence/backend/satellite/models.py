from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class GeoJSONPolygon(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["Polygon", "MultiPolygon"]
    coordinates: list[Any]

    @model_validator(mode="after")
    def validate_geometry(self) -> "GeoJSONPolygon":
        if self.type == "Polygon":
            rings = self.coordinates
        elif self.type == "MultiPolygon":
            rings = [ring for polygon in self.coordinates for ring in polygon]
        else:
            raise ValueError("AOI geometry type must be Polygon or MultiPolygon.")

        if not rings:
            raise ValueError("AOI geometry must contain at least one ring.")

        for ring in rings:
            if len(ring) < 4:
                raise ValueError("AOI polygon rings must contain at least four coordinates.")
            if ring[0] != ring[-1]:
                raise ValueError("AOI polygon rings must be closed.")
            for point in ring:
                if len(point) != 2:
                    raise ValueError("Each AOI coordinate must contain longitude and latitude.")
                lon, lat = point
                if not (-180 <= lon <= 180 and -90 <= lat <= 90):
                    raise ValueError("AOI coordinates must fall within valid geographic bounds.")

        bbox = self.bounds
        width = abs(bbox[2] - bbox[0])
        height = abs(bbox[3] - bbox[1])
        if width > 30 or height > 30:
            raise ValueError("AOI extent is too large for a single analysis region.")
        if width <= 0 or height <= 0:
            raise ValueError("AOI bounds are invalid.")
        return self

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        flattened = [point for ring in self._rings for point in ring]
        lons = [point[0] for point in flattened]
        lats = [point[1] for point in flattened]
        return min(lons), min(lats), max(lons), max(lats)

    @property
    def _rings(self) -> list[list[list[float]]]:
        if self.type == "Polygon":
            return self.coordinates if self.coordinates else []
        return [ring for polygon in self.coordinates for ring in polygon]

    def estimate_area_km2(self) -> float:
        min_lon, min_lat, max_lon, max_lat = self.bounds
        width_km = abs(max_lon - min_lon) * 111.32 * max(abs(min_lat), abs(max_lat)) / 90
        height_km = abs(max_lat - min_lat) * 111.32
        return max(width_km * height_km, 0.0)


class SatelliteSearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    aoi: GeoJSONPolygon
    start_date: date
    end_date: date
    max_cloud_cover: float = Field(default=20.0, ge=0.0, le=100.0)
    satellite: str = "Sentinel-2"
    product_type: str = "S2MSI2A"

    @model_validator(mode="after")
    def validate_dates(self) -> "SatelliteSearchRequest":
        if self.start_date > self.end_date:
            raise ValueError("start_date must be on or before end_date.")
        return self


class SatelliteDownloadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product_id: str = Field(..., min_length=1)
    aoi: GeoJSONPolygon | None = None


class SatelliteProduct(BaseModel):
    product_id: str
    acquisition_date: str
    cloud_cover: float | None = None
    platform: str
    processing_level: str | None = None
    spatial_coverage: str | None = None
    product_size_mb: float | None = None
    available_bands: list[str] = Field(default_factory=list)
    provider: str = "live"
    available: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = self.model_dump()
        payload["available"] = bool(self.available)
        return payload

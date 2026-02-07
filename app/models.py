from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator


# --- Entity Models ---


class EntityIn(BaseModel):
    """Input model for creating/updating an entity."""

    type: str = Field(..., description="Entity type, e.g. 'location.gps', 'event', 'photo'")
    t_start: datetime = Field(..., description="Start timestamp (UTC)")
    t_end: datetime | None = Field(None, description="End timestamp (UTC) for spans; null = instantaneous")
    lat: float | None = Field(None, ge=-90, le=90, description="Latitude (WGS84)")
    lon: float | None = Field(None, ge=-180, le=180, description="Longitude (WGS84)")
    name: str | None = None
    color: str | None = Field(None, description="Color in #RRGGBB format")
    render_offset: float | None = None
    source: str | None = Field(None, description="Source identifier for idempotent upserts")
    external_id: str | None = Field(None, description="External ID from source for idempotent upserts")
    payload: dict[str, Any] | None = Field(None, description="Type-specific JSON data")

    @model_validator(mode="after")
    def validate_time_range(self) -> "EntityIn":
        if self.t_end is not None and self.t_end < self.t_start:
            raise ValueError("t_end must be >= t_start")
        return self

    @model_validator(mode="after")
    def validate_location(self) -> "EntityIn":
        if (self.lat is None) != (self.lon is None):
            raise ValueError("lat and lon must both be provided or both be null")
        return self


class EntityOut(BaseModel):
    """Output model for entity responses."""

    id: UUID
    type: str
    t_start: datetime
    t_end: datetime | None = None
    lat: float | None = None
    lon: float | None = None
    name: str | None = None
    color: str | None = None
    render_offset: float | None = None
    source: str | None = None
    external_id: str | None = None
    payload: dict[str, Any] | None = None


class EntityResponse(BaseModel):
    """Response for single entity operations."""

    id: UUID
    status: Literal["inserted", "updated"]


class BatchEntityResponse(BaseModel):
    """Response for batch entity operations."""

    inserted: int
    updated: int
    errors: int
    total: int


# --- Query Models ---


class ResampleConfig(BaseModel):
    """Configuration for resampling query results."""

    method: Literal["none", "uniform_time"] = "none"
    n: int | None = Field(None, ge=1, le=10000, description="Number of samples for uniform_time")

    @model_validator(mode="after")
    def validate_resample(self) -> "ResampleConfig":
        if self.method == "uniform_time" and self.n is None:
            raise ValueError("n is required when method is 'uniform_time'")
        return self


class TimeQueryRequest(BaseModel):
    """Request model for time-based queries."""

    types: list[str] = Field(..., min_length=1, description="Entity types to query")
    start: datetime = Field(..., description="Start of time window (UTC)")
    end: datetime = Field(..., description="End of time window (UTC)")
    limit: int = Field(2000, ge=1, le=10000, description="Maximum results to return")
    order: Literal["t_start_asc", "t_start_desc"] = "t_start_asc"
    resample: ResampleConfig | None = None

    @model_validator(mode="after")
    def validate_time_range(self) -> "TimeQueryRequest":
        if self.end <= self.start:
            raise ValueError("end must be > start")
        return self


class TimeWindow(BaseModel):
    """Optional time window for bbox queries."""

    start: datetime
    end: datetime


class BBoxQueryRequest(BaseModel):
    """Request model for spatial bounding box queries."""

    types: list[str] = Field(..., min_length=1, description="Entity types to query")
    bbox: list[float] = Field(
        ...,
        min_length=4,
        max_length=4,
        description="Bounding box [minLon, minLat, maxLon, maxLat]",
    )
    time: TimeWindow | None = Field(None, description="Optional time window filter")
    limit: int = Field(5000, ge=1, le=10000, description="Maximum results to return")
    order: Literal["t_start_asc", "t_start_desc", "random"] = "t_start_desc"

    @model_validator(mode="after")
    def validate_bbox(self) -> "BBoxQueryRequest":
        if len(self.bbox) != 4:
            raise ValueError("bbox must have exactly 4 values")
        min_lon, min_lat, max_lon, max_lat = self.bbox
        if not (-180 <= min_lon <= 180 and -180 <= max_lon <= 180):
            raise ValueError("longitude must be between -180 and 180")
        if not (-90 <= min_lat <= 90 and -90 <= max_lat <= 90):
            raise ValueError("latitude must be between -90 and 90")
        if min_lon >= max_lon:
            raise ValueError("minLon must be < maxLon")
        if min_lat >= max_lat:
            raise ValueError("minLat must be < maxLat")
        return self


class QueryResponse(BaseModel):
    """Response model for query endpoints."""

    entities: list[EntityOut]

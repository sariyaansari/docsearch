from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, Field


class DocumentCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=500)
    content: str = Field(..., min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)


class DocumentResponse(BaseModel):
    id: UUID
    tenant_id: str
    title: str
    content: str
    metadata: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class SearchResultItem(BaseModel):
    id: UUID
    title: str
    snippet: str  # ts_headline highlighted excerpt
    score: float
    metadata: dict[str, Any]
    created_at: datetime


class SearchResponse(BaseModel):
    query: str
    tenant_id: str
    total_results: int
    limit: int
    offset: int
    took_ms: float
    cached: bool
    results: list[SearchResultItem]


class HealthDependency(BaseModel):
    status: str  # "up" | "down"
    latency_ms: Optional[float] = None
    detail: Optional[str] = None


class HealthResponse(BaseModel):
    status: str  # "healthy" | "degraded" | "unhealthy"
    dependencies: dict[str, HealthDependency]


class ErrorResponse(BaseModel):
    error: str
    detail: Optional[str] = None

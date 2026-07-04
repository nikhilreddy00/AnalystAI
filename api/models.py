"""Shared API-level request/response models (distinct from agent data contracts)."""

from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: str
    env: str


class ErrorResponse(BaseModel):
    error: str
    detail: str | None = None

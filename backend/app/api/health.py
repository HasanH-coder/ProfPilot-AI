"""Service status endpoints: basic API info and a health check."""

from fastapi import APIRouter

from app.core.config import settings
from app.schemas.health import ApiInfo, HealthStatus

router = APIRouter(tags=["health"])


@router.get("/")
async def read_root() -> ApiInfo:
    return ApiInfo(name=settings.app_name, status="running")


@router.get("/health")
async def health_check() -> HealthStatus:
    return HealthStatus(status="healthy")

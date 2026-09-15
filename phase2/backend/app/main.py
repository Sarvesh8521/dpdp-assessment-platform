"""Main FastAPI application.

PHASE2_SPEC Section 1:
  - Clean API layer
  - Tenant isolation
  - Security headers
"""

from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1 import assessments, auth, auth_google, clients, integrations, audit_logs
from app.core.config import get_settings
from app.core.logging import setup_logging, get_logger

logger = get_logger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan events."""
    setup_logging()
    logger.info("Application starting up")
    yield
    logger.info("Application shutting down")

def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    settings = get_settings()

    app = FastAPI(
        title="DPDP Assessment Platform API",
        description="Phase 2 of the DPDP scanner",
        version="2.0.0",
        lifespan=lifespan,
    )

    # Configure CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # API Routers
    app.include_router(assessments.router, prefix="/api/v1")
    app.include_router(auth.router, prefix="/api/v1")
    app.include_router(auth_google.router, prefix="/api/v1")
    app.include_router(clients.router, prefix="/api/v1")
    app.include_router(integrations.router, prefix="/api/v1")
    app.include_router(audit_logs.router, prefix="/api/v1")

    @app.get("/health")
    async def health_check() -> dict[str, str]:
        return {"status": "healthy"}

    return app

# Application entrypoint
app = create_app()

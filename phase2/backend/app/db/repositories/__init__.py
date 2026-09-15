"""Repositories package."""

from app.db.repositories.base import TenantIsolatedRepository, GlobalRepository

__all__ = ["TenantIsolatedRepository", "GlobalRepository"]

"""Integration base package."""

from app.integrations.base.adapter import (
    IntegrationAdapter,
    AdapterResult,
    ConnectionInfo,
    HealthStatus,
)
from app.integrations.base.http_client import AuditedHTTPClient
from app.integrations.base.exceptions import (
    IntegrationError,
    ScopeMissingError,
    ConnectionExpiredError,
    ConnectionRevokedError,
    CollectionError,
    SanitizationError,
)

__all__ = [
    "IntegrationAdapter",
    "AdapterResult",
    "ConnectionInfo",
    "HealthStatus",
    "AuditedHTTPClient",
    "IntegrationError",
    "ScopeMissingError",
    "ConnectionExpiredError",
    "ConnectionRevokedError",
    "CollectionError",
    "SanitizationError",
]

"""Integration base exceptions."""

from __future__ import annotations

from typing import Any


class IntegrationError(Exception):
    """Base exception for integration errors."""

    def __init__(self, message: str, integration: str = "", details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.integration = integration
        self.details = details or {}


class ScopeMissingError(IntegrationError):
    """Required scope is not granted."""

    def __init__(self, missing_scopes: list[str], **kwargs: Any) -> None:
        super().__init__(
            f"Missing required scopes: {', '.join(missing_scopes)}",
            **kwargs,
        )
        self.missing_scopes = missing_scopes


class ConnectionExpiredError(IntegrationError):
    """Connection has expired."""


class ConnectionRevokedError(IntegrationError):
    """Connection has been revoked."""


class CollectionError(IntegrationError):
    """Error during evidence collection."""


class SanitizationError(IntegrationError):
    """Error during data sanitization."""

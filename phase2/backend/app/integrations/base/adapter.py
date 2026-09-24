"""Integration adapter base interface.

PHASE2_SPEC Section 3:
  Build adapters behind a common interface so that a missing integration
  degrades gracefully to 'Cannot determine' rather than failing the assessment.
"""

from __future__ import annotations

import abc
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.domain.evidence import EvidenceItem


class ConnectionInfo(BaseModel):
    """Information about an active integration connection."""
    provider: str
    account_id: str | None = None
    scopes: list[str] = Field(default_factory=list)
    connected_at: datetime | None = None
    expires_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class HealthStatus(BaseModel):
    """Health check result for an integration."""
    healthy: bool
    provider: str
    message: str
    checked_at: datetime
    details: dict[str, Any] = Field(default_factory=dict)


class AdapterResult(BaseModel):
    """Result of an adapter's collect operation."""
    evidence: list[EvidenceItem] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    partial: bool = False  # True if some data couldn't be collected


class IntegrationAdapter(abc.ABC):
    """Abstract base class for all integration adapters.

    Each adapter MUST:
    1. Define its id, provider, required_scopes, and obligations_answered
    2. Only request read-only scopes
    3. Map every scope to specific obligations
    4. Never store personal data
    5. Generate audit logs for every API call
    6. Degrade gracefully when access is missing
    """

    @property
    @abc.abstractmethod
    def id(self) -> str:
        """Unique adapter identifier."""

    @property
    @abc.abstractmethod
    def provider(self) -> str:
        """Provider name (e.g., 'google_tag_manager')."""

    @property
    @abc.abstractmethod
    def required_scopes(self) -> list[str]:
        """OAuth scopes required — must be read-only."""

    @property
    @abc.abstractmethod
    def obligations_answered(self) -> list[str]:
        """Obligation codes this adapter can answer."""

    @property
    def scope_obligation_map(self) -> dict[str, list[str]]:
        """Map each scope to the obligations that require it.

        Per PHASE2_SPEC Section 1 Rule 3:
        Document, per scope, which obligation requires it.
        """
        return {}

    @property
    def forbidden_endpoints(self) -> list[str]:
        """Endpoints that this adapter must NEVER call.

        These are tested with mock/spy assertions.
        """
        return []

    @property
    def safe_fields(self) -> frozenset[str]:
        """Fields that are safe to persist for this adapter."""
        return frozenset()

    @property
    def forbidden_fields(self) -> frozenset[str]:
        """Fields that must NEVER be persisted for this adapter."""
        return frozenset()

    @abc.abstractmethod
    async def connect(self, credentials: dict[str, Any], audit_callback: Any) -> ConnectionInfo:
        """Establish connection with the external service.

        Must verify scopes and fail if required read-only scopes are missing.
        """

    @abc.abstractmethod
    async def health_check(
        self,
        connection: ConnectionInfo,
        audit_callback: Any,
        *,
        client_id: str | None = None,
        assessment_id: str | None = None,
    ) -> HealthStatus:
        """Check the health of an established connection."""

    @abc.abstractmethod
    async def collect(
        self,
        connection: ConnectionInfo,
        audit_callback: Any,
        *,
        assessment_id: str | None = None,
        client_id: str | None = None,
        **kwargs: Any,
    ) -> AdapterResult:
        """Collect evidence from the external service.

        Must:
        1. Only call read-only endpoints
        2. Sanitize all responses before returning
        3. Generate audit logs for every API call
        4. Handle failures gracefully (Cannot determine)
        5. Never store PII in evidence
        """

    def validate_scopes(self, granted_scopes: list[str]) -> tuple[bool, list[str]]:
        """Validate that all required scopes are granted.

        Returns (all_scopes_granted, missing_scopes).
        """
        missing = [s for s in self.required_scopes if s not in granted_scopes]
        return len(missing) == 0, missing

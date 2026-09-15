"""Audit logging service.

Append-only audit log for every external API call.
Per PHASE2_SPEC Section 1 Rule 4: Every API call must produce an audit entry.
"""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger, request_id_var
from app.db.models.audit_log import AuditLog

logger = get_logger(__name__)


class AuditService:
    """Service for creating append-only audit log entries.

    Every external API call MUST create an audit entry containing:
    - client_id
    - assessment_id
    - actor/service identity
    - integration
    - endpoint / API operation
    - timestamp
    - obligations served
    - outcome
    - failure information where relevant

    This service is used by integration adapters and the HTTP client.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def log_api_call(
        self,
        *,
        client_id: uuid.UUID | str | None = None,
        assessment_id: uuid.UUID | str | None = None,
        actor: str = "system",
        integration: str,
        operation: str,
        endpoint: str,
        status: str,
        status_code: int | None = None,
        duration_ms: int | None = None,
        obligations_served: list[str] | None = None,
        request_metadata: dict[str, Any] | None = None,
        error_detail: str | None = None,
    ) -> AuditLog:
        """Create an append-only audit log entry.

        This method NEVER raises — audit failures are logged but
        do not break the calling operation.
        """
        try:
            # Sanitize request_metadata to prevent PII/secrets
            safe_metadata = _sanitize_audit_metadata(request_metadata)

            entry = AuditLog(
                client_id=uuid.UUID(str(client_id)) if client_id else None,
                assessment_id=uuid.UUID(str(assessment_id)) if assessment_id else None,
                actor=actor,
                integration=integration,
                operation=operation,
                endpoint=_sanitize_endpoint(endpoint),
                status=status,
                status_code=status_code,
                duration_ms=duration_ms,
                obligations_served=obligations_served,
                request_metadata=safe_metadata,
                error_detail=_truncate(error_detail, 2000) if error_detail else None,
                request_id=request_id_var.get(""),
                timestamp=datetime.now(timezone.utc),
            )

            self._session.add(entry)
            # Flush immediately so audit is persisted even if parent transaction fails
            await self._session.flush()

            logger.debug(
                "audit_entry_created",
                integration=integration,
                operation=operation,
                status=status,
            )

            return entry

        except Exception as exc:
            # Audit logging must NEVER break the calling operation
            logger.error(
                "audit_log_failure",
                integration=integration,
                operation=operation,
                error=str(exc),
            )
            # Return a placeholder entry
            return AuditLog(
                actor=actor,
                integration=integration,
                operation=operation,
                endpoint=endpoint,
                status="audit_failure",
                timestamp=datetime.now(timezone.utc),
            )


class AuditTimer:
    """Context manager to time an operation for audit logging."""

    def __init__(self) -> None:
        self._start: float = 0
        self.duration_ms: int = 0

    def __enter__(self) -> "AuditTimer":
        self._start = time.monotonic()
        return self

    def __exit__(self, *args: Any) -> None:
        self.duration_ms = int((time.monotonic() - self._start) * 1000)


def _sanitize_endpoint(endpoint: str) -> str:
    """Remove any sensitive query parameters from endpoint URLs."""
    # Remove common auth-related query params
    import urllib.parse
    parsed = urllib.parse.urlparse(endpoint)
    if parsed.query:
        params = urllib.parse.parse_qs(parsed.query)
        safe_params = {
            k: v for k, v in params.items()
            if k.lower() not in {
                "access_token", "token", "key", "api_key",
                "secret", "password", "credential",
            }
        }
        clean_query = urllib.parse.urlencode(safe_params, doseq=True)
        return urllib.parse.urlunparse(parsed._replace(query=clean_query))
    return endpoint


def _sanitize_audit_metadata(metadata: dict[str, Any] | None) -> dict[str, Any] | None:
    """Remove any sensitive fields from audit metadata."""
    if not metadata:
        return None

    from app.security.pii_sanitizer import sanitize_dict
    return sanitize_dict(metadata)


def _truncate(text: str, max_length: int) -> str:
    """Truncate text to max_length."""
    if len(text) <= max_length:
        return text
    return text[:max_length - 3] + "..."

"""AuditLog model - append-only audit trail.

PHASE2_SPEC Section 1, Rule 4:
  'Audit log everything. Every API call: who, what, when, which client,
   which obligation it served. Append-only. Retained per policy.'

This table is APPEND-ONLY. No UPDATE or DELETE operations are permitted.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Index, String, Text, func
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column, validates

from app.db.base import Base, UUIDPrimaryKeyMixin
from app.security.pii_sanitizer import sanitize_dict, sanitize_string


class AuditLog(Base, UUIDPrimaryKeyMixin):
    """Append-only audit log for every external API call.

    Contains: who, what, when, which client, which obligation it served.
    NEVER contains: OAuth tokens, Authorization headers, PII, secrets.
    """

    __tablename__ = "audit_logs"

    # Who
    client_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    assessment_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    actor: Mapped[str] = mapped_column(String(255), nullable=False)

    # What
    integration: Mapped[str] = mapped_column(String(100), nullable=False)
    operation: Mapped[str] = mapped_column(String(255), nullable=False)
    endpoint: Mapped[str] = mapped_column(String(512), nullable=False)

    # When
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    # Result
    status: Mapped[str] = mapped_column(String(50), nullable=False)
    status_code: Mapped[int | None] = mapped_column()
    duration_ms: Mapped[int | None] = mapped_column()

    # Context (no PII, no secrets)
    obligations_served: Mapped[list | None] = mapped_column(JSON)
    request_metadata: Mapped[dict | None] = mapped_column(JSON)
    error_detail: Mapped[str | None] = mapped_column(Text)

    # Correlation
    request_id: Mapped[str | None] = mapped_column(String(100))

    @validates("error_detail", "endpoint", "operation")
    def sanitize_strings(self, key, value):
        return sanitize_string(value) if value else value

    @validates("request_metadata")
    def sanitize_dicts(self, key, value):
        return sanitize_dict(value) if value else value

    __table_args__ = (
        Index("ix_audit_logs_client_id", "client_id"),
        Index("ix_audit_logs_assessment_id", "assessment_id"),
        Index("ix_audit_logs_integration", "integration"),
        Index("ix_audit_logs_timestamp", "timestamp"),
        Index("ix_audit_logs_actor", "actor"),
        Index("ix_audit_logs_client_timestamp", "client_id", "timestamp"),
    )

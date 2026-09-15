"""Evidence model - stores sanitized assessment evidence.

PHASE2_SPEC Section 6 & Evidence Contract:
  - Every finding carries a confidence level and names its source
  - LOW confidence never produces 'Not observed', only 'Cannot determine'
  - Verdict: Observed | Not observed | Cannot determine
  - Confidence: high | medium | low
  - Evidence store receives only safe metadata, NEVER PII
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Enum, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Verdict(str, enum.Enum):
    OBSERVED = "Observed"
    NOT_OBSERVED = "Not observed"
    CANNOT_DETERMINE = "Cannot determine"


class Confidence(str, enum.Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class Evidence(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Assessment evidence collected from integration adapters.

    CRITICAL: This table MUST only contain sanitized metadata.
    No PII, no raw API responses, no personal data.
    The sanitizer layer enforces this before persistence.
    """

    __tablename__ = "evidence"

    client_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("clients.id", ondelete="CASCADE"),
        nullable=False,
    )
    assessment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("assessments.id", ondelete="CASCADE"),
        nullable=False,
    )
    obligation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("obligations.id"),
        nullable=False,
    )
    source: Mapped[str] = mapped_column(String(200), nullable=False)
    verdict: Mapped[Verdict] = mapped_column(
        Enum(Verdict, name="verdict_type"),
        nullable=False,
    )
    confidence: Mapped[Confidence] = mapped_column(
        Enum(Confidence, name="confidence_type"),
        nullable=False,
    )
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    detail: Mapped[str] = mapped_column(Text, nullable=False)

    # Sanitized metadata only — defined per adapter
    sanitized_metadata: Mapped[dict | None] = mapped_column(JSON)

    # Relationships
    assessment = relationship("Assessment", back_populates="evidence_items")

    __table_args__ = (
        # HARD RULE: LOW confidence can NEVER result in "Not observed"
        CheckConstraint(
            "NOT (confidence = 'LOW' AND verdict = 'NOT_OBSERVED')",
            name="ck_evidence_low_confidence_not_observed",
        ),
        Index("ix_evidence_client_id", "client_id"),
        Index("ix_evidence_assessment_id", "assessment_id"),
        Index("ix_evidence_obligation_id", "obligation_id"),
        Index("ix_evidence_source", "source"),
        Index("ix_evidence_verdict", "verdict"),
        Index("ix_evidence_assessment_obligation", "assessment_id", "obligation_id"),
    )

"""Finding model - assessment findings derived from evidence and reconciliation."""

from __future__ import annotations

import enum
import uuid

from sqlalchemy import Boolean, CheckConstraint, Enum, ForeignKey, Index, Text
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.db.models.evidence import Confidence, Verdict


class FindingStatus(str, enum.Enum):
    OPEN = "open"
    UNDER_REVIEW = "under_review"
    ACKNOWLEDGED = "acknowledged"
    OVERRIDDEN = "overridden"
    RESOLVED = "resolved"


class FindingSeverity(str, enum.Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFORMATIONAL = "informational"


class Finding(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A finding derived from evidence, potentially across multiple sources.

    Findings are what the client sees. They include:
    - The obligation being assessed
    - The verdict and confidence
    - Supporting evidence references
    - Whether human review is required
    """

    __tablename__ = "findings"

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
    verdict: Mapped[Verdict] = mapped_column(
        Enum(Verdict, name="verdict_type", create_type=False),
        nullable=False,
    )
    confidence: Mapped[Confidence] = mapped_column(
        Enum(Confidence, name="confidence_type", create_type=False),
        nullable=False,
    )
    severity: Mapped[FindingSeverity] = mapped_column(
        Enum(FindingSeverity, name="finding_severity"),
        nullable=False,
    )
    detail: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[FindingStatus] = mapped_column(
        Enum(FindingStatus, name="finding_status"),
        default=FindingStatus.OPEN,
        nullable=False,
    )
    human_review_required: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )

    # Source evidence references
    evidence_ids: Mapped[list | None] = mapped_column(JSON)
    sources: Mapped[list | None] = mapped_column(JSON)

    # Relationships
    assessment = relationship("Assessment", back_populates="findings")
    overrides = relationship("HumanOverride", back_populates="finding", cascade="all, delete-orphan")

    __table_args__ = (
        # HARD RULE: LOW confidence can NEVER result in "Not observed"
        CheckConstraint(
            "NOT (confidence = 'LOW' AND verdict = 'NOT_OBSERVED')",
            name="ck_findings_low_confidence_not_observed",
        ),
        Index("ix_findings_client_id", "client_id"),
        Index("ix_findings_assessment_id", "assessment_id"),
        Index("ix_findings_obligation_id", "obligation_id"),
        Index("ix_findings_status", "status"),
        Index("ix_findings_severity", "severity"),
        Index("ix_findings_human_review", "human_review_required"),
        Index("ix_findings_client_assessment", "client_id", "assessment_id"),
    )

"""ReconciliationResult model - cross-source contradiction detection.

PHASE2_SPEC Section 5: 'Any single integration is a config dump. The value
is in the contradictions between them, and this is what nobody else does.'
"""

from __future__ import annotations

import enum
import uuid

from sqlalchemy import Boolean, Enum, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.db.models.evidence import Confidence


class ReconciliationOutcome(str, enum.Enum):
    AGREEMENT = "agreement"
    CONTRADICTION = "contradiction"
    PARTIAL_MATCH = "partial_match"
    INSUFFICIENT_DATA = "insufficient_data"


class ReconciliationResult(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Result of cross-source reconciliation between two evidence sources.

    Each contradiction becomes a finding with both sources cited.
    A finding backed by two independent agreeing sources is the strongest
    artifact the platform can produce.
    """

    __tablename__ = "reconciliation_results"

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
    rule_id: Mapped[str] = mapped_column(String(100), nullable=False)
    source_a: Mapped[str] = mapped_column(String(200), nullable=False)
    source_b: Mapped[str] = mapped_column(String(200), nullable=False)
    outcome: Mapped[ReconciliationOutcome] = mapped_column(
        Enum(ReconciliationOutcome, name="reconciliation_outcome"),
        nullable=False,
    )
    confidence: Mapped[Confidence] = mapped_column(
        Enum(Confidence, name="confidence_type", create_type=False),
        nullable=False,
    )
    detail: Mapped[str] = mapped_column(Text, nullable=False)
    human_review_required: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )

    # Evidence references from each source
    evidence_a_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    evidence_b_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))

    # Additional safe metadata about the reconciliation
    reconciliation_metadata: Mapped[dict | None] = mapped_column(JSON)

    __table_args__ = (
        Index("ix_reconciliation_assessment_id", "assessment_id"),
        Index("ix_reconciliation_obligation_id", "obligation_id"),
        Index("ix_reconciliation_rule_id", "rule_id"),
        Index("ix_reconciliation_outcome", "outcome"),
        Index("ix_reconciliation_human_review", "human_review_required"),
    )

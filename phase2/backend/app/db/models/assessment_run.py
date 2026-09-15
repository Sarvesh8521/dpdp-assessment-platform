"""AssessmentRun model - tracks per-integration execution within an assessment."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class RunStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"  # Integration not connected
    TIMEOUT = "timeout"
    RATE_LIMITED = "rate_limited"


class AssessmentRun(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Tracks the execution of a single integration within an assessment.

    An assessment with GTM + GA4 + Search Console creates 3 AssessmentRuns.
    If one fails, the others continue (graceful degradation).
    """

    __tablename__ = "assessment_runs"

    assessment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("assessments.id", ondelete="CASCADE"),
        nullable=False,
    )
    integration: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[RunStatus] = mapped_column(
        Enum(RunStatus, name="run_status"),
        default=RunStatus.PENDING,
        nullable=False,
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failure_reason: Mapped[str | None] = mapped_column(Text)
    failure_code: Mapped[str | None] = mapped_column(String(50))
    evidence_count: Mapped[int] = mapped_column(default=0, nullable=False)
    retry_count: Mapped[int] = mapped_column(default=0, nullable=False)
    metadata_result: Mapped[dict | None] = mapped_column(JSON)

    # Relationships
    assessment = relationship("Assessment", back_populates="runs")

    __table_args__ = (
        Index("ix_assessment_runs_assessment_id", "assessment_id"),
        Index("ix_assessment_runs_integration", "integration"),
        Index("ix_assessment_runs_status", "status"),
    )

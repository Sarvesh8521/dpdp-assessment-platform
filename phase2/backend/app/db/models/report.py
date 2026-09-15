"""Report model - generated assessment reports."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class ReportStatus(str, enum.Enum):
    GENERATING = "generating"
    COMPLETED = "completed"
    FAILED = "failed"


class Report(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Generated assessment report.

    Report artifacts are stored in object storage (GCS in production,
    local filesystem in development). This table holds the reference.
    """

    __tablename__ = "reports"

    assessment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("assessments.id", ondelete="CASCADE"),
        nullable=False,
    )
    version: Mapped[int] = mapped_column(default=1, nullable=False)
    status: Mapped[ReportStatus] = mapped_column(
        Enum(ReportStatus, name="report_status"),
        default=ReportStatus.GENERATING,
        nullable=False,
    )
    artifact_ref: Mapped[str | None] = mapped_column(String(512))
    format: Mapped[str] = mapped_column(String(50), default="json", nullable=False)

    # Report metadata
    report_metadata: Mapped[dict | None] = mapped_column(JSON)
    generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Relationships
    assessment = relationship("Assessment", back_populates="reports")

    __table_args__ = (
        Index("ix_reports_assessment_id", "assessment_id"),
        Index("ix_reports_status", "status"),
    )

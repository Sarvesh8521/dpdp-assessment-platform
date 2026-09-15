"""Assessment model - represents a DPDP compliance assessment."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class AssessmentStatus(str, enum.Enum):
    PENDING = "pending"
    QUEUED = "queued"
    RUNNING = "running"
    PARTIAL = "partial"  # Some integrations completed, some failed
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class Assessment(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A DPDP compliance assessment run.

    Each assessment runs selected integrations, collects evidence,
    performs reconciliation, creates findings, and generates a report.
    """

    __tablename__ = "assessments"

    client_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("clients.id", ondelete="CASCADE"),
        nullable=False,
    )
    status: Mapped[AssessmentStatus] = mapped_column(
        Enum(AssessmentStatus, name="assessment_status"),
        default=AssessmentStatus.PENDING,
        nullable=False,
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=False,
    )
    configuration: Mapped[dict | None] = mapped_column(JSON)
    version: Mapped[str] = mapped_column(String(50), default="2.0.0", nullable=False)
    selected_integrations: Mapped[list | None] = mapped_column(JSON)
    scope_metadata: Mapped[dict | None] = mapped_column(JSON)

    # Relationships
    client = relationship("Client", back_populates="assessments")
    runs = relationship("AssessmentRun", back_populates="assessment", cascade="all, delete-orphan")
    evidence_items = relationship("Evidence", back_populates="assessment", cascade="all, delete-orphan")
    findings = relationship("Finding", back_populates="assessment", cascade="all, delete-orphan")
    reports = relationship("Report", back_populates="assessment", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_assessments_client_id", "client_id"),
        Index("ix_assessments_status", "status"),
        Index("ix_assessments_created_by", "created_by"),
        Index("ix_assessments_client_status", "client_id", "status"),
    )

"""HumanOverride model - records human review overrides.

PHASE2_SPEC Section 6:
  - Anything a human overrode is recorded, with the reason
  - Track the override rate: if humans correct >10% of findings,
    the automation is not ready for that finding type
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, UUIDPrimaryKeyMixin


class HumanOverride(Base, UUIDPrimaryKeyMixin):
    """Record of a human override of an automated finding.

    Every override records the previous verdict, new verdict, reason,
    who made it, and when. This is immutable — overrides are append-only.
    """

    __tablename__ = "human_overrides"

    finding_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("findings.id", ondelete="CASCADE"),
        nullable=False,
    )
    previous_verdict: Mapped[str] = mapped_column(Text, nullable=False)
    new_verdict: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=False,
    )
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    # Relationships
    finding = relationship("Finding", back_populates="overrides")

    __table_args__ = (
        Index("ix_human_overrides_finding_id", "finding_id"),
        Index("ix_human_overrides_user_id", "user_id"),
        Index("ix_human_overrides_timestamp", "timestamp"),
    )

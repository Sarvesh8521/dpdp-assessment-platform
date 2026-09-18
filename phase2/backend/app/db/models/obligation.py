"""Obligation model - the central spine of the platform.

PHASE2_SPEC Section 3: 'The obligation register is the spine, not the integrations.'
Every integration exists to answer specific obligations. If an integration cannot be
mapped to an obligation, it does not belong in the product.
"""

from __future__ import annotations

import enum

from sqlalchemy import Boolean, Enum, Index, String, Text
from sqlalchemy.dialects.postgresql import JSON
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class ObligationCategory(str, enum.Enum):
    CONSENT = "consent"
    DATA_COLLECTION = "data_collection"
    DATA_RETENTION = "data_retention"
    DATA_SHARING = "data_sharing"
    DATA_PROCESSING = "data_processing"
    TRANSPARENCY = "transparency"
    RIGHTS = "rights"
    SECURITY = "security"
    GOVERNANCE = "governance"
    CROSS_BORDER = "cross_border"


class ObligationSeverity(str, enum.Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class Obligation(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """DPDP obligation from the obligation register.

    The obligation register comes from the counsel-reviewed working file.
    Adapters map to existing obligation IDs — they never invent new ones.
    """

    __tablename__ = "obligations"

    code: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    category: Mapped[ObligationCategory] = mapped_column(
        Enum(ObligationCategory, name="obligation_category"),
        nullable=False,
    )
    severity: Mapped[ObligationSeverity] = mapped_column(
        Enum(ObligationSeverity, name="obligation_severity"),
        default=ObligationSeverity.MEDIUM,
        nullable=False,
    )
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    dpdp_section: Mapped[str | None] = mapped_column(String(50))
    version: Mapped[str] = mapped_column(String(50), default="draft", nullable=False)
    source_reference: Mapped[str | None] = mapped_column(String(500))
    metadata_info: Mapped[dict | None] = mapped_column(JSON)

    __table_args__ = (
        Index("ix_obligations_code", "code", unique=True),
        Index("ix_obligations_category", "category"),
        Index("ix_obligations_active", "active"),
        Index("ix_obligations_version", "version"),
    )

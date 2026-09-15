"""Client model - represents a tenant/client organization."""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum, Index, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class ClientStatus(str, enum.Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    SUSPENDED = "suspended"
    ARCHIVED = "archived"


class Client(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Client/tenant organization.

    Every data record in the system belongs to exactly one Client.
    Per-client isolation is enforced at the repository layer.
    """

    __tablename__ = "clients"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[ClientStatus] = mapped_column(
        Enum(ClientStatus, name="client_status"),
        default=ClientStatus.ACTIVE,
        nullable=False,
    )
    industry: Mapped[str | None] = mapped_column(String(255))
    website: Mapped[str | None] = mapped_column(String(512))
    engagement_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    engagement_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Relationships
    users = relationship("ClientUser", back_populates="client", cascade="all, delete-orphan")
    assessments = relationship("Assessment", back_populates="client", cascade="all, delete-orphan")
    connections = relationship("Connection", back_populates="client", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_clients_name", "name"),
        Index("ix_clients_status", "status"),
    )

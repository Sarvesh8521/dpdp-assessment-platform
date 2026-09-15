"""ClientUser association model - maps users to clients with roles."""

from __future__ import annotations

import enum
import uuid

from sqlalchemy import Enum, ForeignKey, Index, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin


class UserRole(str, enum.Enum):
    PLATFORM_ADMIN = "platform_admin"
    CLIENT_ADMIN = "client_admin"
    ASSESSOR = "assessor"
    READONLY = "readonly"


class ClientUser(Base, TimestampMixin):
    """Association between users and clients with role-based access.

    RBAC enforcement:
    - platform_admin: Full platform access (all clients)
    - client_admin: Full access within their client
    - assessor: Can run assessments, review findings
    - readonly: View-only access to their client's data
    """

    __tablename__ = "client_users"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
        nullable=False,
    )
    client_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("clients.id", ondelete="CASCADE"),
        primary_key=True,
        nullable=False,
    )
    role: Mapped[UserRole] = mapped_column(
        Enum(UserRole, name="user_role"),
        nullable=False,
    )

    # Relationships
    user = relationship("User", back_populates="client_associations")
    client = relationship("Client", back_populates="users")

    __table_args__ = (
        UniqueConstraint("user_id", "client_id", name="uq_client_user"),
        Index("ix_client_users_client_id", "client_id"),
        Index("ix_client_users_user_id", "user_id"),
    )

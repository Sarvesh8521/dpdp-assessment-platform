"""User model - platform users with identity."""

from __future__ import annotations

import enum

from sqlalchemy import Enum, Index, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class UserStatus(str, enum.Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    SUSPENDED = "suspended"


class User(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Platform user.

    Identity is stored as a hashed/opaque reference, not as PII.
    The 'identity' field is the authentication subject (e.g., from JWT sub claim).
    """

    __tablename__ = "users"

    identity: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    hashed_password: Mapped[str | None] = mapped_column(String(512), nullable=True)
    display_label: Mapped[str | None] = mapped_column(String(255))
    # Google Auth
    google_sub: Mapped[str | None] = mapped_column(String(255), nullable=True)
    
    status: Mapped[UserStatus] = mapped_column(
        Enum(UserStatus, name="user_status"),
        default=UserStatus.ACTIVE,
        nullable=False,
    )

    # Relationships
    client_associations = relationship("ClientUser", back_populates="user", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_users_identity", "identity", unique=True),
        Index("ix_users_google_sub", "google_sub", unique=True),
        Index("ix_users_status", "status"),
    )

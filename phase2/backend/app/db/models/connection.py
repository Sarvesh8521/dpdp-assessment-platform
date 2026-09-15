"""Connection model - tracks authenticated integration connections.

PHASE2_SPEC Section 1, Rules 5 & 7:
  - Credentials never touch the database in plaintext
  - Tokens encrypted at rest, scoped per client, revocable
  - Time-boxed access — tokens expire at engagement end
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class ConnectionStatus(str, enum.Enum):
    PENDING = "pending"
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    EXPIRED = "expired"
    REVOKED = "revoked"
    ERROR = "error"


class Connection(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """An authenticated connection to an external service.

    The secret_ref field points to a reference in the secret manager,
    NEVER the actual token value. Tokens are encrypted at rest in the
    secret store and scoped per client.
    """

    __tablename__ = "connections"

    client_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("clients.id", ondelete="CASCADE"),
        nullable=False,
    )
    provider: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[ConnectionStatus] = mapped_column(
        Enum(ConnectionStatus, name="connection_status"),
        default=ConnectionStatus.PENDING,
        nullable=False,
    )
    scopes: Mapped[list | None] = mapped_column(JSON)
    connected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Reference to secret manager — NEVER the actual token
    secret_ref: Mapped[str | None] = mapped_column(String(255))

    # Provider-specific safe metadata (e.g., account ID, property ID)
    provider_metadata: Mapped[dict | None] = mapped_column(JSON)

    # Actual scopes granted by the provider (may differ from requested)
    granted_scopes: Mapped[str | None] = mapped_column(String(1024))
    provider_account_id: Mapped[str | None] = mapped_column(String(255))

    # Selected resource (e.g., GA4 property, GTM container, GitHub repo)
    resource_id: Mapped[str | None] = mapped_column(String(255))
    resource_name: Mapped[str | None] = mapped_column(String(255))

    # Relationships
    client = relationship("Client", back_populates="connections")

    __table_args__ = (
        Index("ix_connections_client_id", "client_id"),
        Index("ix_connections_provider", "provider"),
        Index("ix_connections_client_provider", "client_id", "provider"),
        Index("ix_connections_status", "status"),
    )

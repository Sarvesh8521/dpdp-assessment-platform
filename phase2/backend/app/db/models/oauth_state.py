"""OAuth State model — server-side CSRF and replay prevention.

Stores the state parameter used in OAuth authorization flows.
The callback endpoint validates:
  - state exists
  - state has not expired
  - state has not already been used
  - provider matches
  - user/client ownership matches

After successful callback: used_at is set to prevent replay.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class OAuthState(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Server-side OAuth state for authorization flow security."""

    __tablename__ = "oauth_states"

    # The state parameter sent to the OAuth provider
    state: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)

    # Who initiated this flow
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    client_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("clients.id", ondelete="CASCADE"),
        nullable=False,
    )

    # Which integration provider this flow is for
    provider: Mapped[str] = mapped_column(String(50), nullable=False)

    # What scopes were requested
    requested_scopes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # The redirect URI used (must match at callback time)
    redirect_uri: Mapped[str | None] = mapped_column(String(512), nullable=True)

    # Expiry — states should be short-lived (10 minutes)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # Set after successful use — prevents replay
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_oauth_states_state", "state", unique=True),
        Index("ix_oauth_states_user_client", "user_id", "client_id"),
    )

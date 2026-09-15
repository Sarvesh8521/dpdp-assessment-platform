"""OTP Token model — secure verification codes for login and password reset.

Stores hashed OTPs with purpose, expiration, attempt tracking, and single-use enforcement.
"""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class OtpPurpose(str, enum.Enum):
    LOGIN = "login"
    PASSWORD_RESET = "password_reset"


class OtpToken(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Secure OTP token for authentication and password recovery."""

    __tablename__ = "otp_tokens"

    email: Mapped[str] = mapped_column(String(255), nullable=False)
    hashed_otp: Mapped[str] = mapped_column(String(255), nullable=False)
    purpose: Mapped[str] = mapped_column(String(50), nullable=False)  # 'login' or 'password_reset'
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_otp_tokens_email_purpose", "email", "purpose"),
        Index("ix_otp_tokens_expires_at", "expires_at"),
    )
"""Authentication API endpoints.

Implements:
- Email and password login & registration
- Email OTP login with auto-provisioning
- Forgot password with OTP email verification and reset
- JWT token refresh and session validation
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import (
    AuthContext,
    create_access_token,
    create_refresh_token,
    decode_token,
    get_current_user,
    hash_password,
    verify_password,
)
from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.models.client import Client
from app.db.models.client_user import ClientUser, UserRole
from app.db.models.otp_token import OtpPurpose, OtpToken
from app.db.models.user import User, UserStatus
from app.db.session import get_db_session
from app.schemas.api import (
    ForgotPasswordRequest,
    LoginRequest,
    RefreshTokenRequest,
    RegisterRequest,
    ResetPasswordOtpRequest,
    SendOtpRequest,
    TokenResponse,
    VerifyOtpLoginRequest,
)
from app.services.email_service import (
    send_login_otp_email,
    send_password_reset_otp_email,
)

logger = get_logger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])

OTP_EXPIRY_MINUTES = 10
OTP_COOLDOWN_SECONDS = 60
OTP_MAX_ATTEMPTS = 5


def _hash_otp(otp: str, secret_key: str) -> str:
    """Generate deterministic salted SHA-256 hash for OTP storage."""
    return hashlib.sha256(f"{otp.strip()}:{secret_key}".encode("utf-8")).hexdigest()


async def _get_client_roles(session: AsyncSession, user_id: uuid.UUID) -> dict[str, str]:
    """Fetch client roles for a user."""
    role_result = await session.execute(
        select(ClientUser).where(ClientUser.user_id == user_id)
    )
    client_users = role_result.scalars().all()
    return {
        str(cu.client_id): str(cu.role.value if hasattr(cu.role, "value") else cu.role)
        for cu in client_users
    }


# ── 1. Email & Password Login ──

@router.post("/login", response_model=TokenResponse)
async def login(
    data: LoginRequest,
    session: AsyncSession = Depends(get_db_session),
) -> Any:
    """Authenticate user with email and password."""
    norm_email = data.email.strip().lower()

    result = await session.execute(
        select(User).where(
            func.lower(User.identity) == norm_email,
            User.status == UserStatus.ACTIVE,
        )
    )
    user = result.scalars().first()

    if not user or not user.hashed_password or not verify_password(data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    client_roles = await _get_client_roles(session, user.id)

    access_token = create_access_token(
        user_id=str(user.id),
        identity=user.identity,
        client_roles=client_roles,
    )
    refresh_token = create_refresh_token(user_id=str(user.id))

    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
        "expires_in": 3600,
    }


# ── 2. Email & Password Registration ──

@router.post("/register", response_model=TokenResponse)
async def register(
    data: RegisterRequest,
    session: AsyncSession = Depends(get_db_session),
) -> Any:
    """Register a new user with email & password, provision organization, and return tokens."""
    norm_email = data.email.strip().lower()

    if len(data.password) < 6:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Password must be at least 6 characters long",
        )

    # Check if user already exists
    result = await session.execute(
        select(User).where(func.lower(User.identity) == norm_email)
    )
    if result.scalars().first():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered",
        )

    # Create new user
    new_user = User(
        identity=norm_email,
        hashed_password=hash_password(data.password),
        display_label=data.display_label,
        status=UserStatus.ACTIVE,
    )
    session.add(new_user)
    await session.flush()

    # Create new Client organization
    new_client = Client(name=f"{norm_email}'s Organization")
    session.add(new_client)
    await session.flush()

    # Add user to client as PLATFORM_ADMIN
    new_client_user = ClientUser(
        user_id=new_user.id,
        client_id=new_client.id,
        role=UserRole.PLATFORM_ADMIN,
    )
    session.add(new_client_user)
    await session.commit()

    client_roles = {str(new_client.id): UserRole.PLATFORM_ADMIN.value}

    access_token = create_access_token(
        user_id=str(new_user.id),
        identity=new_user.identity,
        client_roles=client_roles,
    )
    refresh_token = create_refresh_token(user_id=str(new_user.id))

    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
        "expires_in": 3600,
    }


# ── 3. Current User Context ──

@router.get("/me")
async def get_me(auth: AuthContext = Depends(get_current_user)) -> Any:
    """Get current user context."""
    return {
        "user_id": auth.user_id,
        "identity": auth.identity,
        "client_roles": auth.client_roles,
        "is_platform_admin": auth.is_platform_admin,
    }


# ── 4. Login via OTP: Send Code ──

@router.post("/otp/send-login")
async def send_login_otp(
    data: SendOtpRequest,
    session: AsyncSession = Depends(get_db_session),
) -> Any:
    """Send a 6-digit OTP code to email for passwordless sign-in."""
    norm_email = data.email.strip().lower()
    if not norm_email or "@" not in norm_email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A valid email address is required",
        )

    settings = get_settings()
    now = datetime.now(timezone.utc)

    # Check cooldown: prevent sending multiple OTPs within 60s
    recent_query = await session.execute(
        select(OtpToken)
        .where(
            OtpToken.email == norm_email,
            OtpToken.purpose == OtpPurpose.LOGIN.value,
            OtpToken.created_at > now - timedelta(seconds=OTP_COOLDOWN_SECONDS),
        )
        .order_by(OtpToken.created_at.desc())
    )
    if recent_query.scalars().first():
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Please wait {OTP_COOLDOWN_SECONDS} seconds before requesting a new code",
        )

    # Generate cryptographically secure 6-digit numeric OTP
    otp_code = f"{secrets.randbelow(900000) + 100000}"
    hashed_otp = _hash_otp(otp_code, settings.secret_key)
    expires_at = now + timedelta(minutes=OTP_EXPIRY_MINUTES)

    otp_record = OtpToken(
        email=norm_email,
        hashed_otp=hashed_otp,
        purpose=OtpPurpose.LOGIN.value,
        attempts=0,
        expires_at=expires_at,
    )
    session.add(otp_record)
    await session.commit()

    # Send email asynchronously via Resend
    email_sent = await send_login_otp_email(to=norm_email, otp=otp_code)
    logger.info("Login OTP generated for %s (email sent: %s)", norm_email, email_sent)

    return {
        "message": f"Verification code sent to {norm_email}",
        "expires_in": OTP_EXPIRY_MINUTES * 60,
    }


# ── 5. Login via OTP: Verify & Sign In ──

@router.post("/otp/verify-login", response_model=TokenResponse)
async def verify_login_otp(
    data: VerifyOtpLoginRequest,
    session: AsyncSession = Depends(get_db_session),
) -> Any:
    """Verify 6-digit OTP code and sign in (auto-provisions new user if not exists)."""
    norm_email = data.email.strip().lower()
    otp_input = data.otp.strip()
    settings = get_settings()
    now = datetime.now(timezone.utc)

    # Find the most recent active OTP for this email and purpose
    result = await session.execute(
        select(OtpToken)
        .where(
            OtpToken.email == norm_email,
            OtpToken.purpose == OtpPurpose.LOGIN.value,
            OtpToken.used_at.is_(None),
            OtpToken.expires_at > now,
        )
        .order_by(OtpToken.created_at.desc())
    )
    otp_token = result.scalars().first()

    if not otp_token:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired verification code",
        )

    if otp_token.attempts >= OTP_MAX_ATTEMPTS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Too many failed attempts. Please request a new verification code.",
        )

    expected_hash = _hash_otp(otp_input, settings.secret_key)
    if not hmac.compare_digest(otp_token.hashed_otp, expected_hash):
        otp_token.attempts += 1
        await session.commit()
        remaining = OTP_MAX_ATTEMPTS - otp_token.attempts
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Incorrect code. {remaining} attempt{'s' if remaining != 1 else ''} remaining.",
        )

    # Mark OTP as consumed
    otp_token.used_at = now

    # Find or auto-provision user
    user_result = await session.execute(
        select(User).where(func.lower(User.identity) == norm_email)
    )
    user = user_result.scalars().first()

    if not user:
        # Auto-provision user & organization
        user = User(
            identity=norm_email,
            status=UserStatus.ACTIVE,
        )
        session.add(user)
        await session.flush()

        new_client = Client(name=f"{norm_email}'s Organization")
        session.add(new_client)
        await session.flush()

        new_client_user = ClientUser(
            user_id=user.id,
            client_id=new_client.id,
            role=UserRole.PLATFORM_ADMIN,
        )
        session.add(new_client_user)
        await session.commit()
        await session.refresh(user)
    else:
        if user.status != UserStatus.ACTIVE:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Account is inactive or suspended",
            )
        await session.commit()

    client_roles = await _get_client_roles(session, user.id)

    access_token = create_access_token(
        user_id=str(user.id),
        identity=user.identity,
        client_roles=client_roles,
    )
    refresh_token = create_refresh_token(user_id=str(user.id))

    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
        "expires_in": 3600,
    }


# ── 6. Forgot Password: Send Reset OTP ──

@router.post("/forgot-password")
async def forgot_password(
    data: ForgotPasswordRequest,
    session: AsyncSession = Depends(get_db_session),
) -> Any:
    """Send a 6-digit password reset OTP to user email."""
    norm_email = data.email.strip().lower()
    if not norm_email or "@" not in norm_email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A valid email address is required",
        )

    settings = get_settings()
    now = datetime.now(timezone.utc)

    # Check if user exists
    user_result = await session.execute(
        select(User).where(func.lower(User.identity) == norm_email)
    )
    user = user_result.scalars().first()

    # If user doesn't exist, return neutral success message to prevent user enumeration
    if not user:
        return {
            "message": "If an account exists with that email, a password reset code has been sent.",
            "expires_in": OTP_EXPIRY_MINUTES * 60,
        }

    # Check cooldown
    recent_query = await session.execute(
        select(OtpToken)
        .where(
            OtpToken.email == norm_email,
            OtpToken.purpose == OtpPurpose.PASSWORD_RESET.value,
            OtpToken.created_at > now - timedelta(seconds=OTP_COOLDOWN_SECONDS),
        )
        .order_by(OtpToken.created_at.desc())
    )
    if recent_query.scalars().first():
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Please wait {OTP_COOLDOWN_SECONDS} seconds before requesting another reset code",
        )

    # Generate 6-digit OTP code
    otp_code = f"{secrets.randbelow(900000) + 100000}"
    hashed_otp = _hash_otp(otp_code, settings.secret_key)
    expires_at = now + timedelta(minutes=OTP_EXPIRY_MINUTES)

    otp_record = OtpToken(
        email=norm_email,
        hashed_otp=hashed_otp,
        purpose=OtpPurpose.PASSWORD_RESET.value,
        attempts=0,
        expires_at=expires_at,
    )
    session.add(otp_record)
    await session.commit()

    email_sent = await send_password_reset_otp_email(to=norm_email, otp=otp_code)
    logger.info("Password reset OTP generated for %s (email sent: %s)", norm_email, email_sent)

    return {
        "message": "A 6-digit password reset code has been sent to your email.",
        "expires_in": OTP_EXPIRY_MINUTES * 60,
    }


# ── 7. Reset Password: Verify OTP & Update Password ──

@router.post("/reset-password")
async def reset_password(
    data: ResetPasswordOtpRequest,
    session: AsyncSession = Depends(get_db_session),
) -> Any:
    """Verify 6-digit OTP and reset password."""
    norm_email = data.email.strip().lower()
    otp_input = data.otp.strip()
    new_password = data.new_password

    if len(new_password) < 6:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Password must be at least 6 characters long",
        )

    settings = get_settings()
    now = datetime.now(timezone.utc)

    # Find the most recent active reset OTP
    result = await session.execute(
        select(OtpToken)
        .where(
            OtpToken.email == norm_email,
            OtpToken.purpose == OtpPurpose.PASSWORD_RESET.value,
            OtpToken.used_at.is_(None),
            OtpToken.expires_at > now,
        )
        .order_by(OtpToken.created_at.desc())
    )
    otp_token = result.scalars().first()

    if not otp_token:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired reset code",
        )

    if otp_token.attempts >= OTP_MAX_ATTEMPTS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Too many failed attempts. Please request a new reset code.",
        )

    expected_hash = _hash_otp(otp_input, settings.secret_key)
    if not hmac.compare_digest(otp_token.hashed_otp, expected_hash):
        otp_token.attempts += 1
        await session.commit()
        remaining = OTP_MAX_ATTEMPTS - otp_token.attempts
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Incorrect reset code. {remaining} attempt{'s' if remaining != 1 else ''} remaining.",
        )

    # Mark OTP as used
    otp_token.used_at = now

    # Find user and update password
    user_result = await session.execute(
        select(User).where(func.lower(User.identity) == norm_email)
    )
    user = user_result.scalars().first()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    user.hashed_password = hash_password(new_password)
    await session.commit()

    logger.info("Password successfully reset for user %s", norm_email)

    return {
        "message": "Password has been successfully reset. You can now sign in with your new password.",
    }


# ── 8. Refresh Token ──

@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(
    data: RefreshTokenRequest,
    session: AsyncSession = Depends(get_db_session),
) -> Any:
    """Issue a new access token and refresh token from a valid refresh token."""
    payload = decode_token(data.refresh_token)

    if payload.get("type") != "refresh":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token type",
        )

    user_id_str = payload.get("sub")
    if not user_id_str:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token payload",
        )

    user_id = uuid.UUID(user_id_str)
    result = await session.execute(
        select(User).where(User.id == user_id, User.status == UserStatus.ACTIVE)
    )
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or inactive",
        )

    client_roles = await _get_client_roles(session, user.id)

    access_token = create_access_token(
        user_id=str(user.id),
        identity=user.identity,
        client_roles=client_roles,
    )
    new_refresh_token = create_refresh_token(user_id=str(user.id))

    return {
        "access_token": access_token,
        "refresh_token": new_refresh_token,
        "token_type": "bearer",
        "expires_in": 3600,
    }


# ── 9. Logout ──

@router.post("/logout")
async def logout(auth: AuthContext = Depends(get_current_user)) -> Any:
    """Log out current user context."""
    logger.info("User %s logged out", auth.identity)
    return {"message": "Successfully logged out"}

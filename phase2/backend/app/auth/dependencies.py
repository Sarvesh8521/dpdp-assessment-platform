"""Authentication and authorization module.

Implements:
- JWT-based authentication
- RBAC with 4 roles
- Tenant isolation enforcement
- Client context from auth token (never from frontend-supplied client_id)
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import Depends, HTTPException, Query, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import client_id_var, get_logger
from app.db.models.client_user import ClientUser, UserRole
from app.db.models.user import User, UserStatus
from app.db.session import get_db_session

logger = get_logger(__name__)

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
security_scheme = HTTPBearer(auto_error=False)


class AuthContext:
    """Authenticated user context with tenant isolation."""

    def __init__(
        self,
        user_id: uuid.UUID,
        identity: str,
        client_roles: dict[str, Any],
        is_platform_admin: bool = False,
    ) -> None:
        self.user_id = user_id
        self.identity = identity
        self.client_roles = client_roles  # {client_id_str: role}
        self.is_platform_admin = is_platform_admin

    def has_client_access(self, client_id: uuid.UUID | str) -> bool:
        """Check if user has access to a specific client."""
        if self.is_platform_admin:
            return True
        return str(client_id) in self.client_roles

    def get_client_role(self, client_id: uuid.UUID | str) -> UserRole | None:
        """Get the user's role for a specific client."""
        if self.is_platform_admin:
            return UserRole.PLATFORM_ADMIN
        return self.client_roles.get(str(client_id))

    def require_client_access(self, client_id: uuid.UUID | str) -> None:
        """Raise 403 if user doesn't have access to the client."""
        if not self.has_client_access(client_id):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied to this client's resources",
            )

    def require_role(
        self, client_id: uuid.UUID | str, minimum_role: UserRole
    ) -> None:
        """Require at least the specified role for the client."""
        self.require_client_access(client_id)
        role = self.get_client_role(client_id)
        if role is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient permissions",
            )

        role_hierarchy = {
            UserRole.PLATFORM_ADMIN: 4,
            UserRole.CLIENT_ADMIN: 3,
            UserRole.ASSESSOR: 2,
            UserRole.READONLY: 1,
        }

        if role_hierarchy.get(role, 0) < role_hierarchy.get(minimum_role, 0):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient permissions for this operation",
            )

    def get_accessible_client_ids(self) -> list[str]:
        """Get list of client IDs this user can access."""
        return list(self.client_roles.keys())


def hash_password(password: str) -> str:
    """Hash a password using bcrypt."""
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a password against its hash."""
    try:
        return pwd_context.verify(plain_password, hashed_password)
    except Exception:
        return False


def create_access_token(
    user_id: str,
    identity: str,
    client_roles: dict[str, str],
    *,
    expires_delta: timedelta | None = None,
) -> str:
    """Create a JWT access token."""
    settings = get_settings()
    if expires_delta is None:
        expires_delta = timedelta(minutes=settings.access_token_expire_minutes)

    expire = datetime.now(timezone.utc) + expires_delta
    payload = {
        "sub": str(user_id),
        "identity": identity,
        "client_roles": client_roles,
        "exp": expire,
        "iat": datetime.now(timezone.utc),
        "type": "access",
    }
    return jwt.encode(payload, settings.secret_key, algorithm=settings.jwt_algorithm)


def create_refresh_token(
    user_id: str,
    *,
    expires_delta: timedelta | None = None,
) -> str:
    """Create a JWT refresh token."""
    settings = get_settings()
    if expires_delta is None:
        expires_delta = timedelta(days=settings.refresh_token_expire_days)

    expire = datetime.now(timezone.utc) + expires_delta
    payload = {
        "sub": str(user_id),
        "exp": expire,
        "iat": datetime.now(timezone.utc),
        "type": "refresh",
    }
    return jwt.encode(payload, settings.secret_key, algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> dict[str, Any]:
    """Decode and validate a JWT token."""
    settings = get_settings()
    try:
        payload = jwt.decode(
            token,
            settings.secret_key,
            algorithms=[settings.jwt_algorithm],
        )
        return payload
    except JWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(security_scheme),
    token: str | None = Query(None),
    session: AsyncSession = Depends(get_db_session),
) -> AuthContext:
    """FastAPI dependency: extract and validate the current authenticated user.

    Client context is determined from the JWT token, NEVER from
    frontend-supplied client_id parameters.
    """
    raw_token = credentials.credentials if credentials else token
    if not raw_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    payload = decode_token(raw_token)

    if payload.get("type") != "access":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token type",
        )

    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token payload",
        )

    # Verify user still exists and is active
    result = await session.execute(
        select(User).where(
            User.id == uuid.UUID(user_id),
            User.status == UserStatus.ACTIVE,
        )
    )
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or inactive",
        )

    # Load fresh client roles from database (not just from token)
    cu_result = await session.execute(
        select(ClientUser).where(ClientUser.user_id == user.id)
    )
    client_users = cu_result.scalars().all()

    client_roles: dict[str, Any] = {}
    is_platform_admin = False
    for cu in client_users:
        client_roles[str(cu.client_id)] = cu.role
        if cu.role == UserRole.PLATFORM_ADMIN:
            is_platform_admin = True

    auth_context = AuthContext(
        user_id=user.id,
        identity=user.identity,
        client_roles=client_roles,
        is_platform_admin=is_platform_admin,
    )

    # Set client_id context var for logging
    if client_roles:
        first_client_id = next(iter(client_roles))
        client_id_var.set(first_client_id)

    return auth_context


def require_platform_admin(auth: AuthContext = Depends(get_current_user)) -> AuthContext:
    """Dependency that requires platform admin role."""
    if not auth.is_platform_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Platform administrator access required",
        )
    return auth

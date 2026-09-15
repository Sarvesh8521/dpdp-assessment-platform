"""Google authentication API endpoints.

Verifies Google OAuth access tokens and issues JWT tokens.
Uses the implicit (access_token) flow: the frontend gets an access_token
from Google via @react-oauth/google's useGoogleLogin, then sends it here.
We verify the token by calling Google's userinfo endpoint.
"""

from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.auth.dependencies import create_access_token, create_refresh_token
from app.db.session import get_db_session
from app.db.models.user import User, UserStatus
from app.db.models.client_user import ClientUser
from app.schemas.api import TokenResponse, GoogleTokenRequest

router = APIRouter(prefix="/auth/google", tags=["auth", "google"])


@router.post("/token", response_model=TokenResponse)
async def google_token_login(
    data: GoogleTokenRequest,
    session: AsyncSession = Depends(get_db_session),
) -> Any:
    """Exchange a Google access token for application JWT tokens.

    Flow:
    1. Frontend obtains an access_token from Google via @react-oauth/google
    2. Frontend POSTs { access_token: "..." } to this endpoint
    3. Backend verifies the token by calling Google's userinfo endpoint
    4. Backend finds or creates user, returns JWT tokens
    """

    # Verify the access token by calling Google's userinfo endpoint
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            "https://www.googleapis.com/oauth2/v3/userinfo",
            headers={"Authorization": f"Bearer {data.access_token}"},
        )

    if resp.status_code != 200:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Google access token",
        )

    google_user = resp.json()
    google_sub = google_user.get("sub")
    google_email = google_user.get("email")

    if not google_sub or not google_email:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not retrieve user info from Google",
        )

    # Find existing user by google_sub
    result = await session.execute(
        select(User).where(User.google_sub == google_sub)
    )
    user = result.scalar_one_or_none()

    if not user:
        # Check if a user exists with this email (e.g., registered via email/password)
        result = await session.execute(
            select(User).where(User.identity == google_email)
        )
        user = result.scalar_one_or_none()

        if user:
            # Link Google sub to existing user
            user.google_sub = google_sub
            await session.commit()
            await session.refresh(user)
        else:
            # Create new user
            from app.db.models.client import Client

            user = User(
                identity=google_email,
                google_sub=google_sub,
                display_label=google_user.get("name"),
                status=UserStatus.ACTIVE,
            )
            session.add(user)
            await session.flush()

            # Provision a Client organization for the new user
            new_client = Client(name=f"{google_email}'s Organization")
            session.add(new_client)
            await session.flush()

            new_client_user = ClientUser(
                user_id=user.id,
                client_id=new_client.id,
                role="platform_admin",
            )
            session.add(new_client_user)
            await session.commit()
            await session.refresh(user)

    elif user.status != UserStatus.ACTIVE:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User is inactive",
        )

    # Get client roles
    role_result = await session.execute(
        select(ClientUser).where(ClientUser.user_id == user.id)
    )
    client_users = role_result.scalars().all()
    client_roles = {
        str(cu.client_id): str(cu.role.value if hasattr(cu.role, "value") else cu.role)
        for cu in client_users
    }

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

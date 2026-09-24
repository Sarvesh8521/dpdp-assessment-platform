"""OAuth flow handlers for integration connections.

Handles OAuth authorization URL generation, code exchange, user info fetching,
and secure token storage for Google, Meta, and GitHub providers.

KEY CHANGE from original: OAuth state is now DB-backed for CSRF/replay prevention.
The backend handles the OAuth callback directly — the frontend never sees the auth code.

SECURITY RULES:
- Never return raw tokens to the frontend
- Store tokens via SecretManager only
- Audit every external API call
- Use read-only scopes exclusively
- Validate OAuth state server-side
"""

from __future__ import annotations

import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from app.integrations.base.http_client import AuditedHTTPClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.models.oauth_state import OAuthState
from app.security.secret_manager import get_secret_manager

logger = get_logger(__name__)

# ── Scope Definitions (read-only per spec) ──

GOOGLE_SCOPES = {
    "gtm": ["https://www.googleapis.com/auth/tagmanager.readonly"],
    "ga4": ["https://www.googleapis.com/auth/analytics.readonly"],
    "google_ads": ["https://www.googleapis.com/auth/adwords"],
    "search_console": ["https://www.googleapis.com/auth/webmasters.readonly"],
}

META_SCOPES = ["ads_read", "business_management"]

# OAuth state lifetime
OAUTH_STATE_LIFETIME_MINUTES = 10


class OAuthError(Exception):
    """OAuth flow error."""
    pass


# ── OAuth State Management (DB-backed) ──

async def create_oauth_state(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    client_id: uuid.UUID,
    provider: str,
    requested_scopes: list[str],
    redirect_uri: str,
) -> str:
    """Create a secure, DB-backed OAuth state parameter.

    Returns the state string to include in the authorization URL.
    """
    state_token = secrets.token_urlsafe(32)

    oauth_state = OAuthState(
        state=state_token,
        user_id=user_id,
        client_id=client_id,
        provider=provider,
        requested_scopes=" ".join(requested_scopes),
        redirect_uri=redirect_uri,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=OAUTH_STATE_LIFETIME_MINUTES),
    )
    session.add(oauth_state)
    await session.flush()

    return state_token


async def validate_oauth_state(
    session: AsyncSession,
    state: str,
    expected_provider: str | None = None,
) -> OAuthState:
    """Validate an OAuth state parameter from a callback.

    Checks:
    - State exists in DB
    - State has not expired
    - State has not already been used (replay prevention)
    - Provider matches (if specified)

    Marks the state as used on success.
    Raises OAuthError on any validation failure.
    """
    result = await session.execute(
        select(OAuthState).where(OAuthState.state == state)
    )
    oauth_state = result.scalar_one_or_none()

    if not oauth_state:
        raise OAuthError("Invalid OAuth state parameter")

    if oauth_state.used_at is not None:
        raise OAuthError("OAuth state has already been used (replay detected)")

    if oauth_state.expires_at < datetime.now(timezone.utc):
        raise OAuthError("OAuth state has expired")

    if expected_provider and oauth_state.provider != expected_provider:
        raise OAuthError(
            f"OAuth state provider mismatch: expected {expected_provider}, got {oauth_state.provider}"
        )

    # Mark as used
    oauth_state.used_at = datetime.now(timezone.utc)
    await session.flush()

    return oauth_state


# ── Google OAuth Handler ──

class GoogleOAuthHandler:
    """Handle Google OAuth 2.0 flows for GTM, GA4, Google Ads, Search Console."""

    GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
    GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
    GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"

    def __init__(self) -> None:
        self.settings = get_settings()

    def build_auth_url(
        self,
        provider: str,
        state: str,
        scopes: list[str] | None = None,
    ) -> str:
        """Build Google OAuth authorization URL with read-only scopes.

        Uses the backend callback URI from configuration — never a frontend URL.
        """
        if scopes is None:
            scopes = GOOGLE_SCOPES.get(provider, [])

        # Use ONLY the scope required for the selected integration per Section 6
        all_scopes = list(scopes)

        from urllib.parse import urlencode
        params = {
            "client_id": self.settings.google_client_id,
            "redirect_uri": self.settings.google_oauth_redirect_uri,
            "response_type": "code",
            "scope": " ".join(all_scopes),
            "access_type": "offline",
            "prompt": "select_account consent",
            "state": state,
        }

        # Safe debug logging per Section 14 (only redirect_uri, provider, integration)
        logger.info(
            "google_oauth_auth_url_generated",
            redirect_uri=self.settings.google_oauth_redirect_uri,
            provider=provider,
            integration=provider,
        )
        print(f"[GOOGLE_OAUTH_DEBUG] redirect_uri={self.settings.google_oauth_redirect_uri} provider={provider} integration={provider}")

        return f"{self.GOOGLE_AUTH_URL}?{urlencode(params)}"

    async def exchange_code(self, code: str, **kwargs: Any) -> dict[str, Any]:
        """Exchange authorization code for tokens.

        Uses the backend callback URI from config (must match what was sent to Google).
        NEVER returns this to the frontend directly.
        """
        audit_cb = kwargs.get("audit_callback")
        client_id_str = kwargs.get("client_id")
        async with AuditedHTTPClient(
            provider="google", audit_callback=audit_cb,
            client_id=client_id_str, assessment_id=None, obligations_served=["APP.auth.google"]
        ) as client:
            try:
                return await client.post(
                    self.GOOGLE_TOKEN_URL,
                    data={
                        "client_id": self.settings.google_client_id,
                        "client_secret": self.settings.google_client_secret,
                        "code": code,
                        "grant_type": "authorization_code",
                        "redirect_uri": self.settings.google_oauth_redirect_uri,
                    },
                    operation="google_oauth_exchange"
                )
            except Exception as e:
                logger.error("google_oauth_token_exchange_failed", error=str(e))
                raise OAuthError(f"Google OAuth token exchange failed: {e}")

    async def refresh_access_token(
        self,
        client_id: str,
        provider: str,
        refresh_token: str,
        **kwargs: Any
    ) -> dict[str, Any]:
        """Refresh an expired access token using a refresh token and store securely."""
        audit_cb = kwargs.get("audit_callback")
        async with AuditedHTTPClient(
            provider="google", audit_callback=audit_cb,
            client_id=client_id, assessment_id=None, obligations_served=["APP.auth.google"]
        ) as client:
            try:
                new_tokens = await client.post(
                    self.GOOGLE_TOKEN_URL,
                    data={
                        "client_id": self.settings.google_client_id,
                        "client_secret": self.settings.google_client_secret,
                        "refresh_token": refresh_token,
                        "grant_type": "refresh_token",
                    },
                    operation="google_oauth_refresh"
                )
                
                # Keep the original refresh token if the response doesn't return a new one
                if "refresh_token" not in new_tokens:
                    new_tokens["refresh_token"] = refresh_token
                    
                await self.store_tokens_securely(client_id, provider, new_tokens)
                return new_tokens
            except Exception as e:
                logger.error("google_token_refresh_failed", error=str(e))
                raise OAuthError(f"Google token refresh failed: {e}")


    async def store_tokens_securely(
        self,
        client_id: str,
        provider: str,
        token_data: dict[str, Any],
    ) -> str:
        """Store OAuth tokens in SecretManager. Returns a reference ID."""
        secret_manager = get_secret_manager()
        import json
        token_json = json.dumps({
            "access_token": token_data.get("access_token"),
            "refresh_token": token_data.get("refresh_token"),
            "expires_in": token_data.get("expires_in"),
            "token_type": token_data.get("token_type"),
            "scope": token_data.get("scope"),
            "obtained_at": datetime.now(timezone.utc).isoformat(),
        })
        ref = await secret_manager.store_secret(
            client_id,
            f"{provider}_oauth_token",
            token_json,
        )
        return ref


# ── Meta OAuth Handler ──

class MetaOAuthHandler:
    """Handle Meta (Facebook) Business OAuth flow."""

    META_AUTH_URL = "https://www.facebook.com/v21.0/dialog/oauth"
    META_TOKEN_URL = "https://graph.facebook.com/v21.0/oauth/access_token"
    META_ME_URL = "https://graph.facebook.com/v21.0/me"

    def __init__(self) -> None:
        self.settings = get_settings()

    def build_auth_url(self, redirect_uri: str, state: str) -> str:
        """Build Meta OAuth authorization URL."""
        from urllib.parse import urlencode
        scopes = ",".join(META_SCOPES)
        params = {
            "client_id": self.settings.meta_app_id,
            "redirect_uri": redirect_uri,
            "scope": scopes,
            "response_type": "code",
            "state": state,
        }
        return f"{self.META_AUTH_URL}?{urlencode(params)}"

    async def exchange_code(self, code: str, redirect_uri: str, **kwargs: Any) -> dict[str, Any]:
        """Exchange Meta auth code for access token."""
        audit_cb = kwargs.get("audit_callback")
        client_id_str = kwargs.get("client_id")
        async with AuditedHTTPClient(
            provider="meta", audit_callback=audit_cb,
            client_id=client_id_str, assessment_id=None, obligations_served=["APP.auth.meta"]
        ) as client:
            try:
                return await client.get(
                    self.META_TOKEN_URL,
                    params={
                        "client_id": self.settings.meta_app_id,
                        "client_secret": self.settings.meta_app_secret,
                        "code": code,
                        "redirect_uri": redirect_uri,
                    },
                    operation="meta_oauth_exchange",
                )
            except Exception as e:
                logger.error("meta_oauth_token_exchange_failed", error=str(e))
                raise OAuthError(f"Meta OAuth token exchange failed: {e}")

    async def store_tokens_securely(
        self,
        client_id: str,
        token_data: dict[str, Any],
    ) -> str:
        """Store Meta token in SecretManager."""
        secret_manager = get_secret_manager()
        import json
        token_json = json.dumps({
            "access_token": token_data.get("access_token"),
            "token_type": token_data.get("token_type", "bearer"),
            "expires_in": token_data.get("expires_in"),
            "obtained_at": datetime.now(timezone.utc).isoformat(),
        })
        return await secret_manager.store_secret(
            client_id, "meta_oauth_token", token_json,
        )


# ── GitHub App Handler ──

class GitHubAppHandler:
    """Handle GitHub App installation and OAuth flow.

    PHASE2_SPEC Section 4.6: GitHub App (NOT personal access token),
    scoped to named repositories. Never a personal access token.

    Flow:
    1. User clicks Connect GitHub → get_install_url() sends them to GitHub
    2. GitHub redirects back with ?installation_id=...&state=...
    3. We store the installation_id securely.
    4. get_installation_token() generates a JWT from App private key,
       then calls POST /app/installations/{id}/access_tokens for an
       installation-scoped token used for repo listing & scanning
    """

    GITHUB_API_BASE = "https://api.github.com"

    def __init__(self) -> None:
        self.settings = get_settings()

    def get_install_url(self, state: str) -> str:
        """Return the GitHub App installation URL.

        PHASE2_SPEC Section 4.6: GitHub App only, no PAT, no user OAuth scopes.
        Permissions are configured in the GitHub App settings (contents:read,
        metadata:read). No OAuth scopes like 'repo' are requested.

        Uses the App installation page so the user selects which repos to
        grant access to. GitHub redirects back with installation_id + state.
        """
        if not self.settings.github_app_install_url:
            raise OAuthError("GITHUB_APP_INSTALL_URL must be configured for the GitHub App flow.")
            
        separator = "&" if "?" in self.settings.github_app_install_url else "?"
        return f"{self.settings.github_app_install_url}{separator}state={state}"

    def _build_jwt(self) -> str:
        """Build a JWT for GitHub App authentication.

        The JWT is signed with the App's private key and used to request
        installation access tokens. Valid for up to 10 minutes.
        Uses python-jose (already in requirements as python-jose[cryptography]).
        """
        from jose import jwt as jose_jwt
        import time

        private_key = self.settings.github_app_private_key
        app_id = self.settings.github_app_id

        if not private_key or not app_id:
            raise OAuthError("GitHub App private key or App ID not configured")

        # If the key doesn't look like a PEM key, it might be a fingerprint
        if not private_key.strip().startswith("-----BEGIN"):
            raise OAuthError(
                "GITHUB_APP_PRIVATE_KEY must be a PEM-encoded RSA private key, "
                "not a fingerprint. Download it from your GitHub App settings."
            )

        now = int(time.time())
        payload = {
            "iat": now - 60,      # Issued at time (60s in past for clock drift)
            "exp": now + (9 * 60),  # Expiration (9 minutes)
            "iss": app_id,         # GitHub App ID
        }
        return jose_jwt.encode(payload, private_key, algorithm="RS256")

    async def get_installation_token(self, installation_id: str, **kwargs: Any) -> dict[str, Any]:
        """Generate an installation access token for the GitHub App.

        Uses JWT authentication to call POST /app/installations/{id}/access_tokens.
        The returned token is scoped to the repositories the user granted access to.
        """
        try:
            app_jwt = self._build_jwt()
        except Exception as e:
            logger.warning("github_jwt_generation_failed", error=str(e))
            # Return empty — caller will handle gracefully
            return {}

        audit_cb = kwargs.get("audit_callback")
        client_id = kwargs.get("client_id")
        assessment_id = kwargs.get("assessment_id", None)
        
        async with AuditedHTTPClient(
            provider="github",
            audit_callback=audit_cb,
            client_id=client_id,
            assessment_id=assessment_id,
            obligations_served=["APP.auth.github"]
        ) as client:
            try:
                return await client.post(
                    f"{self.GITHUB_API_BASE}/app/installations/{installation_id}/access_tokens",
                    headers={
                        "Authorization": f"Bearer {app_jwt}",
                        "Accept": "application/vnd.github+json",
                        "X-GitHub-Api-Version": "2022-11-28",
                    },
                    operation="github_installation_token",
                )
            except Exception as e:
                logger.error(
                    "github_installation_token_failed",
                    error=str(e),
                    installation_id=installation_id,
                )
                raise OAuthError(
                    f"Failed to get GitHub installation token: {e}"
                )

    async def store_tokens_securely(
        self,
        client_id: str,
        installation_id: str,
    ) -> str:
        """Store GitHub App installation data in SecretManager.

        Stores only the installation_id — the minimum credential needed.
        Installation access tokens are short-lived and generated on demand
        via App JWT + POST /app/installations/{id}/access_tokens.

        No user-level access tokens are stored for the GitHub App flow.
        """
        secret_manager = get_secret_manager()
        import json
        token_json = json.dumps({
            "installation_id": installation_id,
            "obtained_at": datetime.now(timezone.utc).isoformat(),
        })
        return await secret_manager.store_secret(
            client_id, "github_app_token", token_json,
        )

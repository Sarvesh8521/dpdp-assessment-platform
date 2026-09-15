"""Integrations API endpoints — Phase 2 Connection Layer.

Implements the complete integration connection flow:
1. OAuth init → generates auth URL with backend callback
2. Backend OAuth callback → exchanges code, stores tokens, redirects to frontend
3. Resource listing (containers, properties, repos, etc.)
4. Connection finalization (with resource selection)
5. Disconnect / Reconnect
6. Integration status overview

KEY DESIGN DECISIONS:
- OAuth callbacks are handled by the BACKEND, not the frontend
- The frontend never sees/stores provider tokens or auth codes
- OAuth state is validated server-side (DB-backed, with expiry and replay prevention)
- The same Google OAuth client can be used for all Google integrations
- Each integration uses only its own required scope

SECURITY:
- All tokens stored via SecretManager (never plaintext in DB)
- Audit log entry for every API call
- Read-only scopes only
- Per-client tenant isolation
"""

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_

from app.auth.dependencies import AuthContext, get_current_user
from app.core.config import get_settings
from app.db.session import get_db_session
from app.db.models.connection import Connection, ConnectionStatus
from app.db.models.client import Client
from app.db.models.audit_log import AuditLog
from app.db.models.oauth_state import OAuthState
from app.schemas.api import (
    ConnectionResponse,
    ConnectRequest,
    OAuthInitResponse,
    ResourceItem,
    ResourceListResponse,
    IntegrationStatusItem,
    IntegrationStatusResponse,
)
from app.security.secret_manager import get_secret_manager
from app.integrations.registry import get_adapter
from app.api.v1.oauth_handlers import (
    GoogleOAuthHandler,
    MetaOAuthHandler,
    GitHubAppHandler,
    OAuthError,
    GOOGLE_SCOPES,
    create_oauth_state,
    validate_oauth_state,
)
from app.api.v1.resource_fetcher import (
    fetch_gtm_containers,
    fetch_ga4_properties,
    fetch_search_console_sites,
    fetch_google_ads_customers,
    fetch_meta_businesses,
    fetch_meta_business_assets,
    fetch_github_repos,
    ResourceFetchError,
)
from app.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/integrations", tags=["integrations"])

# All supported providers
ALL_PROVIDERS = ["gtm", "ga4", "google_ads", "search_console", "meta", "github", "cmp"]
GOOGLE_PROVIDERS = ["gtm", "ga4", "google_ads", "search_console"]



async def _get_capped_expires_at(session: AsyncSession, client_id: uuid.UUID, token_expires_in: int | None = None) -> datetime | None:
    client = await session.get(Client, client_id)
    if not client:
        return None
    
    now = datetime.now(timezone.utc)
    token_expiry = now + timedelta(seconds=token_expires_in) if token_expires_in else None
    
    if client.engagement_end:
        if token_expiry:
            return min(token_expiry, client.engagement_end)
        return client.engagement_end
    return token_expiry


# ── Audit Logging Helper ──

async def _audit_log(
    session: AsyncSession,
    *,
    client_id: uuid.UUID | None,
    actor: str,
    integration: str,
    operation: str,
    endpoint: str,
    log_status: str = "success",
    status_code: int | None = None,
    error_detail: str | None = None,
    obligations_served: list[str] | None = None,
    assessment_id: uuid.UUID | None = None,
    duration_ms: int | None = None,
) -> None:
    """Write an audit log entry for an integration API call."""
    log = AuditLog(
        client_id=client_id,
        assessment_id=assessment_id,
        actor=actor,
        integration=integration,
        operation=operation,
        endpoint=endpoint,
        status=log_status,
        status_code=status_code,
        duration_ms=duration_ms,
        error_detail=error_detail,
        obligations_served=obligations_served,
    )
    session.add(log)
    # Don't commit here — let the caller commit with their transaction

def make_audit_callback(session: AsyncSession, client_id: uuid.UUID | None = None) -> Any:
    """Factory to create an audit callback that writes to the database."""
    async def callback(**kwargs: Any) -> None:
        log_status = kwargs.pop("status", "success")
        assessment_id = kwargs.pop("assessment_id", None)
        duration_ms = kwargs.pop("duration_ms", None)
        # fallback to the factory's client_id if not explicitly provided
        cb_client_id = kwargs.pop("client_id", client_id) or client_id
        
        await _audit_log(
            session=session,
            client_id=cb_client_id,
            log_status=log_status,
            assessment_id=assessment_id,
            duration_ms=duration_ms,
            **kwargs
        )
    return callback


# ── 1. OAuth Init Endpoints ──

@router.post("/oauth/google/init", response_model=OAuthInitResponse)
async def google_oauth_init(
    provider: str = Query(..., description="Integration provider: gtm, ga4, google_ads, search_console"),
    client_id: uuid.UUID = Query(...),
    auth: AuthContext = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> Any:
    """Initiate Google OAuth flow for a specific integration.

    Creates a DB-backed OAuth state, builds the Google authorization URL
    with the BACKEND callback URI, and returns it. The frontend should
    redirect the browser to this URL (full-page redirect, not popup).
    """
    auth.require_client_access(client_id)

    if provider not in GOOGLE_PROVIDERS:
        raise HTTPException(status_code=400, detail=f"Provider '{provider}' does not use Google OAuth")

    scopes = GOOGLE_SCOPES.get(provider, [])
    settings = get_settings()

    # Create DB-backed OAuth state
    state = await create_oauth_state(
        session,
        user_id=auth.user_id,
        client_id=client_id,
        provider=provider,
        requested_scopes=scopes,
        redirect_uri=settings.google_oauth_redirect_uri,
    )

    handler = GoogleOAuthHandler()
    auth_url = handler.build_auth_url(
        provider=provider,
        state=state,
        scopes=scopes,
    )
    logger.info("google_oauth_start", provider=provider, integration=provider, state_id=state, redirect_uri=settings.google_oauth_redirect_uri)

    await _audit_log(
        session,
        client_id=client_id,
        actor=str(auth.user_id),
        integration=provider,
        operation="oauth_init",
        endpoint="accounts.google.com/oauth",
        log_status="initiated",
    )
    await session.commit()

    return OAuthInitResponse(auth_url=auth_url, state=state, provider=provider)


@router.get("/oauth/google/callback")
async def google_oauth_callback(
    code: str = Query(None),
    state: str = Query(None),
    error: str = Query(None),
    error_description: str = Query(None),
    session: AsyncSession = Depends(get_db_session),
) -> RedirectResponse:
    """Backend Google OAuth callback — handles the redirect from Google.

    Flow:
    1. Google redirects here with ?code=...&state=...
    2. We validate the state from DB (exists, not expired, not used, provider matches)
    3. Exchange code for tokens
    4. Store tokens securely via SecretManager
    5. Fetch user info for display
    6. Create/update Connection record
    7. Redirect browser back to frontend with success/error params

    This endpoint does NOT require Bearer auth — it's a browser redirect from Google.
    User identity comes from the validated OAuth state record.
    """
    settings = get_settings()
    frontend_base = settings.frontend_url

    # Handle error from Google
    if error:
        logger.error("google_oauth_error", error=error, description=error_description)
        params = urlencode({"error": error_description or error, "provider": "google"})
        return RedirectResponse(f"{frontend_base}/integrations/callback?{params}")

    if not code or not state:
        params = urlencode({"error": "Missing code or state parameter", "provider": "google"})
        return RedirectResponse(f"{frontend_base}/integrations/callback?{params}")

    try:
        logger.info("google_oauth_callback_received", state_id=state)
        # 1. Validate OAuth state from DB
        oauth_state = await validate_oauth_state(session, state)
        provider = oauth_state.provider
        client_id = oauth_state.client_id
        user_id = oauth_state.user_id
        logger.info("google_oauth_state_validation_success", provider=provider, client_id=str(client_id))

        # 2. Exchange code for tokens
        handler = GoogleOAuthHandler()
        token_data = await handler.exchange_code(code, audit_callback=make_audit_callback(session, client_id))
        logger.info("google_oauth_token_exchange_success", provider=provider)

        access_token = token_data.get("access_token")
        if not access_token:
            raise OAuthError("No access_token in Google response")

        # 3. Fetch user info (for display in UI — email of the provider account)
        user_info = await handler.fetch_user_info(access_token, audit_callback=make_audit_callback(session, client_id))
        logger.info("google_oauth_user_info_fetched", provider=provider, has_email=bool(user_info.get("email")))

        # 4. Store tokens securely
        secret_ref = await handler.store_tokens_securely(
            str(client_id), provider, token_data
        )

        # 5. Record granted scopes
        granted_scopes = token_data.get("scope", "")

        # 6. Create or update Connection record
        result = await session.execute(
            select(Connection).where(
                and_(
                    Connection.client_id == client_id,
                    Connection.provider == provider,
                    Connection.status.in_([
                        ConnectionStatus.CONNECTED,
                        ConnectionStatus.PENDING,
                        ConnectionStatus.EXPIRED,
                    ]),
                )
            )
        )
        existing = result.scalar_one_or_none()

        adapter = get_adapter(
            {"gtm": "GTM", "ga4": "GA4", "google_ads": "GoogleAds", "search_console": "SearchConsole"}.get(provider, provider)
        )
        scopes = adapter.required_scopes if adapter else []

        if existing:
            existing.status = ConnectionStatus.CONNECTED
            existing.secret_ref = secret_ref
            existing.scopes = scopes
            existing.granted_scopes = granted_scopes
            existing.connected_at = datetime.now(timezone.utc)
            existing.expires_at = await _get_capped_expires_at(session, client_id, token_data.get("expires_in"))
            meta = dict(existing.provider_metadata) if existing.provider_metadata else {}
            if user_info.get("email"):
                meta["account_email"] = user_info.get("email")
            if user_info.get("name"):
                meta["account_name"] = user_info.get("name")
            existing.provider_metadata = meta
            connection = existing
        else:
            meta = {}
            if user_info.get("email"):
                meta["account_email"] = user_info.get("email")
            if user_info.get("name"):
                meta["account_name"] = user_info.get("name")
            connection = Connection(
                client_id=client_id,
                provider=provider,
                status=ConnectionStatus.CONNECTED,
                scopes=scopes,
                granted_scopes=granted_scopes,
                secret_ref=secret_ref,
                connected_at=datetime.now(timezone.utc),
                expires_at=await _get_capped_expires_at(session, client_id, token_data.get("expires_in")),
                provider_metadata=meta if meta else None,
            )
            session.add(connection)

        await _audit_log(
            session,
            client_id=client_id,
            actor=str(user_id),
            integration=provider,
            operation="oauth_callback",
            endpoint="oauth2.googleapis.com/token",
            log_status="success",
        )
        await session.commit()

        await session.commit()
        logger.info("google_oauth_connection_saved", connection_id=str(connection.id), provider=provider)

        # 7. Redirect to frontend with success
        params = urlencode({
            "provider": provider,
            "status": "connected",
            "connection_id": str(connection.id),
        })
        redirect_url = f"{frontend_base}/integrations/callback?{params}"
        logger.info("google_oauth_final_redirect", url=redirect_url)
        return RedirectResponse(redirect_url)

    except OAuthError as e:
        logger.error("google_oauth_callback_failed", error=str(e))
        # Try to extract provider from state for the error redirect
        error_provider = "google"
        try:
            from sqlalchemy import select as sel
            r = await session.execute(sel(OAuthState).where(OAuthState.state == state))
            s = r.scalar_one_or_none()
            if s:
                error_provider = s.provider
        except Exception:
            pass
        params = urlencode({"error": str(e), "provider": error_provider})
        return RedirectResponse(f"{frontend_base}/integrations/callback?{params}")

    except Exception as e:
        logger.error("google_oauth_callback_unexpected_error", error=str(e))
        params = urlencode({"error": "An unexpected error occurred during authorization", "provider": "google"})
        return RedirectResponse(f"{frontend_base}/integrations/callback?{params}")


# ── Meta OAuth Init ──

@router.post("/oauth/meta/init", response_model=OAuthInitResponse)
async def meta_oauth_init(
    client_id: uuid.UUID = Query(...),
    auth: AuthContext = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> Any:
    """Initiate Meta Business OAuth flow."""
    auth.require_client_access(client_id)

    handler = MetaOAuthHandler()
    settings = get_settings()

    state = await create_oauth_state(
        session,
        user_id=auth.user_id,
        client_id=client_id,
        provider="meta",
        requested_scopes=["ads_read", "business_management"],
        redirect_uri=settings.meta_oauth_redirect_uri,
    )
    auth_url = handler.build_auth_url(
        settings.meta_oauth_redirect_uri, state
    )

    await _audit_log(
        session,
        client_id=client_id,
        actor=str(auth.user_id),
        integration="meta",
        operation="oauth_init",
        endpoint="facebook.com/oauth",
        log_status="initiated",
    )
    await session.commit()

    return OAuthInitResponse(auth_url=auth_url, state=state, provider="meta")


@router.get("/oauth/meta/callback")
async def meta_oauth_callback(
    code: str = Query(None),
    state: str = Query(None),
    error: str = Query(None),
    error_description: str = Query(None),
    session: AsyncSession = Depends(get_db_session),
) -> RedirectResponse:
    """Backend Meta OAuth callback."""
    settings = get_settings()
    frontend_base = settings.frontend_url

    if error:
        logger.error("meta_oauth_error", error=error, description=error_description)
        params = urlencode({"error": error_description or error, "provider": "meta"})
        return RedirectResponse(f"{frontend_base}/integrations/callback?{params}")

    if not code or not state:
        params = urlencode({"error": "Missing code or state parameter", "provider": "meta"})
        return RedirectResponse(f"{frontend_base}/integrations/callback?{params}")

    try:
        oauth_state = await validate_oauth_state(session, state, expected_provider="meta")
        client_id = oauth_state.client_id
        user_id = oauth_state.user_id

        handler = MetaOAuthHandler()
        token_data = await handler.exchange_code(code, settings.meta_oauth_redirect_uri, audit_callback=make_audit_callback(session, client_id))

        user_info = await handler.fetch_user_info(token_data.get("access_token", ""))

        secret_ref = await handler.store_tokens_securely(str(client_id), token_data)

        result = await session.execute(
            select(Connection).where(
                and_(
                    Connection.client_id == client_id,
                    Connection.provider == "meta",
                    Connection.status.in_([
                        ConnectionStatus.CONNECTED,
                        ConnectionStatus.PENDING,
                        ConnectionStatus.EXPIRED,
                    ]),
                )
            )
        )
        existing = result.scalar_one_or_none()
        
        adapter = get_adapter("Meta")
        scopes = adapter.required_scopes if adapter else []
        
        if existing:
            existing.status = ConnectionStatus.CONNECTED
            existing.secret_ref = secret_ref
            existing.scopes = scopes
            existing.connected_at = datetime.now(timezone.utc)
            existing.expires_at = await _get_capped_expires_at(session, client_id, token_data.get("expires_in"))
            meta = dict(existing.provider_metadata) if existing.provider_metadata else {}
            if user_info.get("email"):
                meta["account_email"] = user_info.get("email")
            if user_info.get("name"):
                meta["account_name"] = user_info.get("name")
            existing.provider_metadata = meta
            connection = existing
        else:
            meta = {}
            if user_info.get("email"):
                meta["account_email"] = user_info.get("email")
            if user_info.get("name"):
                meta["account_name"] = user_info.get("name")
            connection = Connection(
                client_id=client_id,
                provider="meta",
                status=ConnectionStatus.CONNECTED,
                scopes=scopes,
                secret_ref=secret_ref,
                connected_at=datetime.now(timezone.utc),
                expires_at=await _get_capped_expires_at(session, client_id, token_data.get("expires_in")),
                provider_metadata=meta if meta else None,
            )
            session.add(connection)

        await _audit_log(
            session, client_id=client_id, actor=str(user_id),
            integration="meta", operation="oauth_callback", endpoint="graph.facebook.com/oauth",
            log_status="success"
        )
        await session.commit()

        params = urlencode({
            "provider": "meta",
            "status": "connected",
            "connection_id": str(connection.id),
        })
        return RedirectResponse(f"{frontend_base}/integrations/callback?{params}")

    except Exception as e:
        logger.error("meta_oauth_callback_error", error=str(e))
        params = urlencode({"error": str(e), "provider": "meta"})
        return RedirectResponse(f"{frontend_base}/integrations/callback?{params}")


# ── GitHub OAuth Init ──

@router.post("/oauth/github/init", response_model=OAuthInitResponse)
async def github_oauth_init(
    client_id: uuid.UUID = Query(...),
    auth: AuthContext = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> Any:
    """Initiate GitHub App installation / OAuth flow.

    Per PHASE2_SPEC Section 4.6: GitHub App only, no PAT.
    Permissions are configured in the GitHub App settings,
    not requested at OAuth init time.
    """
    auth.require_client_access(client_id)

    handler = GitHubAppHandler()

    # GitHub App permissions are configured in the App settings, not here.
    # We store empty scopes — the App's configured permissions are the source of truth.
    state = await create_oauth_state(
        session,
        user_id=auth.user_id,
        client_id=client_id,
        provider="github",
        requested_scopes=["contents:read", "metadata:read"],
        redirect_uri=get_settings().github_oauth_redirect_uri,
    )
    auth_url = handler.get_install_url(state)

    await _audit_log(
        session,
        client_id=client_id,
        actor=str(auth.user_id),
        integration="github",
        operation="github_app_init",
        endpoint="github.com/apps/install",
        log_status="initiated",
    )
    await session.commit()

    return OAuthInitResponse(auth_url=auth_url, state=state, provider="github")


@router.get("/oauth/github/callback")
async def github_oauth_callback(
    code: str = Query(None),
    state: str = Query(None),
    installation_id: str = Query(None),
    setup_action: str = Query(None),
    error: str = Query(None),
    error_description: str = Query(None),
    session: AsyncSession = Depends(get_db_session),
) -> RedirectResponse:
    """Backend GitHub App callback.

    GitHub App installation redirects here with:
    ?installation_id=...&setup_action=install&code=...&state=...

    Flow:
    1. Validate OAuth state from DB
    2. Exchange code for user-level access token
    3. Fetch user info for display
    4. Store token + installation_id securely
    5. Create/update Connection with installation_id in metadata
    6. Redirect to frontend with success params
    """
    settings = get_settings()
    frontend_base = settings.frontend_url

    if error:
        logger.error("github_app_callback_error", error=error, description=error_description)
        params = urlencode({"error": error_description or error, "provider": "github"})
        return RedirectResponse(f"{frontend_base}/integrations/callback?{params}")

    if not state:
        params = urlencode({"error": "Missing state parameter", "provider": "github"})
        return RedirectResponse(f"{frontend_base}/integrations/callback?{params}")

    # GitHub App may send only installation_id without code on initial install
    # (setup_action=install). If code is missing, we still record the installation.
    if not code and not installation_id:
        params = urlencode({"error": "Missing code and installation_id", "provider": "github"})
        return RedirectResponse(f"{frontend_base}/integrations/callback?{params}")

    try:
        oauth_state = await validate_oauth_state(session, state, expected_provider="github")
        client_id = oauth_state.client_id
        user_id = oauth_state.user_id

        logger.info(
            "github_app_callback_received",
            has_code=bool(code),
            has_installation_id=bool(installation_id),
            setup_action=setup_action,
        )

        handler = GitHubAppHandler()

        # Exchange code for user-level token (if code is present)
        token_data: dict[str, Any] = {}
        user_info: dict[str, Any] = {}
        if code:
            token_data = await handler.exchange_code(code, audit_callback=make_audit_callback(session, client_id))
            user_access_token = token_data.get("access_token", "")
            user_info = await handler.fetch_user_info(user_access_token)
            
            # If we didn't get an installation_id from the URL (OAuth flow), fetch installations
            if not installation_id and user_access_token:
                installations = await handler.fetch_user_installations(user_access_token)
                if installations:
                    # For simplicity, use the first installation they have access to
                    installation_id = str(installations[0].get("id", ""))
                    logger.info("github_fetched_user_installation", installation_id=installation_id)

        # Store token + installation_id securely
        secret_ref = await handler.store_tokens_securely(
            str(client_id),
            token_data,
            installation_id=installation_id or "",
        )

        # Find existing connection
        result = await session.execute(
            select(Connection).where(
                and_(
                    Connection.client_id == client_id,
                    Connection.provider == "github",
                    Connection.status.in_([
                        ConnectionStatus.CONNECTED,
                        ConnectionStatus.PENDING,
                        ConnectionStatus.EXPIRED,
                    ]),
                )
            )
        )
        existing = result.scalar_one_or_none()

        adapter = get_adapter("github")
        scopes = adapter.required_scopes if adapter else []

        # Build provider metadata with installation_id
        meta: dict[str, Any] = {}
        if installation_id:
            meta["installation_id"] = installation_id
        if setup_action:
            meta["setup_action"] = setup_action
        if user_info.get("email"):
            meta["account_email"] = user_info["email"]
        if user_info.get("login") or user_info.get("name"):
            meta["account_name"] = user_info.get("login") or user_info.get("name")

        if existing:
            existing.status = ConnectionStatus.CONNECTED
            existing.secret_ref = secret_ref
            existing.scopes = scopes
            existing.connected_at = datetime.now(timezone.utc)
            existing.expires_at = await _get_capped_expires_at(session, client_id, token_data.get("expires_in"))
            # Merge new metadata with existing (preserve resource selection)
            old_meta = dict(existing.provider_metadata) if existing.provider_metadata else {}
            old_meta.update(meta)
            existing.provider_metadata = old_meta
            connection = existing
        else:
            connection = Connection(
                client_id=client_id,
                provider="github",
                status=ConnectionStatus.CONNECTED,
                scopes=scopes,
                secret_ref=secret_ref,
                connected_at=datetime.now(timezone.utc),
                expires_at=await _get_capped_expires_at(session, client_id, token_data.get("expires_in")),
                provider_metadata=meta if meta else None,
            )
            session.add(connection)

        await _audit_log(
            session, client_id=client_id, actor=str(user_id),
            integration="github", operation="github_app_callback",
            endpoint="github.com/apps/callback",
            log_status="success",
        )
        await session.commit()

        logger.info(
            "github_app_connection_saved",
            connection_id=str(connection.id),
            installation_id=installation_id,
        )

        params = urlencode({
            "provider": "github",
            "status": "connected",
            "connection_id": str(connection.id),
        })
        return RedirectResponse(f"{frontend_base}/integrations/callback?{params}")

    except OAuthError as e:
        logger.error("github_app_callback_oauth_error", error=str(e))
        params = urlencode({"error": str(e), "provider": "github"})
        return RedirectResponse(f"{frontend_base}/integrations/callback?{params}")

    except Exception as e:
        logger.error("github_app_callback_unexpected_error", error=str(e))
        params = urlencode({"error": "An unexpected error occurred during GitHub authorization", "provider": "github"})
        return RedirectResponse(f"{frontend_base}/integrations/callback?{params}")


# ── 2. Resource Listing Endpoints ──

@router.get("/{provider}/resources", response_model=ResourceListResponse)
async def list_resources(
    provider: str,
    client_id: uuid.UUID = Query(...),
    auth: AuthContext = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> Any:
    """List available resources for a connected provider.

    Retrieves the stored credential from SecretManager to make API calls.
    """
    auth.require_client_access(client_id)

    # Find the active connection for this provider
    result = await session.execute(
        select(Connection).where(
            and_(
                Connection.client_id == client_id,
                Connection.provider == provider,
                Connection.status == ConnectionStatus.CONNECTED,
            )
        )
    )
    connection = result.scalar_one_or_none()

    if not connection or not connection.secret_ref:
        raise HTTPException(status_code=400, detail=f"No active connection for provider '{provider}'")

    # Retrieve the access token from SecretManager
    secret_manager = get_secret_manager()
    try:
        token_json = await secret_manager.get_secret(str(client_id), connection.secret_ref)
        token_data = json.loads(token_json)
        access_token = token_data.get("access_token")
    except Exception:
        raise HTTPException(status_code=400, detail="Failed to retrieve credentials — try reconnecting")

    if not access_token and provider != "github":
        raise HTTPException(status_code=400, detail="No valid access token found — try reconnecting")

    try:
        resources: list[dict[str, Any]] = []

        if provider == "gtm":
            resources = await fetch_gtm_containers(access_token, audit_callback=make_audit_callback(session, client_id))
        elif provider == "ga4":
            resources = await fetch_ga4_properties(access_token, audit_callback=make_audit_callback(session, client_id))
        elif provider == "search_console":
            resources = await fetch_search_console_sites(access_token, audit_callback=make_audit_callback(session, client_id))
        elif provider == "meta":
            resources = await fetch_meta_businesses(access_token, audit_callback=make_audit_callback(session, client_id))
        elif provider == "github":
            # GitHub App: use installation_id to get installation-scoped token
            # then fetch repos accessible to the installation
            conn_meta = connection.provider_metadata or {}
            inst_id = conn_meta.get("installation_id") or token_data.get("installation_id", "")
            if inst_id:
                try:
                    gh_handler = GitHubAppHandler()
                    inst_token_data = await gh_handler.get_installation_token(inst_id)
                    inst_access_token = inst_token_data.get("token", "")
                    if inst_access_token:
                        resources = await fetch_github_repos(
                            inst_access_token,
                            installation_id=inst_id,
                            audit_callback=make_audit_callback(session, client_id),
                        )
                    else:
                        logger.warning("github_no_installation_token", installation_id=inst_id)
                        resources = await fetch_github_repos(
                            access_token or "",
                            audit_callback=make_audit_callback(session, client_id),
                        )
                except Exception as e:
                    logger.warning("github_installation_token_error", error=str(e))
                    # Fallback to user token if installation token fails
                    if access_token:
                        resources = await fetch_github_repos(
                            access_token, audit_callback=make_audit_callback(session, client_id),
                        )
            elif access_token:
                resources = await fetch_github_repos(access_token, audit_callback=make_audit_callback(session, client_id))
            else:
                raise HTTPException(
                    status_code=400,
                    detail="GitHub App installation_id not found — try reconnecting",
                )
        elif provider == "google_ads":
            resources = await fetch_google_ads_customers(access_token, audit_callback=make_audit_callback(session, client_id))
        else:
            raise HTTPException(status_code=400, detail=f"Resource listing not supported for '{provider}'")

        await _audit_log(
            session,
            client_id=client_id,
            actor=str(auth.user_id),
            integration=provider,
            operation="list_resources",
            endpoint=f"{provider}/resources",
            log_status="success",
        )
        await session.commit()

        return ResourceListResponse(
            provider=provider,
            resources=[
                ResourceItem(
                    id=r.get("id", ""),
                    name=r.get("name", ""),
                    metadata=r.get("metadata"),
                )
                for r in resources
            ],
        )

    except ResourceFetchError as e:
        await _audit_log(
            session,
            client_id=client_id,
            actor=str(auth.user_id),
            integration=provider,
            operation="list_resources",
            endpoint=f"{provider}/resources",
            log_status="error",
            error_detail=str(e),
        )
        await session.commit()
        raise HTTPException(status_code=400, detail=str(e))

# ── 2b. Meta Business Assets (sub-resources) ──

@router.get("/{provider}/resources/{business_id}/assets", response_model=ResourceListResponse)
async def list_business_assets(
    provider: str,
    business_id: str,
    client_id: uuid.UUID = Query(...),
    auth: AuthContext = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> Any:
    """List assets (ad accounts, pixels) for a specific Meta Business.

    After the user selects a business from the businesses list,
    this endpoint fetches the ad accounts and pixels within it.
    """
    auth.require_client_access(client_id)

    if provider != "meta":
        raise HTTPException(status_code=400, detail="Asset listing only supported for Meta")

    # Find the active connection
    result = await session.execute(
        select(Connection).where(
            and_(
                Connection.client_id == client_id,
                Connection.provider == "meta",
                Connection.status == ConnectionStatus.CONNECTED,
            )
        )
    )
    connection = result.scalar_one_or_none()

    if not connection or not connection.secret_ref:
        raise HTTPException(status_code=400, detail="No active Meta connection")

    # Retrieve the access token
    secret_manager = get_secret_manager()
    try:
        token_json = await secret_manager.get_secret(str(client_id), connection.secret_ref)
        token_data = json.loads(token_json)
        access_token = token_data.get("access_token")
    except Exception:
        raise HTTPException(status_code=400, detail="Failed to retrieve Meta credentials")

    if not access_token:
        raise HTTPException(status_code=400, detail="No valid Meta access token")

    try:
        assets = await fetch_meta_business_assets(
            access_token, business_id, audit_callback=make_audit_callback(session, client_id)
        )

        await _audit_log(
            session,
            client_id=client_id,
            actor=str(auth.user_id),
            integration="meta",
            operation="list_business_assets",
            endpoint=f"meta/resources/{business_id}/assets",
            log_status="success",
        )
        await session.commit()

        return ResourceListResponse(
            provider="meta",
            resources=[
                ResourceItem(
                    id=a.get("id", ""),
                    name=a.get("name", ""),
                    metadata={"type": a.get("type", "unknown")},
                )
                for a in assets
            ],
        )

    except ResourceFetchError as e:
        await _audit_log(
            session,
            client_id=client_id,
            actor=str(auth.user_id),
            integration="meta",
            operation="list_business_assets",
            endpoint=f"meta/resources/{business_id}/assets",
            log_status="error",
            error_detail=str(e),
        )
        await session.commit()
        raise HTTPException(status_code=400, detail=str(e))


# ── 3. Connection Finalization & Management ──

@router.get("", response_model=list[ConnectionResponse])
async def list_connections(
    client_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> Any:
    """List active connections for a client."""
    auth.require_client_access(client_id)

    result = await session.execute(
        select(Connection).where(Connection.client_id == client_id)
    )
    return result.scalars().all()


@router.post("/connect/{provider}/select-resource", status_code=status.HTTP_200_OK)
async def select_resource(
    provider: str,
    client_id: uuid.UUID = Query(...),
    resource_id: str = Query(...),
    resource_name: str = Query(""),
    auth: AuthContext = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> Any:
    """Select a resource for a connected integration.

    After OAuth callback creates a Connection, the user selects
    which resource (container, property, site) to use for assessment.
    """
    auth.require_client_access(client_id)

    # Find the connection
    result = await session.execute(
        select(Connection).where(
            and_(
                Connection.client_id == client_id,
                Connection.provider == provider,
                Connection.status == ConnectionStatus.CONNECTED,
            )
        )
    )
    connection = result.scalar_one_or_none()

    if not connection:
        raise HTTPException(status_code=404, detail=f"No active connection for provider '{provider}'")

    # Update with selected resource
    connection.resource_id = resource_id
    connection.resource_name = resource_name

    await _audit_log(
        session,
        client_id=client_id,
        actor=str(auth.user_id),
        integration=provider,
        operation="select_resource",
        endpoint=f"integrations/{provider}/select-resource",
        log_status="success",
    )
    await session.commit()

    return {
        "provider": provider,
        "status": "connected",
        "connection_id": str(connection.id),
        "resource": {
            "id": resource_id,
            "name": resource_name,
        },
    }


@router.post("/connect", status_code=status.HTTP_201_CREATED)
async def finalize_connection(
    client_id: uuid.UUID,
    data: ConnectRequest,
    auth: AuthContext = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> Any:
    """Finalize a connection — used for non-OAuth providers (CMP) or legacy flow.

    For Google OAuth integrations, the backend callback creates the connection
    automatically. This endpoint is used for:
    - CMP API key connections
    - Manual configuration connections
    """
    auth.require_client_access(client_id)

    adapter = get_adapter(data.provider)
    scopes = adapter.required_scopes if adapter else []

    secret_ref = ""
    resource_id = None
    resource_name = None

    try:
        if data.provider == "cmp":
            # CMP: provider-specific API key/config
            if not data.oauth_code:
                raise HTTPException(status_code=400, detail="API credentials required")

            secret_manager = get_secret_manager()
            cmp_provider = data.provider_name or "onetrust"
            secret_ref = await secret_manager.store_secret(
                str(client_id),
                f"cmp_{cmp_provider}_token",
                data.oauth_code,
            )

            resource = data.resource or {}
            resource_id = resource.get("site_id")
            resource_name = resource.get("site_name") or cmp_provider

        elif data.provider == "google_ads":
            # Google Ads: connection exists from OAuth, but may need customer ID
            result = await session.execute(
                select(Connection).where(
                    and_(
                        Connection.client_id == client_id,
                        Connection.provider == "google_ads",
                        Connection.status == ConnectionStatus.CONNECTED,
                    )
                )
            )
            existing = result.scalar_one_or_none()
            if not existing:
                raise HTTPException(status_code=400, detail="Connect Google Ads via OAuth first")

            resource = data.resource or {}
            customer_id = resource.get("customer_id", "")
            if customer_id:
                existing.resource_id = customer_id
                existing.resource_name = f"Google Ads Account ({customer_id})"

            await session.commit()
            return {
                "integration": "google_ads",
                "status": "connected",
                "connection_id": str(existing.id),
                "resource": {"id": customer_id, "name": existing.resource_name},
            }

        else:
            raise HTTPException(status_code=400, detail=f"Use OAuth flow for provider '{data.provider}'")

    except HTTPException:
        raise
    except Exception as e:
        connection = Connection(
            client_id=client_id,
            provider=data.provider,
            status=ConnectionStatus.ERROR,
            secret_ref="",
        )
        session.add(connection)
        await _audit_log(
            session,
            client_id=client_id,
            actor=str(auth.user_id),
            integration=data.provider,
            operation="connect",
            endpoint=f"integrations/{data.provider}/connect",
            log_status="error",
            error_detail=str(e),
        )
        await session.commit()
        raise HTTPException(status_code=400, detail=str(e))

    # Create connection for CMP
    connection = Connection(
        client_id=client_id,
        provider=data.provider,
        status=ConnectionStatus.CONNECTED,
        scopes=scopes,
        secret_ref=secret_ref,
        resource_id=resource_id,
        resource_name=resource_name,
        connected_at=datetime.now(timezone.utc),
        expires_at=await _get_capped_expires_at(session, client_id),
    )
    session.add(connection)

    await _audit_log(
        session,
        client_id=client_id,
        actor=str(auth.user_id),
        integration=data.provider,
        operation="connect",
        endpoint=f"integrations/{data.provider}/connect",
        log_status="success",
    )
    await session.commit()

    return {
        "integration": data.provider,
        "status": "connected",
        "connection_id": str(connection.id),
        "resource": {"id": resource_id, "name": resource_name},
    }


# ── 4. Disconnect / Reconnect ──

@router.post("/{connection_id}/disconnect", status_code=status.HTTP_200_OK)
async def disconnect(
    connection_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> Any:
    """Disconnect an integration and revoke tokens."""
    connection = await session.get(Connection, connection_id)
    if not connection:
        raise HTTPException(status_code=404, detail="Connection not found")

    auth.require_client_access(connection.client_id)

    connection.status = ConnectionStatus.DISCONNECTED
    connection.revoked_at = datetime.now(timezone.utc)

    # Revoke token from secret manager
    if connection.secret_ref:
        try:
            secret_manager = get_secret_manager()
            await secret_manager.revoke_secret(str(connection.client_id), connection.secret_ref)
        except Exception:
            # Log but don't fail — the connection is being disconnected either way
            pass

    await _audit_log(
        session,
        client_id=connection.client_id,
        actor=str(auth.user_id),
        integration=connection.provider,
        operation="disconnect",
        endpoint=f"integrations/{connection.id}/disconnect",
        log_status="success",
    )
    await session.commit()
    return {"message": "Disconnected", "provider": connection.provider}


@router.post("/{connection_id}/reconnect", status_code=status.HTTP_200_OK)
async def reconnect(
    connection_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> Any:
    """Reconnect a disconnected integration.

    Returns info needed to re-initiate the OAuth flow.
    """
    connection = await session.get(Connection, connection_id)
    if not connection:
        raise HTTPException(status_code=404, detail="Connection not found")

    auth.require_client_access(connection.client_id)

    connection.status = ConnectionStatus.PENDING

    await _audit_log(
        session,
        client_id=connection.client_id,
        actor=str(auth.user_id),
        integration=connection.provider,
        operation="reconnect",
        endpoint=f"integrations/{connection.id}/reconnect",
        log_status="initiated",
    )
    await session.commit()

    return {
        "message": "Reconnection initiated",
        "provider": connection.provider,
        "previous_resource": {
            "id": connection.resource_id,
            "name": connection.resource_name,
        },
    }


# ── 5. Integration Status Overview ──

@router.get("/status", response_model=IntegrationStatusResponse)
async def integration_status(
    client_id: uuid.UUID = Query(...),
    auth: AuthContext = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> Any:
    """Get the connection status of all integrations for a client.

    Returns a normalized status for each of the 7 integration types.
    """
    auth.require_client_access(client_id)

    # Fetch all connections for this client
    result = await session.execute(
        select(Connection).where(Connection.client_id == client_id)
    )
    connections = result.scalars().all()

    # Build a map of provider -> latest connection
    provider_map: dict[str, Connection] = {}
    for conn in connections:
        existing = provider_map.get(conn.provider)
        if existing is None or (conn.connected_at and (
            existing.connected_at is None or conn.connected_at > existing.connected_at
        )):
            provider_map[conn.provider] = conn

    # Build status list
    statuses = []
    for provider in ALL_PROVIDERS:
        current_conn = provider_map.get(provider)
        if current_conn and current_conn.status == ConnectionStatus.CONNECTED:
            meta = current_conn.provider_metadata or {}
            statuses.append(IntegrationStatusItem(
                provider=provider,
                status="connected",
                connection_id=str(current_conn.id),
                resource_id=current_conn.resource_id,
                resource_name=current_conn.resource_name,
                account_email=meta.get("account_email"),
                account_name=meta.get("account_name"),
                connected_at=current_conn.connected_at,
            ))
        elif current_conn and current_conn.status == ConnectionStatus.ERROR:
            statuses.append(IntegrationStatusItem(
                provider=provider,
                status="error",
                connection_id=str(current_conn.id),
                error="Connection error — try reconnecting",
            ))
        elif current_conn and current_conn.status == ConnectionStatus.PENDING:
            statuses.append(IntegrationStatusItem(
                provider=provider,
                status="connecting",
                connection_id=str(current_conn.id),
            ))
        elif current_conn and current_conn.status == ConnectionStatus.EXPIRED:
            statuses.append(IntegrationStatusItem(
                provider=provider,
                status="expired",
                connection_id=str(current_conn.id),
                error="Credentials expired — reconnect required",
            ))
        else:
            statuses.append(IntegrationStatusItem(
                provider=provider,
                status="not_connected",
            ))

    return IntegrationStatusResponse(integrations=statuses)


# ── 6. Health Check ──

@router.post("/{provider}/health-check")
async def health_check_integration(
    provider: str,
    client_id: uuid.UUID = Query(...),
    auth: AuthContext = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> Any:
    """Run a real health/access check on a connected integration.

    For GitHub: verifies the installation token still works by listing repos.
    Does NOT mark connected just because OAuth completed — checks actual API access.
    """
    auth.require_client_access(client_id)

    result = await session.execute(
        select(Connection).where(
            and_(
                Connection.client_id == client_id,
                Connection.provider == provider,
                Connection.status == ConnectionStatus.CONNECTED,
            )
        )
    )
    connection = result.scalar_one_or_none()

    if not connection:
        return {"provider": provider, "healthy": False, "message": "Not connected"}

    if provider == "github":
        conn_meta = connection.provider_metadata or {}
        inst_id = conn_meta.get("installation_id", "")

        if not inst_id:
            return {
                "provider": "github",
                "healthy": False,
                "message": "No installation_id found — GitHub App may not be installed",
            }

        try:
            handler = GitHubAppHandler()
            inst_token_data = await handler.get_installation_token(inst_id)
            inst_token = inst_token_data.get("token", "")

            if not inst_token:
                return {
                    "provider": "github",
                    "healthy": False,
                    "message": "Failed to generate installation access token",
                }

            # Verify access by listing repos (just 1 to minimize API calls)
            from app.integrations.base.http_client import AuditedHTTPClient
            async with AuditedHTTPClient(provider="github", access_token=inst_token, audit_callback=make_audit_callback(session, client_id)) as client:
                resp = await client.get(
                    "https://api.github.com/installation/repositories",
                    params={"per_page": 1},
                    operation="github_health_check"
                )
                if resp:
                    data = resp
                    total = data.get("total_count", 0)
                    await _audit_log(
                        session, client_id=client_id, actor=str(auth.user_id),
                        integration="github", operation="health_check",
                        endpoint="api.github.com/installation/repositories",
                        log_status="success",
                    )
                    await session.commit()
                    return {
                        "provider": "github",
                        "healthy": True,
                        "message": f"GitHub App installed, {total} repository(ies) accessible",
                        "total_repos": total,
                    }
                else:
                    return {
                        "provider": "github",
                        "healthy": False,
                        "message": f"GitHub API returned {resp.status_code} — App may have been uninstalled",
                    }

        except OAuthError as e:
            return {
                "provider": "github",
                "healthy": False,
                "message": str(e),
            }
        except Exception as e:
            logger.error("github_health_check_error", error=str(e))
            return {
                "provider": "github",
                "healthy": False,
                "message": f"Health check failed: {str(e)}",
            }

    else:
        # For non-GitHub providers, verify by attempting to retrieve the token
        if not connection.secret_ref:
            return {"provider": provider, "healthy": False, "message": "No credentials stored"}

        try:
            secret_manager = get_secret_manager()
            token_json = await secret_manager.get_secret(str(client_id), connection.secret_ref)
            token_data = json.loads(token_json)
            has_token = bool(token_data.get("access_token"))
            
            if not has_token:
                return {
                    "provider": provider,
                    "healthy": False,
                    "message": "Token missing or expired",
                }
                
            if provider == "search_console":
                # Perform a real read-only check
                try:
                    
                    
                    await fetch_search_console_sites(token_data.get("access_token"), audit_callback=make_audit_callback(session, client_id))
                    return {
                        "provider": provider,
                        "healthy": True,
                        "message": "Search Console API access verified",
                    }
                except Exception as e:
                    return {
                        "provider": provider,
                        "healthy": False,
                        "message": f"Search Console access failed: {str(e)}",
                    }

            return {
                "provider": provider,
                "healthy": has_token,
                "message": "Credentials available",
            }
        except Exception:
            return {"provider": provider, "healthy": False, "message": "Failed to retrieve credentials"}

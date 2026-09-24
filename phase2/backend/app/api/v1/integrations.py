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

from fastapi import APIRouter, Depends, HTTPException, Query, status, Body
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_

from app.auth.dependencies import AuthContext, get_current_user, apply_client_isolation
from app.core.config import get_settings
from app.db.session import get_db_session, async_session_factory
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
    SelectResourcePayload,
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

async def _require_active_engagement(session: AsyncSession, client_id: uuid.UUID) -> None:
    """Raise HTTP 403 if the client's engagement period has ended."""
    client = await session.get(Client, client_id)
    if client and client.engagement_end and datetime.now(timezone.utc) > client.engagement_end:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot establish new connections: the engagement period has ended."
        )

async def _enforce_engagement_expiry(session: AsyncSession, connection: Connection) -> bool:
    """Check if engagement ended. If so, mark expired and revoke secret."""
    if connection.status == ConnectionStatus.EXPIRED:
        return True
    
    client = await session.get(Client, connection.client_id)
    if client and client.engagement_end and datetime.now(timezone.utc) > client.engagement_end:
        connection.status = ConnectionStatus.EXPIRED
        connection.revoked_at = datetime.now(timezone.utc)
        if connection.secret_ref:
            try:
                secret_manager = get_secret_manager()
                await secret_manager.revoke_secret(str(connection.client_id), connection.secret_ref)
            except Exception:
                pass
        await session.commit()
        return True
    return False


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
        
        async with async_session_factory() as audit_session:
            await _audit_log(
                session=audit_session,
                client_id=cb_client_id,
                log_status=log_status,
                assessment_id=assessment_id,
                duration_ms=duration_ms,
                **kwargs
            )
            await audit_session.commit()
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
    await _require_active_engagement(session, client_id)

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
        token_data = await handler.exchange_code(
            code,
            audit_callback=make_audit_callback(session, client_id),
            client_id=str(client_id)
        )
        logger.info("google_oauth_token_exchange_success", provider=provider)

        access_token = token_data.get("access_token")
        if not access_token:
            raise OAuthError("No access_token in Google response")

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
            connection = existing
        else:
            meta = {}
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
    await _require_active_engagement(session, client_id)

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
        token_data = await handler.exchange_code(
            code,
            settings.meta_oauth_redirect_uri,
            audit_callback=make_audit_callback(session, client_id),
            client_id=str(client_id)
        )

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
            ).order_by(
                Connection.resource_id.isnot(None).desc(),
                Connection.connected_at.desc().nulls_last()
            )
        )
        all_existing = list(result.scalars().all())
        existing = all_existing[0] if all_existing else None
        
        # Deactivate stale duplicates
        for stale in all_existing[1:]:
            stale.status = ConnectionStatus.DISCONNECTED
        
        adapter = get_adapter("Meta")
        scopes = adapter.required_scopes if adapter else []
        
        if existing:
            existing.status = ConnectionStatus.CONNECTED
            existing.secret_ref = secret_ref
            existing.scopes = scopes
            existing.connected_at = datetime.now(timezone.utc)
            existing.expires_at = await _get_capped_expires_at(session, client_id, token_data.get("expires_in"))
            connection = existing
        else:
            meta = {}
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
    await _require_active_engagement(session, client_id)

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

    # GitHub App installation passes installation_id.
    # We do not use user OAuth codes.
    if not installation_id:
        params = urlencode({"error": "Missing installation_id", "provider": "github"})
        return RedirectResponse(f"{frontend_base}/integrations/callback?{params}")

    try:
        oauth_state = await validate_oauth_state(session, state, expected_provider="github")
        client_id = oauth_state.client_id
        user_id = oauth_state.user_id

        logger.info(
            "github_app_callback_received",
            has_installation_id=bool(installation_id),
            setup_action=setup_action,
        )

        handler = GitHubAppHandler()

        # Store installation_id securely
        secret_ref = await handler.store_tokens_securely(
            str(client_id),
            installation_id=installation_id,
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
            ).order_by(Connection.connected_at.desc().nulls_last())
        )
        existing = result.scalars().first()

        adapter = get_adapter("github")
        scopes = adapter.required_scopes if adapter else []

        # Build provider metadata with installation_id
        meta: dict[str, Any] = {}
        if installation_id:
            meta["installation_id"] = installation_id
        if setup_action:
            meta["setup_action"] = setup_action

        if existing:
            existing.status = ConnectionStatus.CONNECTED
            existing.secret_ref = secret_ref
            existing.scopes = scopes
            existing.connected_at = datetime.now(timezone.utc)
            existing.expires_at = await _get_capped_expires_at(session, client_id, None)
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
                expires_at=await _get_capped_expires_at(session, client_id, None),
                provider_metadata=meta if meta else None,
            )
            session.add(connection)

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
        apply_client_isolation(select(Connection).where(and_(Connection.provider == provider, Connection.status == ConnectionStatus.CONNECTED)), Connection, auth).where(Connection.client_id == client_id)
    )
    connection = result.scalar_one_or_none()

    if not connection or not connection.secret_ref:
        raise HTTPException(status_code=400, detail=f"No active connection for provider '{provider}'")

    if await _enforce_engagement_expiry(session, connection):
        raise HTTPException(status_code=403, detail="Access expired — engagement period ended")

    # Retrieve the access token from SecretManager
    secret_manager = get_secret_manager()
    try:
        token_json = await secret_manager.get_secret(str(client_id), connection.secret_ref)
        token_data = json.loads(token_json)
        access_token = token_data.get("access_token")
    except Exception as e:
        print("SECRET MANAGER EXCEPTION:", e)
        raise HTTPException(status_code=400, detail="Failed to retrieve credentials — try reconnecting")

    if not access_token and provider != "github":
        raise HTTPException(status_code=400, detail="No valid access token found — try reconnecting")

    try:
        resources: list[dict[str, Any]] = []

        if provider == "gtm":
            resources = await fetch_gtm_containers(access_token, audit_callback=make_audit_callback(session, client_id), client_id=str(client_id))
        elif provider == "ga4":
            resources = await fetch_ga4_properties(access_token, audit_callback=make_audit_callback(session, client_id), client_id=str(client_id))
        elif provider == "search_console":
            resources = await fetch_search_console_sites(access_token, audit_callback=make_audit_callback(session, client_id), client_id=str(client_id))
        elif provider == "meta":
            resources = await fetch_meta_businesses(access_token, audit_callback=make_audit_callback(session, client_id), client_id=str(client_id))
        elif provider == "github":
            # GitHub App: use installation_id to get installation-scoped token
            # then fetch repos accessible to the installation. No user token fallback.
            conn_meta = connection.provider_metadata or {}
            inst_id = conn_meta.get("installation_id") or token_data.get("installation_id", "")
            if not inst_id:
                raise HTTPException(
                    status_code=400,
                    detail="GitHub App installation_id not found — the GitHub App may not be installed. Please reconnect.",
                )
            try:
                gh_handler = GitHubAppHandler()
                inst_token_data = await gh_handler.get_installation_token(inst_id, audit_callback=make_audit_callback(session, client_id), client_id=str(client_id))
                inst_access_token = inst_token_data.get("token", "")
                if not inst_access_token:
                    raise HTTPException(
                        status_code=400,
                        detail="Failed to generate GitHub App installation token — check App configuration",
                    )
                resources = await fetch_github_repos(
                    inst_access_token,
                    installation_id=inst_id,
                    audit_callback=make_audit_callback(session, client_id),
                    client_id=str(client_id),
                )
            except HTTPException:
                raise
            except Exception as e:
                logger.warning("github_installation_token_error", error=str(e))
                raise HTTPException(
                    status_code=400,
                    detail=f"GitHub App access failed: {e}. Try reconnecting.",
                )
        elif provider == "google_ads":
            resources = await fetch_google_ads_customers(access_token, audit_callback=make_audit_callback(session, client_id), client_id=str(client_id))
        else:
            raise HTTPException(status_code=400, detail=f"Resource listing not supported for '{provider}'")

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
        apply_client_isolation(select(Connection).where(Connection.client_id == client_id), Connection, auth)
    )
    return result.scalars().all()


@router.post("/connect/{provider}/select-resource", status_code=status.HTTP_200_OK)
async def select_resource(
    provider: str,
    client_id: uuid.UUID = Query(...),
    resource_id: str = Query(...),
    resource_name: str = Query(""),
    connection_id: uuid.UUID | None = Query(None),
    payload: SelectResourcePayload | None = Body(None),
    auth: AuthContext = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> Any:
    """Select a resource for a connected integration.

    After OAuth callback creates a Connection, the user selects
    which resource (container, property, site) to use for assessment.

    Handles duplicate connections by picking the most recently connected
    row and deactivating stale duplicates.
    """
    auth.require_client_access(client_id)
    
    logger.info(f"Meta resource selection received: connection=?, business_id={resource_id}")

    if provider == "meta" and not connection_id:
        raise HTTPException(status_code=400, detail="connection_id is required for meta provider")

    # Find ALL connected rows for this client+provider, ordered by most recent
    query = select(Connection).where(
        and_(Connection.provider == provider, Connection.status == ConnectionStatus.CONNECTED)
    )
        
    result = await session.execute(
        apply_client_isolation(query, Connection, auth)
        .where(Connection.client_id == client_id)
        .order_by(Connection.connected_at.desc().nulls_last())
    )
    all_connections = list(result.scalars().all())

    if not all_connections:
        raise HTTPException(status_code=404, detail=f"No active connection for provider '{provider}'")

    if connection_id:
        target_conn = next((c for c in all_connections if c.id == connection_id), None)
        if not target_conn:
            raise HTTPException(status_code=404, detail=f"No active connection found with ID {connection_id}")
        connection = target_conn
    else:
        # Use the most recently connected row
        connection = all_connections[0]

    # Deactivate stale duplicates to prevent future divergence
    for stale in all_connections:
        if stale.id != connection.id:
            stale.status = ConnectionStatus.DISCONNECTED
            logger.info(
                "deactivated_duplicate_connection",
                provider=provider,
                stale_id=str(stale.id),
                kept_id=str(connection.id),
            )

    # For GitHub: validate the selected repo is within the installation scope
    if provider == "github":
        conn_meta = connection.provider_metadata or {}
        inst_id = conn_meta.get("installation_id", "")
        if not inst_id:
            raise HTTPException(
                status_code=400,
                detail="GitHub App installation_id not found — reconnect the GitHub App",
            )
        try:
            gh_handler = GitHubAppHandler()
            inst_token_data = await gh_handler.get_installation_token(inst_id)
            inst_access_token = inst_token_data.get("token", "")
            if inst_access_token:
                installation_repos = await fetch_github_repos(
                    inst_access_token,
                    installation_id=inst_id,
                    audit_callback=make_audit_callback(session, client_id),
                )
                accessible_repo_ids = {r["id"] for r in installation_repos}
                if resource_id not in accessible_repo_ids:
                    raise HTTPException(
                        status_code=403,
                        detail=f"Repository '{resource_id}' is not accessible to the installed GitHub App. "
                               f"Update the App installation to include this repository.",
                    )
        except HTTPException:
            raise
        except Exception as e:
            logger.warning("github_scope_validation_failed", error=str(e))
            # Don't block resource selection if scope check fails (e.g. network issue)
            # The assessment will catch access errors later.

    # Update with selected resource
    if provider == "google_ads":
        resource_id = resource_id.replace("-", "")
        
    connection.resource_id = resource_id
    connection.resource_name = resource_name
    
    if payload and payload.provider_metadata:
        current_meta = connection.provider_metadata or {}
        connection.provider_metadata = {**current_meta, **payload.provider_metadata}

    await session.commit()
    await session.refresh(connection)
    
    logger.info(f"Meta connection after save: resource_id={connection.resource_id}, resource_selected={bool(connection.resource_id)}")

    return {
        "provider": provider,
        "status": "connected",
        "connection_id": str(connection.id),
        "resource_id": connection.resource_id,
        "resource_name": connection.resource_name,
        "resource": {
            "id": connection.resource_id,
            "name": connection.resource_name,
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
    await _require_active_engagement(session, client_id)

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
    result = await session.execute(
        select(Connection).where(
            Connection.id == connection_id,
            Connection.client_id.in_([uuid.UUID(c) for c in auth.get_accessible_client_ids()])
        )
    )
    connection = result.scalar_one_or_none()
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
    result = await session.execute(
        select(Connection).where(
            Connection.id == connection_id,
            Connection.client_id.in_([uuid.UUID(c) for c in auth.get_accessible_client_ids()])
        )
    )
    connection = result.scalar_one_or_none()
    if not connection:
        raise HTTPException(status_code=404, detail="Connection not found")

    auth.require_client_access(connection.client_id)

    connection.status = ConnectionStatus.PENDING

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
        apply_client_isolation(select(Connection).where(Connection.client_id == client_id), Connection, auth)
    )
    connections = result.scalars().all()

    # Build a map of provider -> latest connection
    provider_map: dict[str, Connection] = {}
    for conn in connections:
        existing = provider_map.get(conn.provider)
        if existing is None:
            provider_map[conn.provider] = conn
        else:
            if conn.status == ConnectionStatus.CONNECTED and existing.status != ConnectionStatus.CONNECTED:
                provider_map[conn.provider] = conn
            elif conn.status == existing.status:
                if conn.connected_at and (existing.connected_at is None or conn.connected_at > existing.connected_at):
                    provider_map[conn.provider] = conn

    # Providers that don't require post-OAuth resource selection
    NO_RESOURCE_SELECTION_PROVIDERS = {"cmp"}

    # Build status list
    statuses = []
    for provider in ALL_PROVIDERS:
        current_conn = provider_map.get(provider)
        requires_res = provider not in NO_RESOURCE_SELECTION_PROVIDERS
        if current_conn and current_conn.status == ConnectionStatus.CONNECTED:
            meta = current_conn.provider_metadata or {}
            has_resource = bool(current_conn.resource_id)
            statuses.append(IntegrationStatusItem(
                provider=provider,
                status="connected",
                connection_id=str(current_conn.id),
                resource_id=current_conn.resource_id,
                resource_name=current_conn.resource_name,
                resource_selected=has_resource,
                requires_resource_selection=requires_res,
                connected_at=current_conn.connected_at,
                provider_metadata=meta,
            ))
        elif current_conn and current_conn.status == ConnectionStatus.ERROR:
            statuses.append(IntegrationStatusItem(
                provider=provider,
                status="error",
                connection_id=str(current_conn.id),
                requires_resource_selection=requires_res,
                error="Connection error — try reconnecting",
            ))
        elif current_conn and current_conn.status == ConnectionStatus.PENDING:
            statuses.append(IntegrationStatusItem(
                provider=provider,
                status="connecting",
                connection_id=str(current_conn.id),
                requires_resource_selection=requires_res,
            ))
        elif current_conn and current_conn.status == ConnectionStatus.EXPIRED:
            statuses.append(IntegrationStatusItem(
                provider=provider,
                status="expired",
                connection_id=str(current_conn.id),
                requires_resource_selection=requires_res,
                error="Credentials expired — reconnect required",
            ))
        else:
            statuses.append(IntegrationStatusItem(
                provider=provider,
                status="not_connected",
                requires_resource_selection=requires_res,
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
        apply_client_isolation(select(Connection).where(and_(Connection.provider == provider, Connection.status == ConnectionStatus.CONNECTED)), Connection, auth).where(Connection.client_id == client_id)
    )
    connection = result.scalar_one_or_none()

    if not connection:
        return {"provider": provider, "healthy": False, "message": "Not connected"}

    if await _enforce_engagement_expiry(session, connection):
        return {"provider": provider, "healthy": False, "message": "Access expired — engagement period ended"}

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
            inst_token_data = await handler.get_installation_token(
                inst_id,
                audit_callback=make_audit_callback(session, client_id),
                client_id=str(client_id)
            )
            inst_token = inst_token_data.get("token", "")

            if not inst_token:
                return {
                    "provider": "github",
                    "healthy": False,
                    "message": "Failed to generate installation access token",
                }

            # Verify access by listing repos (just 1 to minimize API calls)
            from app.integrations.base.http_client import AuditedHTTPClient
            async with AuditedHTTPClient(
                provider="github",
                access_token=inst_token,
                audit_callback=make_audit_callback(session, client_id),
                client_id=str(client_id),
                assessment_id=None,
                obligations_served=["APP.auth.github"]
            ) as client:
                resp = await client.get(
                    "https://api.github.com/installation/repositories",
                    params={"per_page": 1},
                    operation="github_health_check"
                )
                if resp:
                    data = resp
                    total = data.get("total_count", 0)
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
                    
                    
                    await fetch_search_console_sites(token_data.get("access_token"), audit_callback=make_audit_callback(session, client_id), client_id=str(client_id))
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

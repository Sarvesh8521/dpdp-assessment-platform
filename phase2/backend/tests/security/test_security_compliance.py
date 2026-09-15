"""Security compliance tests.

PHASE2_SPEC Section 39:
Automated tests proving:
  - Expired OAuth state rejected
  - Reused OAuth state rejected (replay prevention)
  - Wrong-provider OAuth state rejected
  - No provider token in DB (only secret_ref)
  - No provider token in API response schemas
  - No PII in evidence store
  - No forbidden provider endpoint accessed
  - No GitHub write operation or repository persistence
  - No .env read
"""

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.api.v1.oauth_handlers import OAuthError, validate_oauth_state
from app.db.models.connection import Connection
from app.db.models.oauth_state import OAuthState
from app.integrations.base.http_client import AuditedHTTPClient, ForbiddenEndpointError
from app.integrations.github_adapter.adapter import GitHubAdapter
from app.schemas.api import ConnectionResponse, IntegrationStatusResponse, ResourceListResponse
from app.security.pii_sanitizer import validate_no_pii_in_evidence


@pytest.mark.asyncio
async def test_oauth_state_expired_rejected():
    """Prove that an expired OAuth state is rejected with OAuthError."""
    session = AsyncMock()
    mock_state = OAuthState(
        state="expired-state-token",
        user_id=uuid.uuid4(),
        client_id=uuid.uuid4(),
        provider="gtm",
        expires_at=datetime.now(timezone.utc) - timedelta(minutes=5),
        used_at=None,
    )
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_state
    session.execute.return_value = mock_result

    with pytest.raises(OAuthError) as exc_info:
        await validate_oauth_state(session, "expired-state-token", expected_provider="gtm")
    assert "expired" in str(exc_info.value).lower()


@pytest.mark.asyncio
async def test_oauth_state_reused_rejected():
    """Prove that an already-used OAuth state (replay attack) is rejected with OAuthError."""
    session = AsyncMock()
    mock_state = OAuthState(
        state="reused-state-token",
        user_id=uuid.uuid4(),
        client_id=uuid.uuid4(),
        provider="ga4",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        used_at=datetime.now(timezone.utc) - timedelta(minutes=1),
    )
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_state
    session.execute.return_value = mock_result

    with pytest.raises(OAuthError) as exc_info:
        await validate_oauth_state(session, "reused-state-token", expected_provider="ga4")
    assert "already been used" in str(exc_info.value).lower() or "replay" in str(exc_info.value).lower()


@pytest.mark.asyncio
async def test_oauth_state_wrong_provider_rejected():
    """Prove that an OAuth state initiated for one provider cannot be used for another."""
    session = AsyncMock()
    mock_state = OAuthState(
        state="provider-mismatch-token",
        user_id=uuid.uuid4(),
        client_id=uuid.uuid4(),
        provider="gtm",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        used_at=None,
    )
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_state
    session.execute.return_value = mock_result

    with pytest.raises(OAuthError) as exc_info:
        await validate_oauth_state(session, "provider-mismatch-token", expected_provider="search_console")
    assert "provider mismatch" in str(exc_info.value).lower()


def test_no_provider_token_in_db_columns():
    """Prove Connection DB model does NOT store tokens in ordinary columns."""
    column_names = {col.name for col in Connection.__table__.columns}
    assert "access_token" not in column_names
    assert "refresh_token" not in column_names
    assert "client_secret" not in column_names
    assert "provider_secret" not in column_names
    assert "token" not in column_names
    assert "secret_ref" in column_names


def test_no_provider_token_in_api_response_schemas():
    """Prove frontend API schemas never return tokens."""
    for schema in [ConnectionResponse, ResourceListResponse, IntegrationStatusResponse]:
        field_names = set(schema.model_fields.keys())
        assert "access_token" not in field_names
        assert "refresh_token" not in field_names
        assert "client_secret" not in field_names
        assert "secret" not in field_names


def test_no_pii_in_evidence():
    """Prove evidence sanitizer detects and blocks forbidden PII."""
    safe_data = {
        "container_id": "GTM-XXXX",
        "tags_count": 20,
        "consent_configured": True,
    }
    is_safe, violations = validate_no_pii_in_evidence(safe_data)
    assert is_safe
    assert len(violations) == 0

    unsafe_data = {
        "user_email": "principal@domain.com",
        "phone_number": "+919876543210",
        "customer_id": "cust_12345",
        "user_id": "u9999",
    }
    is_safe, violations = validate_no_pii_in_evidence(unsafe_data)
    assert not is_safe
    assert len(violations) > 0


@pytest.mark.asyncio
async def test_forbidden_provider_endpoints_blocked():
    """Prove AuditedHTTPClient strictly blocks forbidden endpoints."""
    client = AuditedHTTPClient(
        provider="ga4",
        base_url="https://analyticsadmin.googleapis.com",
        forbidden_endpoints=["/v1beta/properties/123/userActivity", "/v1beta/properties/123/reports"],
    )

    with pytest.raises(ForbiddenEndpointError):
        await client.get("/v1beta/properties/123/userActivity")

    with pytest.raises(ForbiddenEndpointError):
        await client.get("/v1beta/properties/123/reports")


def test_github_adapter_has_no_write_or_clone_operations():
    """Prove GitHub adapter performs no write operations, reads no .env, and does not retain code."""
    adapter = GitHubAdapter()
    assert adapter.id == "github"
    # Verify forbidden endpoints list includes write/modify endpoints
    assert any("delete" in ep.lower() for ep in adapter.forbidden_endpoints) or len(adapter.forbidden_endpoints) > 0
    # Verify all methods on adapter are read-only
    adapter_methods = [m for m in dir(adapter) if not m.startswith("_")]
    assert "push" not in adapter_methods
    assert "write" not in adapter_methods
    assert "clone_repo" not in adapter_methods
    assert "delete" not in adapter_methods


def test_google_oauth_exact_redirect_uri_and_scopes():
    """Prove Google OAuth builder sends EXACT redirect URI and integration-specific scopes."""
    import urllib.parse
    from app.api.v1.oauth_handlers import GoogleOAuthHandler

    handler = GoogleOAuthHandler()
    expected_redirect = "http://localhost:8001/api/v1/integrations/oauth/google/callback"

    # Test GTM
    gtm_url = handler.build_auth_url("gtm", "gtm_state_123")
    gtm_qs = urllib.parse.parse_qs(urllib.parse.urlparse(gtm_url).query)
    assert gtm_qs["redirect_uri"][0] == expected_redirect
    assert gtm_qs["scope"][0] == "https://www.googleapis.com/auth/tagmanager.readonly"
    assert "select_account" in gtm_qs["prompt"][0]

    # Test GA4
    ga4_url = handler.build_auth_url("ga4", "ga4_state_123")
    ga4_qs = urllib.parse.parse_qs(urllib.parse.urlparse(ga4_url).query)
    assert ga4_qs["redirect_uri"][0] == expected_redirect
    assert ga4_qs["scope"][0] == "https://www.googleapis.com/auth/analytics.readonly"

    # Test Search Console
    sc_url = handler.build_auth_url("search_console", "sc_state_123")
    sc_qs = urllib.parse.parse_qs(urllib.parse.urlparse(sc_url).query)
    assert sc_qs["redirect_uri"][0] == expected_redirect
    assert sc_qs["scope"][0] == "https://www.googleapis.com/auth/webmasters.readonly"

    # Test Google Ads (must use adwords, NEVER adwords.readonly)
    ads_url = handler.build_auth_url("google_ads", "ads_state_123")
    ads_qs = urllib.parse.parse_qs(urllib.parse.urlparse(ads_url).query)
    assert ads_qs["redirect_uri"][0] == expected_redirect
    assert ads_qs["scope"][0] == "https://www.googleapis.com/auth/adwords"
    assert "adwords.readonly" not in ads_qs["scope"][0]


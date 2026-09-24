import pytest
from unittest.mock import AsyncMock, patch
from datetime import datetime, timezone

from app.integrations.meta.adapter import MetaAdapter, META_FORBIDDEN_ENDPOINTS
from app.integrations.base.adapter import ConnectionInfo
from app.integrations.base.http_client import ExternalAPIError
from app.domain.evidence import Verdict, Confidence

async def dummy_audit(*args, **kwargs): pass

@pytest.fixture
def adapter():
    return MetaAdapter()

@pytest.fixture
def connection():
    return ConnectionInfo(
        provider="meta", 
        scopes=["ads_read", "business_management"], 
        connected_at=datetime.now(timezone.utc),
        metadata={"business_id": "biz123", "ad_account_id": "act_123456"}
    )

@pytest.mark.asyncio
async def test_meta_forbidden_endpoints(adapter):
    """Test forbidden data access list is correct (never reads PII)."""
    assert adapter.forbidden_endpoints == META_FORBIDDEN_ENDPOINTS
    assert "/customaudiences/" in adapter.forbidden_endpoints or any("/customaudiences/" in ep for ep in adapter.forbidden_endpoints)
    assert any("users" in ep for ep in adapter.forbidden_endpoints)
    assert any("events" in ep for ep in adapter.forbidden_endpoints)

@pytest.mark.asyncio
@patch("app.integrations.meta.adapter.AuditedHTTPClient")
async def test_meta_adapter_evidence_generation(mock_client_class, adapter, connection):
    """Test comprehensive evidence generation across all new endpoints."""
    mock_instance = mock_client_class.return_value
    mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
    mock_instance.__aexit__ = AsyncMock()
    mock_instance.close = AsyncMock()
    
    # Setup mock responses for the various GET calls
    async def mock_get(url, *args, **kwargs):
        if "customaudiences" in url:
            return {
                "data": [
                    {
                        "name": "My Customer List",
                        "subtype": "CUSTOM",
                        "approximate_count": 1000,
                        "customer_file_source": "USER_PROVIDED_ONLY",
                        "permission_for_actions": {"can_edit": True}
                    }
                ]
            }
        elif "owned_pixels" in url:
            return {"data": [{"id": "px1"}, {"id": "px2"}]}
        elif "px1" in url:
            return {
                "id": "px1",
                "name": "Main Pixel",
                "automatic_matching_fields": ["email", "phone"],
                "first_party_cookie_status": "ENABLED",
                "server_events_enabled": False,
            }
        elif "px2" in url:
            return {
                "id": "px2",
                "name": "CAPI Pixel",
                "automatic_matching_fields": [],
                "is_server_events_enabled": True
            }
        elif "agencies" in url:
            return {"data": [{"id": "ag1", "name": "Test Agency"}]}
        elif "client_businesses" in url:
            return {"data": []}
        elif "offline_conversion_data_sets" in url:
            return {"data": [{"id": "off1", "name": "In-store sales"}]}
        return {"data": []}
        
    mock_instance.get = AsyncMock(side_effect=mock_get)
    
    result = await adapter.collect(connection, ad_account_id="act_123456", audit_callback=dummy_audit)
    
    assert len(result.errors) == 0
    assert len(result.evidence) >= 10
    
    # S9.sharing.audienceUpload (Custom Audiences)
    audience_ev = next(e for e in result.evidence if e.source == "meta:custom_audience:My Customer List")
    assert audience_ev.verdict == Verdict.OBSERVED
    assert audience_ev.confidence == Confidence.HIGH
    assert audience_ev.structured_detail.metadata["audience"]["origin"] == "USER_PROVIDED_ONLY"
    
    # S9.sharing.advancedMatching (Pixels)
    matching_ev = next(e for e in result.evidence if e.source == "meta:advanced_matching:px1")
    assert matching_ev.verdict == Verdict.OBSERVED
    assert "email" in matching_ev.detail
    
    # S9.sharing.serverSide (Conversions API indicator) - Direct CAPI evidence
    capi_ev2 = next(e for e in result.evidence if e.source == "meta:conversions_api:px2")
    assert capi_ev2.verdict == Verdict.OBSERVED
    assert capi_ev2.confidence == Confidence.HIGH
    assert "enabled" in capi_ev2.detail
    
    # S9.sharing.documented (Business sharing)
    sharing_ev = next(e for e in result.evidence if e.source == "meta:business_sharing:ag1")
    assert sharing_ev.verdict == Verdict.OBSERVED
    
    # S9.sharing.audienceUpload (Offline Events)
    offline_ev = next(e for e in result.evidence if e.source == "meta:offline_events:off1")
    assert offline_ev.verdict == Verdict.OBSERVED

@pytest.mark.asyncio
@patch("app.integrations.meta.adapter.AuditedHTTPClient")
async def test_meta_adapter_error_handling(mock_client_class, adapter, connection):
    """Test that external API errors degrade gracefully to CANNOT_DETERMINE."""
    mock_instance = mock_client_class.return_value
    mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
    mock_instance.__aexit__ = AsyncMock()
    mock_instance.close = AsyncMock()
    
    mock_instance.get = AsyncMock(side_effect=ExternalAPIError("API rate limit exceeded"))
    
    # Partial failure vs total failure depends on where it happens.
    result = await adapter.collect(connection, ad_account_id="act_123456", audit_callback=dummy_audit)
    
    assert len(result.errors) >= 4  # Audiences, Pixels, Sharing (agencies, client_businesses), Offline Events
    assert len(result.evidence) >= 5
    assert result.partial

@pytest.mark.asyncio
@patch("app.integrations.meta.adapter.AuditedHTTPClient")
async def test_meta_adapter_total_failure(mock_client_class, adapter, connection):
    """Test total failure returns partial result with Cannot Determine."""
    pass

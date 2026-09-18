import pytest
from unittest.mock import AsyncMock, patch
from datetime import datetime, timezone

from app.integrations.google_ads.adapter import GoogleAdsAdapter, GOOGLE_ADS_FORBIDDEN_ENDPOINTS
from app.integrations.base.adapter import ConnectionInfo
from app.integrations.base.http_client import ExternalAPIError
from app.domain.evidence import Verdict, Confidence

@pytest.fixture
def adapter():
    return GoogleAdsAdapter()

@pytest.fixture
def connection():
    return ConnectionInfo(
        provider="google_ads", 
        scopes=["https://www.googleapis.com/auth/adwords"], 
        connected_at=datetime.now(timezone.utc),
        metadata={"developer_token": "test-token"}
    )

@pytest.mark.asyncio
async def test_google_ads_forbidden_endpoints(adapter):
    """Test forbidden data access list is correct (never reads PII)."""
    assert adapter.forbidden_endpoints == GOOGLE_ADS_FORBIDDEN_ENDPOINTS
    assert "userListMembers" in adapter.forbidden_endpoints
    assert "offlineUserDataJobs" in adapter.forbidden_endpoints

@pytest.mark.asyncio
@patch("app.integrations.google_ads.adapter.AuditedHTTPClient")
async def test_google_ads_adapter_evidence_generation(mock_client_class, adapter, connection):
    """Test comprehensive evidence generation across all endpoints."""
    mock_instance = mock_client_class.return_value
    mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
    mock_instance.__aexit__ = AsyncMock()
    mock_instance.close = AsyncMock()
    
    # Setup mock responses for the searchStream POST calls
    async def mock_post(url, json_body, *args, **kwargs):
        query = json_body.get("query", "")
        if "user_list" in query:
            return [{
                "results": [
                    {
                        "userList": {
                            "name": "My CRM List",
                            "sizeForDisplay": "1000",
                            "type": "CRM_BASED",
                            "membershipLifeSpan": "30",
                            "sizeRangeForDisplay": "1000-5000"
                        }
                    }
                ]
            }]
        elif "conversion_action" in query:
            return [{
                "results": [
                    {
                        "conversionAction": {
                            "name": "Purchase",
                            "type": "ENHANCED_CONVERSIONS",
                            "category": "PURCHASE",
                            "status": "ENABLED"
                        }
                    }
                ]
            }]
        elif "customer.consent_setting" in query:
            return [{
                "results": [
                    {
                        "customer": {
                            "id": "12345",
                            "remarketingSetting": {
                                "googleGlobalSiteTag": "AW-123"
                            },
                            "consentSetting": {
                                "adUserData": "GRANTED",
                                "adPersonalization": "DENIED"
                            }
                        }
                    }
                ]
            }]
        elif "customer_client_link" in query:
            return [{
                "results": [
                    {
                        "customerClientLink": {
                            "status": "ACTIVE",
                            "clientCustomer": "customers/67890"
                        }
                    }
                ]
            }]
        return [{"results": []}]
        
    mock_instance.post = AsyncMock(side_effect=mock_post)
    
    result = await adapter.collect(connection, customer_id="12345")
    
    assert len(result.errors) == 0
    assert len(result.evidence) == 6 # 1 match, 1 conv, 2 consent, 1 link, 1 legacy upload
    
    for ev in result.evidence:
        assert ev.structured_detail.reason is not None and len(ev.structured_detail.reason) > 0, f"Evidence {ev.source} missing reason"
    
    # 1. Customer Match
    cm_ev = next(e for e in result.evidence if "customer_match" in e.source)
    assert cm_ev.verdict == Verdict.OBSERVED
    assert cm_ev.structured_detail.metadata["list_metadata"]["upload_date"] == "Cannot determine (not exposed safely via API)"
    assert cm_ev.structured_detail.metadata["list_metadata"]["name"] == "My CRM List"
    
    # 2. Enhanced Conversions
    conv_ev = next(e for e in result.evidence if "enhanced_conversions" in e.source)
    assert conv_ev.verdict == Verdict.OBSERVED
    assert conv_ev.obligation_id == "S9.sharing.enhancedConversions"
    assert "ENHANCED" in str(conv_ev.structured_detail.observed_state)
    
    # 3. Remarketing (Consent / Purpose)
    rem_ev = next(e for e in result.evidence if e.source == "google_ads:remarketing")
    assert rem_ev.verdict == Verdict.OBSERVED
    assert rem_ev.obligation_id == "S7.purposeLimitation"
    
    # 4. Consent Mode Diagnostics
    consent_ev = next(e for e in result.evidence if e.source == "google_ads:consent_mode")
    assert consent_ev.verdict == Verdict.OBSERVED
    assert "adUserData=GRANTED" in consent_ev.structured_detail.observed_state
    
    # 5. Data Manager Connections / Sub-account sharing
    link_ev = next(e for e in result.evidence if "client_links" in e.source)
    assert link_ev.verdict == Verdict.OBSERVED
    assert "customers/67890" in link_ev.detail
    
    # 6. Legacy Uploads
    legacy_ev = next(e for e in result.evidence if e.source == "google_ads:legacy_upload")
    assert legacy_ev.verdict == Verdict.CANNOT_DETERMINE
    assert "cannot be safely audited" in legacy_ev.detail

@pytest.mark.asyncio
@patch("app.integrations.google_ads.adapter.AuditedHTTPClient")
async def test_google_ads_adapter_no_consent_diagnostics(mock_client_class, adapter, connection):
    """Test when consent mode diagnostics are not exposed, CANNOT_DETERMINE is returned."""
    mock_instance = mock_client_class.return_value
    mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
    mock_instance.__aexit__ = AsyncMock()
    mock_instance.close = AsyncMock()
    
    # Setup mock responses for the searchStream POST calls
    async def mock_post(url, json_body, *args, **kwargs):
        query = json_body.get("query", "")
        if "customer.consent_setting" in query:
            return [{
                "results": [
                    {
                        "customer": {
                            "id": "12345",
                            # Missing consentSetting entirely
                        }
                    }
                ]
            }]
        return [{"results": []}]
        
    mock_instance.post = AsyncMock(side_effect=mock_post)
    
    result = await adapter.collect(connection, customer_id="12345")
    
    consent_ev = next(e for e in result.evidence if e.source == "google_ads:consent_mode")
    assert consent_ev.verdict == Verdict.CANNOT_DETERMINE
    assert consent_ev.confidence == Confidence.LOW
    assert consent_ev.structured_detail.reason is not None and len(consent_ev.structured_detail.reason) > 0

import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch
import json

from app.integrations.cmp.adapter import OneTrustAdapter, CookieYesAdapter, SecuritiAdapter
from app.integrations.base.adapter import ConnectionInfo
from app.domain.evidence import Verdict
from app.integrations.base.http_client import ExternalAPIError

@pytest.fixture
def connection():
    return ConnectionInfo(
        provider="onetrust", 
        scopes=["read"], 
        connected_at=datetime.now(timezone.utc),
        metadata={"access_token": "test-token"}
    )

@pytest.mark.asyncio
@patch("app.integrations.cmp.adapter.AuditedHTTPClient")
async def test_onetrust_health_check_success(mock_client_class, connection):
    mock_instance = mock_client_class.return_value
    mock_instance.get = AsyncMock(return_value={"content": []})
    mock_instance.close = AsyncMock()
    
    adapter = OneTrustAdapter()
    result = await adapter.health_check(connection)
    
    assert result.healthy is True
    mock_instance.get.assert_called_with("/api/v1/purposes", operation="health_check")

@pytest.mark.asyncio
@patch("app.integrations.cmp.adapter.AuditedHTTPClient")
async def test_onetrust_health_check_failure(mock_client_class, connection):
    mock_instance = mock_client_class.return_value
    mock_instance.get = AsyncMock(side_effect=ExternalAPIError(status_code=401, message="Unauthorized"))
    mock_instance.close = AsyncMock()
    
    adapter = OneTrustAdapter()
    result = await adapter.health_check(connection)
    
    assert result.healthy is False

@pytest.mark.asyncio
async def test_other_providers_graceful_health_check(connection):
    cookie_adapter = CookieYesAdapter()
    sec_adapter = SecuritiAdapter()
    
    c_res = await cookie_adapter.health_check(connection)
    s_res = await sec_adapter.health_check(connection)
    
    assert c_res.healthy is False
    assert s_res.healthy is False
    assert "unsupported for automated metadata extraction" in c_res.message
    assert "unsupported for automated metadata extraction" in s_res.message

@pytest.mark.asyncio
@patch("app.integrations.cmp.adapter.AuditedHTTPClient")
async def test_onetrust_collect_comprehensive(mock_client_class, connection):
    mock_instance = mock_client_class.return_value
    mock_instance.close = AsyncMock()
    
    async def mock_get(endpoint, **kwargs):
        if "purposes" in endpoint:
            return {"content": [{"id": "p1"}, {"id": "p2"}]}
        elif "cookie-categories" in endpoint:
            return {"content": [{"id": "c1"}]}
        elif "geolocation-rules" in endpoint:
            return {"content": [{"regions": ["EU", "US"], "countries": ["GB"]}]}
        elif "banner-configurations" in endpoint:
            return {"content": [{"id": "b1"}]}
        elif "schema" in endpoint:
            return {"fields": [{"name": "email"}, {"name": "consent_date"}]}
        elif "integrations" in endpoint:
            return {"content": [{"target": "salesforce"}]}
        raise ExternalAPIError(status_code=404, message="Not found")
        
    mock_instance.get.side_effect = mock_get
    
    adapter = OneTrustAdapter()
    result = await adapter.collect(connection)
    
    assert not result.errors
    
    obl_map = {}
    for ev in result.evidence:
        obl_map.setdefault(ev.obligation_id, []).append(ev)
        
    # Check that India exemption logic triggered NOT_OBSERVED because India was omitted
    geo_ev = obl_map["CMP.geolocation.india"][0]
    assert geo_ev.verdict == Verdict.NOT_OBSERVED
    assert "exempt India" in geo_ev.detail
    
    # Check purposes, banners, schema, integrations
    purposes_ev = obl_map["CMP.purposes.configured"]
    assert len(purposes_ev) == 4 # purposes, banners, schema, integrations
    
    assert any("2 consent purposes configured" in e.detail for e in purposes_ev)
    assert any("1 banner configurations" in e.detail for e in purposes_ev)
    assert any("schema contains 2" in e.detail for e in purposes_ev)
    assert any("1 active integration" in e.detail for e in purposes_ev)
    
    categories_ev = obl_map["CMP.categories.complete"][0]
    assert "1 cookie categories" in categories_ev.detail

@pytest.mark.asyncio
async def test_other_providers_collect(connection):
    cookie_adapter = CookieYesAdapter()
    result = await cookie_adapter.collect(connection)
    assert len(result.evidence) == 1
    assert result.evidence[0].verdict == Verdict.CANNOT_DETERMINE
    assert "CookieYes API is not currently supported" in result.evidence[0].detail

    sec_adapter = SecuritiAdapter()
    s_result = await sec_adapter.collect(connection)
    assert len(s_result.evidence) == 1
    assert s_result.evidence[0].verdict == Verdict.CANNOT_DETERMINE
    assert "Securiti API is not currently supported" in s_result.evidence[0].detail

@pytest.mark.asyncio
async def test_forbidden_endpoints_rejection():
    from app.integrations.base.http_client import AuditedHTTPClient
    adapter = OneTrustAdapter()
    
    # Assert critical endpoints are in the forbidden list
    assert "consent-receipts$" in adapter.forbidden_endpoints
    assert "consent-records" in adapter.forbidden_endpoints
    
    client = AuditedHTTPClient(
        provider="onetrust",
        base_url="https://api.onetrust.com",
        access_token="test",
        forbidden_endpoints=adapter.forbidden_endpoints,
        obligations_served=[]
    )
    
    from app.integrations.base.http_client import ForbiddenEndpointError
    
    try:
        # The AuditedHTTPClient should raise a ForbiddenEndpointError when attempting to access a forbidden endpoint
        with pytest.raises(ForbiddenEndpointError, match="Attempted to call forbidden endpoint"):
            await client.get("/api/v1/consent-receipts", operation="read_receipts")
            
        with pytest.raises(ForbiddenEndpointError, match="Attempted to call forbidden endpoint"):
            await client.get("/api/v1/consent-records?user=123", operation="read_records")
            
        with pytest.raises(ForbiddenEndpointError, match="Attempted to call forbidden endpoint"):
            await client.get("/api/individual-consent/456", operation="read_individual")
            
        # Prove schema IS allowed
        schema_called = False
        async def mock_execute(*args, **kwargs):
            nonlocal schema_called
            schema_called = True
            from unittest.mock import MagicMock
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {}
            return mock_resp
            
        with patch.object(client, '_execute_with_retry', new=mock_execute):
            await client.get("/api/v1/consent-receipts/schema", operation="read_schema")
            assert schema_called is True

    finally:
        await client.close()

from unittest.mock import AsyncMock
import pytest

async def dummy_audit(*args, **kwargs): pass

from datetime import datetime, timezone
import uuid
from app.integrations.cmp.adapter import CookieYesAdapter, SecuritiAdapter, OneTrustAdapter
from app.integrations.base.adapter import ConnectionInfo
from app.domain.evidence import Verdict


@pytest.fixture
def dummy_connection():
    return ConnectionInfo(
        provider="dummy",
        scopes=["read"],
        connected_at=datetime.now(timezone.utc)
    )


@pytest.mark.asyncio
async def test_cookieyes_adapter_reports_cannot_determine(dummy_connection):
    adapter = CookieYesAdapter()
    
    # Test health check is false
    health = await adapter.health_check(dummy_connection, audit_callback=AsyncMock())
    assert health.healthy is False
    
    # Test collect explicitly reports Cannot determine
    result = await adapter.collect(dummy_connection, audit_callback=AsyncMock())
    
    assert len(result.evidence) == 1
    evidence = result.evidence[0]
    
    assert evidence.verdict == Verdict.CANNOT_DETERMINE
    assert "CookieYes API is not currently supported" in evidence.detail
    assert "CookieYes" in evidence.detail
    
    assert evidence.structured_detail is not None
    assert evidence.structured_detail.integration == "cookieyes"


@pytest.mark.asyncio
async def test_securiti_adapter_reports_cannot_determine(dummy_connection):
    adapter = SecuritiAdapter()
    
    # Test health check is false
    health = await adapter.health_check(dummy_connection, audit_callback=AsyncMock())
    assert health.healthy is False
    
    # Test collect explicitly reports Cannot determine
    result = await adapter.collect(dummy_connection, audit_callback=AsyncMock())
    
    assert len(result.evidence) == 1
    evidence = result.evidence[0]
    
    assert evidence.verdict == Verdict.CANNOT_DETERMINE
    assert "Securiti API is not currently supported" in evidence.detail
    assert "Securiti" in evidence.detail

    assert evidence.structured_detail is not None
    assert evidence.structured_detail.integration == "securiti"


@pytest.mark.asyncio
async def test_onetrust_forbidden_endpoints(dummy_connection):
    adapter = OneTrustAdapter()
    from app.integrations.base.http_client import AuditedHTTPClient, ForbiddenEndpointError
    
    client = AuditedHTTPClient(audit_callback=AsyncMock(), 
        provider=adapter.provider,
        base_url="https://api.onetrust.com",
        access_token="fake",
        forbidden_endpoints=adapter.forbidden_endpoints,
        client_id=str(uuid.uuid4()),
        assessment_id=str(uuid.uuid4()),
        obligations_served=["test"]
    )
    
    # The adapter should forbid these endpoints
    for endpoint in ["/api/v1/consent-receipts", "/api/v1/consentRecords", "/api/individual-consent"]:
        with pytest.raises(ForbiddenEndpointError):
            await client.get(endpoint)


@pytest.mark.asyncio
async def test_onetrust_adapter_collects_metadata_only(dummy_connection):
    from unittest.mock import patch, AsyncMock
    adapter = OneTrustAdapter()
    
    # Mock the HTTP client's get method
    with patch("app.integrations.base.http_client.AuditedHTTPClient.get") as mock_get:
        async def mock_api_response(endpoint, **kwargs):
            if "/purposes" in endpoint:
                return {"content": [{"id": "1", "name": "Strictly Necessary"}]}
            if "/cookie-categories" in endpoint:
                return {"content": [{"id": "C0001", "name": "Essential"}]}
            if "/geolocation-rules" in endpoint:
                # India included
                return {"content": [{"regions": ["Asia", "IN"]}]}
            if "/banner-configurations" in endpoint:
                return {"content": [{"id": "B1"}]}
            if "/consent-receipts/schema" in endpoint:
                return {"fields": [{"name": "receiptId"}]}
            if "/integrations" in endpoint:
                return {"content": [{"id": "I1"}]}
            return {}

        mock_get.side_effect = mock_api_response
        
        result = await adapter.collect(dummy_connection, audit_callback=AsyncMock())
        
        # Verify no errors
        assert not result.errors
        
        # Verify evidence length (Purposes, Categories, Geo rules, Banner, Schema, Integrations)
        assert len(result.evidence) == 6
        
        # Verify specific evidence properties
        purposes = next((e for e in result.evidence if "purposes" in e.source), None)
        assert purposes is not None
        assert purposes.verdict == Verdict.OBSERVED
        assert purposes.obligation_id == "CMP.purposes.configured"
        
        geo = next((e for e in result.evidence if "geolocation" in e.source), None)
        assert geo is not None
        assert geo.verdict == Verdict.OBSERVED # Because India is included
        assert geo.obligation_id == "CMP.geolocation.india"

        # Verify no PII endpoints were queried by checking mock_get call args
        for call in mock_get.call_args_list:
            endpoint = call[0][0]
            assert "receipts" not in endpoint or "schema" in endpoint # Only schema is allowed
            assert "user" not in endpoint
            assert "individual" not in endpoint

@pytest.mark.asyncio
async def test_onetrust_graceful_degradation(dummy_connection):
    from unittest.mock import patch
    from app.integrations.base.http_client import ExternalAPIError
    adapter = OneTrustAdapter()
    
    with patch("app.integrations.base.http_client.AuditedHTTPClient.get") as mock_get:
        # Simulate API failure for all endpoints
        mock_get.side_effect = ExternalAPIError("API down")
        
        result = await adapter.collect(dummy_connection, audit_callback=AsyncMock())
        
        # Only the purposes check has error logged (the rest pass gracefully without error strings)
        assert len(result.errors) == 1
        assert "Purposes query failed" in result.errors[0]
        
        # It returns empty evidence if everything fails
        assert len(result.evidence) == 0

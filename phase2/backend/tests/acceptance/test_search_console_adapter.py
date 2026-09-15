import pytest
from unittest.mock import AsyncMock, patch
from datetime import datetime, timezone

from app.integrations.search_console.adapter import SearchConsoleAdapter, SC_FORBIDDEN_ENDPOINTS
from app.integrations.base.adapter import ConnectionInfo
from app.integrations.base.http_client import ExternalAPIError
from app.domain.evidence import Verdict, Confidence

@pytest.fixture
def adapter():
    return SearchConsoleAdapter()

@pytest.fixture
def connection():
    return ConnectionInfo(
        provider="search_console", 
        scopes=["https://www.googleapis.com/auth/webmasters.readonly"], 
        connected_at=datetime.now(timezone.utc),
        metadata={"resource_id": "https://example.com/"}
    )

@pytest.mark.asyncio
async def test_sc_forbidden_endpoints(adapter):
    """Test forbidden data access list is correct (never reads search queries)."""
    assert adapter.forbidden_endpoints == SC_FORBIDDEN_ENDPOINTS
    assert "query" in adapter.forbidden_endpoints
    assert "searchAnalytics" in adapter.forbidden_endpoints

@pytest.mark.asyncio
@patch("app.integrations.search_console.adapter.AuditedHTTPClient")
async def test_sc_adapter_evidence_generation(mock_client_class, adapter, connection):
    """Test comprehensive evidence generation for properties and sitemaps."""
    mock_instance = mock_client_class.return_value
    mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
    mock_instance.__aexit__ = AsyncMock()
    mock_instance.close = AsyncMock()
    
    # Setup mock responses for the various GET calls
    async def mock_get(url, *args, **kwargs):
        if url.endswith("/sites"):
            return {
                "siteEntry": [
                    {
                        "siteUrl": "https://example.com/",
                        "permissionLevel": "siteOwner"
                    },
                    {
                        "siteUrl": "https://other.com/",
                        "permissionLevel": "siteRestrictedUser"
                    }
                ]
            }
        elif "sitemaps" in url:
            return {
                "sitemap": [
                    {
                        "path": "https://example.com/sitemap.xml",
                        "type": "sitemap",
                        "warnings": 0,
                        "errors": 0,
                        "isPending": False,
                        "isSitemapsIndex": False
                    }
                ]
            }
        return {}
        
    mock_instance.get = AsyncMock(side_effect=mock_get)
    
    # We pass declared_properties to test the property scope validation
    result = await adapter.collect(connection, declared_properties=["https://example.com/"])
    
    assert len(result.errors) == 0
    assert len(result.evidence) == 2
    
    # S10.scope.properties (Verified Sites)
    sites_ev = next(e for e in result.evidence if e.source == "search_console:verified_sites")
    # One property is undeclared (other.com), so it should be NOT_OBSERVED
    assert sites_ev.verdict == Verdict.NOT_OBSERVED
    assert sites_ev.confidence == Confidence.HIGH
    assert "other.com" in sites_ev.detail
    assert sites_ev.raw_metadata["total_verified"] == 2
    
    # S10.scope.properties (Sitemaps)
    sitemaps_ev = next(e for e in result.evidence if e.source == "search_console:sitemaps")
    assert sitemaps_ev.verdict == Verdict.OBSERVED
    assert sitemaps_ev.confidence == Confidence.HIGH
    assert len(sitemaps_ev.raw_metadata["sitemaps"]) == 1
    assert sitemaps_ev.raw_metadata["sitemaps"][0]["path"] == "https://example.com/sitemap.xml"

@pytest.mark.asyncio
@patch("app.integrations.search_console.adapter.AuditedHTTPClient")
async def test_sc_adapter_error_handling(mock_client_class, adapter, connection):
    """Test that external API errors degrade gracefully to empty result with errors."""
    mock_instance = mock_client_class.return_value
    mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
    mock_instance.__aexit__ = AsyncMock()
    mock_instance.close = AsyncMock()
    
    mock_instance.get = AsyncMock(side_effect=ExternalAPIError("API rate limit exceeded"))
    
    result = await adapter.collect(connection)
    
    assert len(result.errors) == 2  # Property discovery, Sitemap collection
    assert len(result.evidence) == 0
    assert not result.partial

@pytest.mark.asyncio
@patch("app.integrations.search_console.adapter.AuditedHTTPClient")
async def test_sc_adapter_total_failure(mock_client_class, adapter, connection):
    """Test total failure returns CANNOT_DETERMINE."""
    # Simulate a non-API crash
    mock_instance = mock_client_class.return_value
    mock_instance.get = AsyncMock(side_effect=ValueError("Unexpected internal crash"))
    mock_instance.close = AsyncMock()
    
    result = await adapter.collect(connection)
    
    assert len(result.errors) == 1
    assert result.partial is True
    assert len(result.evidence) == 1
    assert result.evidence[0].verdict == Verdict.CANNOT_DETERMINE
    assert "Unexpected internal crash" in result.evidence[0].detail

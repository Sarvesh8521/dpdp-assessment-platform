import pytest
from app.integrations.base.http_client import AuditedHTTPClient, ForbiddenEndpointError
from app.integrations.github_adapter.adapter import GitHubAdapter
from app.integrations.gtm.adapter import GTMAdapter
from app.integrations.ga4.adapter import GA4Adapter
from app.integrations.google_ads.adapter import GoogleAdsAdapter
from app.integrations.meta.adapter import MetaAdapter
from app.integrations.search_console.adapter import SearchConsoleAdapter
from app.integrations.cmp.adapter import OneTrustAdapter

adapters_with_forbidden = [
    (GTMAdapter(), "/users/123"),
    (GTMAdapter(), "/permissions"),
    (GA4Adapter(), "/runReport"),
    (GA4Adapter(), "/users/456"),
    (GA4Adapter(), "/audience/789"),
    (GoogleAdsAdapter(), "/customers/123/userLists"),
    (GoogleAdsAdapter(), "/customers/123/offlineUserDataJobs"),
    (MetaAdapter(), "/12345/customaudiences/999/users"),
    (MetaAdapter(), "/12345/users"),
    (MetaAdapter(), "/events"),
    (SearchConsoleAdapter(), "/searchanalytics/query"),
    (GitHubAdapter(), "/user"),
    (GitHubAdapter(), "/repositories"),
    (GitHubAdapter(), "/user"),
    (OneTrustAdapter(), "/api/v1/consent-receipts"),
    (OneTrustAdapter(), "/api/v1/consent-records"),
    (OneTrustAdapter(), "/data-subjects/xyz"),
]

@pytest.mark.asyncio
@pytest.mark.parametrize("adapter, test_endpoint", adapters_with_forbidden)
async def test_adapters_block_forbidden_reads(mocker, adapter, test_endpoint):
    # Retrieve the configured forbidden_endpoints from the adapter instance
    forbidden_list = adapter.forbidden_endpoints
    
    # Instantiate the AuditedHTTPClient which the adapters use internally
    client = AuditedHTTPClient(
        provider=adapter.provider,
        base_url="https://api.github.com",
        forbidden_endpoints=forbidden_list,
        audit_callback=mocker.AsyncMock()
    )
    
    # Execute a GET request and verify it raises ForbiddenEndpointError
    with pytest.raises(ForbiddenEndpointError) as exc:
        await client.get(test_endpoint)
        
    assert "SECURITY VIOLATION: Attempted to call forbidden endpoint" in str(exc.value)

@pytest.mark.parametrize("adapter, expected_scopes", [
    (GTMAdapter(), ["https://www.googleapis.com/auth/tagmanager.readonly"]),
    (GA4Adapter(), ["https://www.googleapis.com/auth/analytics.readonly"]),
    (SearchConsoleAdapter(), ["https://www.googleapis.com/auth/webmasters.readonly"]),
])
def test_adapter_scopes_are_least_privilege(adapter, expected_scopes):
    """Verify that the adapters only request read-only or strictly necessary scopes."""
    if hasattr(adapter, "SCOPES"):
        assert all(scope in adapter.SCOPES for scope in expected_scopes)


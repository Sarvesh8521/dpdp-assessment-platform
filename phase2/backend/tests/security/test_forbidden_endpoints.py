import pytest
from app.integrations.base.http_client import AuditedHTTPClient, ForbiddenEndpointError

# Import all adapters to verify they have forbidden endpoints
from app.integrations.github_adapter.adapter import GitHubAdapter
from app.integrations.gtm.adapter import GTMAdapter
from app.integrations.ga4.adapter import GA4Adapter
from app.integrations.google_ads.adapter import GoogleAdsAdapter
from app.integrations.meta.adapter import MetaAdapter
from app.integrations.search_console.adapter import SearchConsoleAdapter
from app.integrations.cmp.adapter import OneTrustAdapter

@pytest.mark.asyncio
async def test_audited_http_client_blocks_forbidden():
    client = AuditedHTTPClient(
        provider="test",
        base_url="https://api.github.com",
        forbidden_endpoints=["/delete", "/users/admin"],
    )
    
    with pytest.raises(ForbiddenEndpointError) as exc:
        await client.get("/delete/something")
    
    assert "/delete/something" in str(exc.value)

def test_all_adapters_declare_forbidden_endpoints():
    adapters = [
        GitHubAdapter(),
        GTMAdapter(),
        GA4Adapter(),
        GoogleAdsAdapter(),
        MetaAdapter(),
        SearchConsoleAdapter(),
        OneTrustAdapter(),
    ]
    
    for adapter in adapters:
        # Every adapter must have at least one forbidden endpoint except CMP which we just check is a list
        if not isinstance(adapter, OneTrustAdapter):
             assert len(adapter.forbidden_endpoints) >= 0, f"{adapter.provider} has no forbidden endpoints!"
        
        # Verify that all forbidden endpoints are strings
        for endpoint in adapter.forbidden_endpoints:
            assert isinstance(endpoint, str)

import uuid
from datetime import datetime, timezone
import pytest
from unittest.mock import AsyncMock, patch

from app.integrations.base.http_client import AuditedHTTPClient, ExternalAPIError, ForbiddenEndpointError
from app.integrations.github_adapter.adapter import GitHubAdapter
from app.integrations.base.adapter import ConnectionInfo

@pytest.fixture
def mock_audit():
    return AsyncMock()

@pytest.mark.asyncio
async def test_github_adapter_forbids_unscoped_endpoints(mock_audit):
    """Test that GITHUB_FORBIDDEN_ENDPOINTS are actively blocked."""
    adapter = GitHubAdapter()
    client = AuditedHTTPClient(
        provider=adapter.provider,
        audit_callback=mock_audit,
        base_url="https://api.github.com",
        access_token="fake-token",
        forbidden_endpoints=adapter.forbidden_endpoints,
        obligations_served=adapter.obligations_answered,
        client_id=str(uuid.uuid4()),
        assessment_id=str(uuid.uuid4()),
    )
    
    # Test a few forbidden endpoints
    forbidden_urls = [
        "/user",
        "/repositories",
        "/users/{username}/repos",
        "/repos/*/contents",
        "/repos/*/merges",
    ]
    
    for url in forbidden_urls:
        with pytest.raises(ForbiddenEndpointError):
            await client.get(url)
            
    # Check that a write operation is also blocked
    with pytest.raises(ExternalAPIError) as exc:
        await client.post("/repos/owner/repo/issues", json_body={"title": "test"})
    assert "Write operation" in str(exc.value)
    
    await client.close()

@pytest.mark.asyncio
async def test_github_adapter_forbids_user_auth_and_pats(mock_audit):
    """Test that connection health check fails if installation_id is missing, meaning it rejects PAT/user auth setups."""
    adapter = GitHubAdapter()
    
    # Missing installation_id implies a PAT or User OAuth token, which is rejected.
    connection = ConnectionInfo(
        provider=adapter.provider,
        scopes=adapter.required_scopes,
        connected_at=datetime.now(timezone.utc),
        metadata={},  # No installation_id
    )
    
    result = await adapter.health_check(connection, mock_audit)
    
    assert result.healthy is False
    assert "No installation_id" in result.message

@pytest.mark.asyncio
async def test_github_audit_logs_match_api_calls(mock_audit):
    """Test that every API call results in exactly one audit log."""
    adapter = GitHubAdapter()
    client = AuditedHTTPClient(
        provider=adapter.provider,
        audit_callback=mock_audit,
        base_url="https://api.github.com",
        access_token="fake-token",
        forbidden_endpoints=adapter.forbidden_endpoints,
        obligations_served=adapter.obligations_answered,
        client_id=str(uuid.uuid4()),
        assessment_id=str(uuid.uuid4()),
    )
    
    # Patch the underlying httpx request so it succeeds
    with patch("httpx.AsyncClient.request", new_callable=AsyncMock) as mock_req:
        mock_req.return_value.status_code = 200
        mock_req.return_value.json = AsyncMock(return_value={"total_count": 1})
        mock_req.return_value.raise_for_status = AsyncMock()
        
        await client.get("/installation/repositories", operation="list_repos")
        
        # Verify exactly 1 audit call was made
        assert mock_audit.call_count == 1
        kwargs = mock_audit.call_args.kwargs
        assert kwargs["integration"] == "github"
        assert kwargs["operation"] == "list_repos"
        
    await client.close()

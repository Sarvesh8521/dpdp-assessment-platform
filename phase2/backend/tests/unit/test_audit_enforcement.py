import pytest

async def dummy_audit(*args, **kwargs): pass

from unittest.mock import AsyncMock, patch, MagicMock
from httpx import Response
import uuid

from app.integrations.registry import get_adapter
from app.integrations.base.adapter import ConnectionInfo

# List of all adapters that we need to test
ALL_ADAPTERS = [
    "gtm",
    "ga4",
    "google_ads",
    "meta",
    "search_console",
    "github",
    "onetrust", # CMP example
]

@pytest.mark.asyncio
@pytest.mark.parametrize("provider_id", ALL_ADAPTERS)
async def test_every_adapter_enforces_audit_logging(provider_id: str):
    """
    Test that every adapter enforces the audit log contract.
    We mock httpx.AsyncClient.request, call adapter.collect(audit_callback=dummy_audit), and verify
    that every HTTP request results in exactly one audit_callback invocation.
    """
    adapter = get_adapter(provider_id)
    if not adapter:
        pytest.skip(f"Adapter {provider_id} not registered")

    mock_audit_callback = AsyncMock()

    # We mock the underlying httpx.AsyncClient.send (the transport used by both request and stream)
    with patch("httpx.AsyncClient.send") as mock_httpx_send:
        
        # Setup a generic mock response
        mock_response = Response(200, json={"data": [], "account": [], "content": [], "repositories": []})
        mock_response._request = MagicMock()
        mock_httpx_send.return_value = mock_response

        # Mock oauth handlers where necessary (GitHub gets token, etc)
        with patch("app.api.v1.oauth_handlers.GitHubAppHandler.get_installation_token", new_callable=AsyncMock) as mock_gh_token:
            mock_gh_token.return_value = {"token": "mock-token"}
            
            # 1. Test connect()
            try:
                conn_info = await adapter.connect(
                    credentials={"access_token": "test_token", "installation_id": "123"},
                    audit_callback=mock_audit_callback
                )
            except Exception:
                # Some adapters might raise on connect without specific credentials. We just want to check calls.
                conn_info = ConnectionInfo(provider=provider_id)
            
            # Record calls made during connect
            connect_httpx_calls = mock_httpx_send.call_count
            connect_audit_calls = mock_audit_callback.call_count
            assert connect_httpx_calls == connect_audit_calls, f"connect() failed audit contract for {provider_id}"

            # 2. Test health_check()
            mock_httpx_send.reset_mock()
            mock_audit_callback.reset_mock()
            try:
                await adapter.health_check(conn_info, audit_callback=mock_audit_callback, client_id=str(uuid.uuid4()), assessment_id=str(uuid.uuid4()))
            except Exception:
                pass
            
            hc_httpx_calls = mock_httpx_send.call_count
            hc_audit_calls = mock_audit_callback.call_count
            assert hc_httpx_calls == hc_audit_calls, f"health_check() failed audit contract for {provider_id}"

            # 3. Test collect()
            mock_httpx_send.reset_mock()
            mock_audit_callback.reset_mock()
            try:
                await adapter.collect(
                    conn_info, audit_callback=mock_audit_callback,
                    client_id=str(uuid.uuid4()),
                    assessment_id=str(uuid.uuid4()),
                    access_token="test_token",
                    repositories=["mock/repo"]
                )
            except Exception:
                pass
            
            collect_httpx_calls = mock_httpx_send.call_count
            collect_audit_calls = mock_audit_callback.call_count
            assert collect_httpx_calls == collect_audit_calls, f"collect() failed audit contract for {provider_id}"

@pytest.mark.asyncio
async def test_missing_audit_context_blocks_request():
    """Test that missing audit context immediately raises an error and blocks the network request."""
    from app.integrations.base.http_client import AuditedHTTPClient, ExternalAPIError
    
    mock_audit_callback = AsyncMock()
    with patch("httpx.AsyncClient.request") as mock_request:
        
        # 1. Missing audit_callback
        with pytest.raises(ExternalAPIError, match="SECURITY VIOLATION: Missing audit callback"):
            async with AuditedHTTPClient(provider="test", audit_callback=None) as client:
                await client.get("/test", operation="test")
                
        # 2. Missing client_id
        with pytest.raises(ExternalAPIError, match="SECURITY VIOLATION: Missing audit context \\(client_id\\)"):
            async with AuditedHTTPClient(
                provider="test", audit_callback=mock_audit_callback,
                client_id=None, assessment_id="test-123", obligations_served=["test"]
            ) as client:
                await client.get("/test", operation="test")
                
        # 3. Missing assessment_id
        with pytest.raises(ExternalAPIError, match="SECURITY VIOLATION: Missing audit context \\(assessment_id\\)"):
            async with AuditedHTTPClient(
                provider="test", audit_callback=mock_audit_callback,
                client_id="client-123", assessment_id=None, obligations_served=["test"]
            ) as client:
                await client.get("/test", operation="test")
                
        # 4. Missing obligations_served
        with pytest.raises(ExternalAPIError, match="SECURITY VIOLATION: Missing audit context \\(obligations_served\\)"):
            async with AuditedHTTPClient(
                provider="test", audit_callback=mock_audit_callback,
                client_id="client-123", assessment_id="test-123", obligations_served=None
            ) as client:
                await client.get("/test", operation="test")
                
        # Verify no network requests were made
        assert mock_request.call_count == 0

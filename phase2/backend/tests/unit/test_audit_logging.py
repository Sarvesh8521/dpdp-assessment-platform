import pytest
import uuid
import json
from unittest.mock import AsyncMock, patch, MagicMock

from httpx import Response
from app.api.v1.integrations import make_audit_callback
from app.integrations.base.http_client import AuditedHTTPClient, ExternalAPIError
from app.db.models.audit_log import AuditLog

@pytest.mark.asyncio
@patch("app.api.v1.integrations.async_session_factory")
async def test_audit_callback_commits_independently(mock_async_session_factory):
    # Setup mock session
    mock_session = AsyncMock()
    # async with returns the mock session
    mock_async_session_factory.return_value.__aenter__.return_value = mock_session
    
    client_id = uuid.uuid4()
    
    # Create the callback
    callback = make_audit_callback(session=AsyncMock(), client_id=client_id)
    
    # Simulate AuditedHTTPClient calling the callback on a successful API request
    await callback(
        status="success",
        actor="adapter:test",
        integration="test",
        operation="fetch_data",
        endpoint="https://api.test.com/data",
        status_code=200,
        duration_ms=150,
        obligations_served=["O1.test"]
    )
    
    # Verify the AuditLog was added to the dedicated session
    mock_session.add.assert_called_once()
    added_log = mock_session.add.call_args[0][0]
    assert isinstance(added_log, AuditLog)
    assert added_log.integration == "test"
    assert added_log.status == "success"
    assert added_log.status_code == 200
    assert added_log.client_id == client_id
    
    # Verify the dedicated session was committed
    mock_session.commit.assert_called_once()


@pytest.mark.asyncio
async def test_audited_http_client_success_logging():
    mock_callback = AsyncMock()
    
    with patch("httpx.AsyncClient.request") as mock_request:
        # Mock successful response
        mock_response = Response(200, json={"data": "test"})
        mock_response._request = MagicMock()
        mock_request.return_value = mock_response
        
        async with AuditedHTTPClient(provider="google", audit_callback=mock_callback) as client:
            result = await client.get("/test-endpoint", operation="test_op")
            assert result == {"data": "test"}
            
        # Verify callback was invoked with success details
        mock_callback.assert_called_once()
        call_kwargs = mock_callback.call_args[1]
        assert call_kwargs["status"] == "success"
        assert call_kwargs["status_code"] == 200
        assert call_kwargs["integration"] == "google"
        assert call_kwargs["operation"] == "test_op"
        assert call_kwargs["endpoint"] == "/test-endpoint"


@pytest.mark.asyncio
async def test_audited_http_client_failure_logging():
    mock_callback = AsyncMock()
    
    with patch("httpx.AsyncClient.request") as mock_request:
        # Mock 403 Forbidden response
        mock_response = Response(403, json={"error": "forbidden"})
        mock_response._request = MagicMock()
        mock_request.return_value = mock_response
        
        async with AuditedHTTPClient(provider="meta", audit_callback=mock_callback) as client:
            with pytest.raises(ExternalAPIError):
                await client.get("/restricted-endpoint", operation="restricted_op")
            
        # Verify callback was invoked with error details despite the exception
        mock_callback.assert_called_once()
        call_kwargs = mock_callback.call_args[1]
        assert call_kwargs["status"] == "error"
        assert call_kwargs["status_code"] == 403
        assert call_kwargs["integration"] == "meta"
        assert call_kwargs["operation"] == "restricted_op"
        assert call_kwargs["endpoint"] == "/restricted-endpoint"


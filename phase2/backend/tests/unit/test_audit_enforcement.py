import pytest
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from app.integrations.base.http_client import AuditedHTTPClient, ExternalAPIError
from app.api.v1.integrations import make_audit_callback

@pytest.mark.asyncio
@patch("app.api.v1.integrations.async_session_factory")
async def test_audited_http_client_logs_to_db(mock_async_session_factory):
    """Verify that using AuditedHTTPClient with make_audit_callback actually writes to the db."""
    mock_session = AsyncMock()
    mock_session.add = MagicMock()
    mock_async_session_factory.return_value.__aenter__.return_value = mock_session
    
    test_client_id = uuid.uuid4()
    audit_cb = make_audit_callback(session=mock_session, client_id=test_client_id)
    
    # Use a dummy provider but hit github API which is in ALLOWED_HOSTS
    async with AuditedHTTPClient(provider="dummy", access_token="fake_token", audit_callback=audit_cb) as client:
        try:
            # We hit a non-existent endpoint on api.github.com so it fails, but logs
            await client.get("https://api.github.com/non_existent_test_endpoint", operation="test_operation")
        except ExternalAPIError:
            pass
            
    # Check DB session was called to add AuditLog
    assert mock_session.add.called
    log = mock_session.add.call_args[0][0]
    
    assert log is not None, "Audit log was not created!"
    assert log.client_id == test_client_id
    assert log.integration == "dummy"
    assert log.operation == "test_operation"
    assert log.endpoint == "https://api.github.com/non_existent_test_endpoint"
    assert log.status == "error"
    assert log.duration_ms is not None

@pytest.mark.asyncio
async def test_audited_http_client_rejects_unauthorized_writes():
    """Verify AuditedHTTPClient blocks POST/PUT outside of OAuth."""
    async with AuditedHTTPClient(provider="dummy") as client:
        with pytest.raises(ExternalAPIError, match="Write operation POST is forbidden"):
            # Use allowed host to pass SSRF check, but get blocked by write operation check
            await client.post("https://api.github.com/api/data", data={"secret": "value"})

@pytest.mark.asyncio
async def test_audited_http_client_allows_oauth_post():
    """Verify AuditedHTTPClient allows POST to token endpoints."""
    logs = []
    async def dummy_cb(**kwargs):
        logs.append(kwargs)
        
    async with AuditedHTTPClient(provider="dummy", audit_callback=dummy_cb) as client:
        try:
            # Hit an allowed oauth endpoint
            await client.post("https://oauth2.googleapis.com/token", data={"grant_type": "test"})
        except ExternalAPIError as e:
            assert "forbidden" not in str(e).lower()
            
    assert len(logs) == 1
    assert logs[0]["endpoint"] == "https://oauth2.googleapis.com/token"
    assert logs[0]["status"] == "error"  # Fails because fake data, but it made the call

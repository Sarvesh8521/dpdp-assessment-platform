import ast
import asyncio
from pathlib import Path
import uuid
import pytest
from unittest.mock import AsyncMock, patch

from app.integrations.base.http_client import AuditedHTTPClient, ExternalAPIError
import httpx

# A simple fake response mimicking httpx.Response
class FakeResponse:
    def __init__(self, status_code, json_data=None, headers=None):
        self.status_code = status_code
        self._json = json_data or {}
        self.headers = headers or {}

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"Error {self.status_code}",
                request=httpx.Request("GET", "http://test"),
                response=self  # type: ignore
            )

@pytest.fixture
def mock_audit_callback():
    return AsyncMock()

@pytest.mark.asyncio
@patch("app.integrations.base.http_client.ALLOWED_HOSTS", {"api.example.com"})
async def test_audit_single_successful_call(mock_audit_callback):
    """Test 1 successful API call generates exactly 1 audit record."""
    with patch("httpx.AsyncClient.request", return_value=FakeResponse(200, {"success": True})):
        client = AuditedHTTPClient(
            provider="test_provider",
            base_url="https://api.example.com",
            audit_callback=mock_audit_callback,
            client_id="test_client",
            assessment_id="test_assessment"
        )
        
        await client.get("/test", operation="test_op")
        
        # Verify exactly 1 call
        assert mock_audit_callback.call_count == 1
        
        # Verify audit fields
        call_kwargs = mock_audit_callback.call_args.kwargs
        assert call_kwargs["client_id"] == "test_client"
        assert call_kwargs["assessment_id"] == "test_assessment"
        assert call_kwargs["operation"] == "test_op"
        assert call_kwargs["endpoint"] == "/test"
        assert call_kwargs["status"] == "success"
        assert call_kwargs["status_code"] == 200
        assert "duration_ms" in call_kwargs

@pytest.mark.asyncio
@patch("app.integrations.base.http_client.ALLOWED_HOSTS", {"api.example.com"})
async def test_audit_failed_call(mock_audit_callback):
    """Test 1 failed API call generates exactly 1 audit record with error status."""
    with patch("httpx.AsyncClient.request", return_value=FakeResponse(500)):
        client = AuditedHTTPClient(
            provider="test_provider",
            base_url="https://api.example.com",
            audit_callback=mock_audit_callback,
        )
        
        # Expecting ExternalAPIError because 500 triggers raise_for_status equivalent or manual raise
        with pytest.raises(ExternalAPIError):
            # We patch sleep to avoid actually waiting during retry attempts
            with patch("asyncio.sleep", return_value=None):
                await client.get("/failed")
                
        # The AuditedHTTPClient should still log exactly 1 audit record for the logical call
        assert mock_audit_callback.call_count == 1
        
        call_kwargs = mock_audit_callback.call_args.kwargs
        assert call_kwargs["status"] == "error"
        assert call_kwargs["status_code"] == 500

@pytest.mark.asyncio
@patch("app.integrations.base.http_client.ALLOWED_HOSTS", {"api.example.com"})
async def test_audit_timeout(mock_audit_callback):
    """Test timeout generates exactly 1 audit record with timeout status."""
    with patch("httpx.AsyncClient.request", side_effect=httpx.TimeoutException("Connection timeout")):
        client = AuditedHTTPClient(
            provider="test_provider",
            base_url="https://api.example.com",
            audit_callback=mock_audit_callback,
        )
        
        with pytest.raises(ExternalAPIError):
            with patch("asyncio.sleep", return_value=None):
                await client.get("/timeout")
                
        assert mock_audit_callback.call_count == 1
        call_kwargs = mock_audit_callback.call_args.kwargs
        assert call_kwargs["status"] == "timeout"
        assert call_kwargs["error_detail"] == "Connection timeout"

@pytest.mark.asyncio
@patch("app.integrations.base.http_client.ALLOWED_HOSTS", {"api.example.com"})
async def test_audit_multiple_calls(mock_audit_callback):
    """Test multiple API calls generate multiple audit records."""
    with patch("httpx.AsyncClient.request", return_value=FakeResponse(200)):
        client = AuditedHTTPClient(
            provider="test_provider",
            base_url="https://api.example.com",
            audit_callback=mock_audit_callback,
        )
        
        await client.get("/test1")
        await client.get("/test2")
        await client.get("/test3")
        
        assert mock_audit_callback.call_count == 3
        
        endpoints_called = [call.kwargs["endpoint"] for call in mock_audit_callback.call_args_list]
        assert endpoints_called == ["/test1", "/test2", "/test3"]

@pytest.mark.asyncio
@patch("app.integrations.base.http_client.ALLOWED_HOSTS", {"api.example.com"})
async def test_audit_retry_behavior(mock_audit_callback):
    """Test that a request which fails then succeeds on retry generates exactly 1 logical audit record."""
    # First call returns 500, second call returns 200
    mock_responses = [FakeResponse(500), FakeResponse(200, {"success": True})]
    
    with patch("httpx.AsyncClient.request", side_effect=mock_responses) as mock_request:
        client = AuditedHTTPClient(
            provider="test_provider",
            base_url="https://api.example.com",
            audit_callback=mock_audit_callback,
        )
        
        with patch("asyncio.sleep", return_value=None):
            await client.get("/retry")
            
        assert mock_request.call_count == 2
        assert mock_audit_callback.call_count == 1
        
        call_kwargs = mock_audit_callback.call_args.kwargs
        assert call_kwargs["status"] == "success"
        assert call_kwargs["status_code"] == 200

def test_no_adapter_bypasses_audit_layer():
    """
    Static analysis check: Parse all adapter.py files using AST.
    Ensure that none of them import `httpx`, `requests`, `aiohttp`, or `urllib`.
    This guarantees that all external calls must be routed through AuditedHTTPClient.
    """
    integrations_dir = Path("app/integrations")
    adapter_files = list(integrations_dir.rglob("adapter.py"))
    
    assert len(adapter_files) > 0, "No adapter files found!"
    
    forbidden_modules = {"httpx", "requests", "aiohttp", "urllib"}
    
    for adapter_file in adapter_files:
        content = adapter_file.read_text(encoding="utf-8")
        tree = ast.parse(content, filename=str(adapter_file))
        
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    module_base = alias.name.split('.')[0]
                    assert module_base not in forbidden_modules, (
                        f"SECURITY VIOLATION: {adapter_file} directly imports '{module_base}'. "
                        "All API calls must use AuditedHTTPClient to ensure audit logging."
                    )
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    module_base = node.module.split('.')[0]
                    if node.module == "urllib.parse":
                        continue  # Safe to use URL parsing
                    assert module_base not in forbidden_modules, (
                        f"SECURITY VIOLATION: {adapter_file} directly imports from '{module_base}'. "
                        "All API calls must use AuditedHTTPClient to ensure audit logging."
                    )

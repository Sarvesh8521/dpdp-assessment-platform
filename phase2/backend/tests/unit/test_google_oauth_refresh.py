import pytest
from unittest.mock import AsyncMock, patch
from typing import Any

from app.api.v1.oauth_handlers import GoogleOAuthHandler, OAuthError


@pytest.mark.asyncio
async def test_refresh_access_token_success():
    """Verify that refresh_access_token successfully posts and securely stores credentials."""
    handler = GoogleOAuthHandler()
    
    mock_post_response = {
        "access_token": "new-access-token",
        "expires_in": 3600,
        "token_type": "Bearer",
        # Simulate Google not returning a new refresh token
    }
    
    # Mock the AuditedHTTPClient
    with patch("app.api.v1.oauth_handlers.AuditedHTTPClient") as MockClient:
        mock_instance = MockClient.return_value.__aenter__.return_value
        mock_instance.post = AsyncMock(return_value=mock_post_response)
        
        # Mock store_tokens_securely
        with patch.object(handler, "store_tokens_securely", new_callable=AsyncMock) as mock_store:
            
            result = await handler.refresh_access_token(
                client_id="test-client-id",
                provider="google_ads",
                refresh_token="old-refresh-token"
            )
            
            # Should inject the old refresh token if missing
            assert result["access_token"] == "new-access-token"
            assert result["refresh_token"] == "old-refresh-token"
            
            # Assert client.post was called correctly
            mock_instance.post.assert_called_once()
            _, kwargs = mock_instance.post.call_args
            assert kwargs["data"]["refresh_token"] == "old-refresh-token"
            assert kwargs["data"]["grant_type"] == "refresh_token"
            assert kwargs["operation"] == "google_oauth_refresh"
            
            # Assert store_tokens_securely was called
            mock_store.assert_called_once_with(
                "test-client-id", "google_ads", result
            )


@pytest.mark.asyncio
async def test_refresh_access_token_failure():
    """Verify that refresh_access_token handles errors correctly by raising OAuthError."""
    handler = GoogleOAuthHandler()
    
    with patch("app.api.v1.oauth_handlers.AuditedHTTPClient") as MockClient:
        mock_instance = MockClient.return_value.__aenter__.return_value
        mock_instance.post = AsyncMock(side_effect=Exception("Invalid grant"))
        
        with patch.object(handler, "store_tokens_securely", new_callable=AsyncMock) as mock_store:
            
            with pytest.raises(OAuthError) as exc_info:
                await handler.refresh_access_token(
                    client_id="test-client-id",
                    provider="google_ads",
                    refresh_token="old-refresh-token"
                )
                
            assert "Google token refresh failed: Invalid grant" in str(exc_info.value)
            # Make sure it didn't try to store invalid tokens
            mock_store.assert_not_called()

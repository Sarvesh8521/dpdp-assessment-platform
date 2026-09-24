import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import HTTPException

from app.db.models.client import Client
from app.db.models.connection import Connection, ConnectionStatus
from app.api.v1.integrations import _get_capped_expires_at, _require_active_engagement
from app.services.assessment_service import AssessmentService

@pytest.fixture
def mock_session():
    session = AsyncMock(spec=AsyncSession)
    return session

@pytest.mark.asyncio
async def test_get_capped_expires_at_token_expires_before_engagement(mock_session):
    client_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    
    mock_client = Client(id=client_id, engagement_end=now + timedelta(days=10))
    mock_session.get.return_value = mock_client
    
    # Token expires in 1 day
    expires_at = await _get_capped_expires_at(mock_session, client_id, token_expires_in=86400)
    
    assert expires_at is not None
    # Difference should be roughly 1 day
    delta = expires_at - now
    assert timedelta(hours=23) < delta < timedelta(hours=25)

@pytest.mark.asyncio
async def test_get_capped_expires_at_engagement_ends_before_token(mock_session):
    client_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    
    engagement_end = now + timedelta(days=1)
    mock_client = Client(id=client_id, engagement_end=engagement_end)
    mock_session.get.return_value = mock_client
    
    # Token expires in 10 days
    expires_at = await _get_capped_expires_at(mock_session, client_id, token_expires_in=864000)
    
    assert expires_at == engagement_end

@pytest.mark.asyncio
async def test_get_capped_expires_at_no_token_expiry(mock_session):
    client_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    
    engagement_end = now + timedelta(days=1)
    mock_client = Client(id=client_id, engagement_end=engagement_end)
    mock_session.get.return_value = mock_client
    
    expires_at = await _get_capped_expires_at(mock_session, client_id, token_expires_in=None)
    
    assert expires_at == engagement_end

@pytest.mark.asyncio
async def test_require_active_engagement_blocks_expired(mock_session):
    client_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    
    # Engagement ended 1 day ago
    mock_client = Client(id=client_id, engagement_end=now - timedelta(days=1))
    mock_session.get.return_value = mock_client
    
    with pytest.raises(HTTPException) as exc_info:
        await _require_active_engagement(mock_session, client_id)
    
    assert exc_info.value.status_code == 403
    assert "engagement period has ended" in exc_info.value.detail

@pytest.mark.asyncio
async def test_require_active_engagement_allows_active(mock_session):
    client_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    
    # Engagement ends in 1 day
    mock_client = Client(id=client_id, engagement_end=now + timedelta(days=1))
    mock_session.get.return_value = mock_client
    
    # Should not raise exception
    await _require_active_engagement(mock_session, client_id)

@pytest.mark.asyncio
async def test_refresh_connection_blocks_after_engagement(mock_session):
    client_id = uuid.uuid4()
    assessment_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    
    mock_client = Client(id=client_id, engagement_end=now - timedelta(days=1))
    mock_session.get.return_value = mock_client
    
    service = AssessmentService(mock_session, client_id)
    service.assessment_id = assessment_id
    
    # Mock token that is expired
    expired_obtained_at = (now - timedelta(hours=2)).isoformat()
    secret_str = f'{{"access_token": "expired", "refresh_token": "refresh", "obtained_at": "{expired_obtained_at}", "expires_in": 3600}}'
    
    with patch('app.services.assessment_service.get_secret_manager') as mock_sm:
        mock_manager = AsyncMock()
        mock_manager.get_secret.return_value = secret_str
        mock_sm.return_value = mock_manager

        db_conn = Connection(
            id=uuid.uuid4(),
            client_id=client_id,
            provider="gtm",
            secret_ref="mock_ref",
            status=ConnectionStatus.CONNECTED
        )
        
        with patch("app.api.v1.oauth_handlers.GoogleOAuthHandler") as mock_handler_class:
            mock_handler = AsyncMock()
            mock_handler_class.return_value = mock_handler
            
            token, err = await service._resolve_access_token(db_conn, "gtm")
            
            assert token == ""
            assert err == "Access expired — engagement period ended"
            assert db_conn.status == ConnectionStatus.EXPIRED
            mock_session.commit.assert_called()
            mock_handler.refresh_access_token.assert_not_called()
            mock_manager.revoke_secret.assert_called_once_with(str(client_id), "mock_ref")

@pytest.mark.asyncio
async def test_enforce_engagement_expiry_revokes_secret(mock_session):
    from app.api.v1.integrations import _enforce_engagement_expiry
    
    client_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    mock_client = Client(id=client_id, engagement_end=now - timedelta(days=1))
    mock_session.get.return_value = mock_client
    
    connection = Connection(
        id=uuid.uuid4(),
        client_id=client_id,
        provider="github",
        secret_ref="test_secret",
        status=ConnectionStatus.CONNECTED
    )
    
    with patch('app.api.v1.integrations.get_secret_manager') as mock_sm:
        mock_manager = AsyncMock()
        mock_sm.return_value = mock_manager
        
        result = await _enforce_engagement_expiry(mock_session, connection)
        
        assert result is True
        assert connection.status == ConnectionStatus.EXPIRED
        assert connection.revoked_at is not None
        mock_manager.revoke_secret.assert_called_once_with(str(client_id), "test_secret")
        mock_session.commit.assert_called_once()

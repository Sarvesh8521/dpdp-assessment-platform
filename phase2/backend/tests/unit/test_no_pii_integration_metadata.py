import pytest
import uuid
from fastapi import FastAPI
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, patch

from app.api.v1.integrations import router as integrations_router
from app.db.models.connection import Connection, ConnectionStatus
from app.db.models.client import Client
from app.schemas.api import IntegrationStatusResponse
from app.auth.dependencies import get_current_user, get_db_session
from unittest.mock import AsyncMock, patch, MagicMock, Mock

app = FastAPI()
app.include_router(integrations_router)

async def override_get_current_user():
    auth_ctx = Mock()
    auth_ctx.user_id = uuid.uuid4()
    auth_ctx.require_client_access = Mock()
    return auth_ctx

app.dependency_overrides[get_current_user] = override_get_current_user

client = TestClient(app)


@pytest.mark.asyncio
@patch("app.api.v1.integrations.get_db_session")
async def test_integration_status_response_no_pii(mock_get_db_session):
    mock_session = AsyncMock()
    mock_get_db_session.return_value = mock_session
    
    # Need to override get_db_session in dependencies too
    async def override_get_db_session():
        yield mock_session
    
    app.dependency_overrides[get_db_session] = override_get_db_session
    
    mock_client = Client(id=uuid.uuid4())
    
    # Mock existing connection with some metadata (but NO PII allowed by schema anyway)
    mock_connection = Connection(
        id=uuid.uuid4(),
        client_id=mock_client.id,
        provider="google_ads",
        status=ConnectionStatus.CONNECTED,
        resource_id="12345",
        resource_name="Google Ads Customer",
        provider_metadata={"setup_action": "connected", "other": "data"},
        connected_at=None)
    
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [mock_connection]
    mock_session.execute.return_value = mock_result
    
    response = client.get(
        f"/integrations/status?client_id={mock_client.id}",
        headers={"Authorization": "Bearer fake_token"}
    )
    
    # Asserting response structure
    data = response.json()
    assert "integrations" in data
    
    # Assert PII fields are present in schema but null (no PII stored in this mock)
    for integration in data["integrations"]:
        if integration["provider"] == "google_ads" and integration["status"] == "connected":
            # Fields exist in schema but are null since mock metadata has no account_email/account_name
            assert integration.get("account_email") is None
            assert integration.get("account_name") is None


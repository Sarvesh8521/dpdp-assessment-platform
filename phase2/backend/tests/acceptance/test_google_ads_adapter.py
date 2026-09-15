import pytest
from datetime import datetime, timezone
from app.integrations.google_ads.adapter import GoogleAdsAdapter
from app.integrations.base.adapter import ConnectionInfo

@pytest.mark.asyncio
async def test_google_ads_adapter(mocker):
    adapter = GoogleAdsAdapter()
    connection = ConnectionInfo(provider="google_ads", scopes=["read"], connected_at=datetime.now(timezone.utc))
    
    mock_client_class = mocker.patch("app.integrations.google_ads.adapter.AuditedHTTPClient")
    mock_instance = mock_client_class.return_value
    mock_instance.get = mocker.AsyncMock(return_value={})
    mock_instance.close = mocker.AsyncMock()
    
    result = await adapter.collect(connection)
    assert isinstance(result.evidence, list)

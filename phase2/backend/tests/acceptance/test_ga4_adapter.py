import pytest
from datetime import datetime, timezone
from app.integrations.ga4.adapter import GA4Adapter
from app.integrations.base.adapter import ConnectionInfo
from app.domain.evidence import Verdict

@pytest.mark.asyncio
async def test_ga4_adapter(mocker):
    adapter = GA4Adapter()
    connection = ConnectionInfo(provider="ga4", scopes=["read"], connected_at=datetime.now(timezone.utc))
    
    mock_client_class = mocker.patch("app.integrations.ga4.adapter.AuditedHTTPClient")
    mock_instance = mock_client_class.return_value
    mock_instance.get = mocker.AsyncMock()
    mock_instance.close = mocker.AsyncMock()
    
    async def mock_get(endpoint, **kwargs):
        if "accountSummaries" in endpoint:
            return {"accountSummaries": [{"propertySummaries": [{"property": "properties/123"}]}]}
        elif "dataRetentionSettings" in endpoint:
            return {"eventDataRetention": "FIFTY_FOUR_MONTHS", "resetUserDataOnNewActivity": True}
        elif "googleSignalsSettings" in endpoint:
            return {"state": "GOOGLE_SIGNALS_ENABLED"}
        elif "customDimensions" in endpoint:
            return {"customDimensions": [{"parameterName": "user_email"}]}
        return {}
        
    mock_instance.get.side_effect = mock_get
    
    result = await adapter.collect(connection)
    
    assert len(result.errors) == 0
    
    # Retention
    retention_ev = next(e for e in result.evidence if e.obligation_id == "S8.retention")
    assert retention_ev.verdict == Verdict.OBSERVED
    assert "FIFTY_FOUR_MONTHS" in retention_ev.detail
    
    # Signals
    signals_ev = next(e for e in result.evidence if e.obligation_id == "S9.sharing.crossDevice")
    assert signals_ev.verdict == Verdict.OBSERVED
    
    # PII Custom Dimension
    dim_ev = next(e for e in result.evidence if e.obligation_id == "S8.dataMinimization")
    assert dim_ev.verdict == Verdict.OBSERVED
    assert "user_email" in dim_ev.detail

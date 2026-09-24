import pytest
from datetime import datetime, timezone
from app.integrations.ga4.adapter import GA4Adapter
from app.integrations.base.adapter import ConnectionInfo
from app.domain.evidence import Verdict
from app.integrations.base.http_client import ExternalAPIError

async def dummy_audit(*args, **kwargs): pass

@pytest.mark.asyncio
async def test_ga4_adapter_success(mocker):
    adapter = GA4Adapter()
    connection = ConnectionInfo(
        provider="ga4", 
        scopes=["read"], 
        connected_at=datetime.now(timezone.utc),
        metadata={"resource_id": "properties/123", "resource_name": "Test Property"}
    )
    
    mock_client_class = mocker.patch("app.integrations.ga4.adapter.AuditedHTTPClient")
    mock_instance = mock_client_class.return_value
    mock_instance.get = mocker.AsyncMock()
    mock_instance.close = mocker.AsyncMock()
    
    async def mock_get(endpoint, **kwargs):
        if "dataRetentionSettings" in endpoint:
            return {"eventDataRetention": "FIFTY_FOUR_MONTHS", "resetUserDataOnNewActivity": True}
        elif "googleSignalsSettings" in endpoint:
            return {"state": "GOOGLE_SIGNALS_ENABLED"}
        elif "customDimensions" in endpoint:
            return {"customDimensions": [{"parameterName": "user_email"}]}
        return {}
        
    mock_instance.get.side_effect = mock_get
    
    result = await adapter.collect(connection, dummy_audit)
    
    assert len(result.errors) == 0
    
    # Retention
    retention_ev = next(e for e in result.evidence if e.obligation_id == "S8.retention")
    assert retention_ev.verdict == Verdict.OBSERVED
    assert "FIFTY_FOUR_MONTHS" in retention_ev.detail
    
    # Signals
    signals_ev = next(e for e in result.evidence if e.obligation_id == "S9.sharing.crossDevice")
    assert signals_ev.verdict == Verdict.OBSERVED
    
    # PII Custom Dimension
    dim_ev = next(e for e in result.evidence if e.obligation_id == "S8.dataMinimization" and "user_email" in getattr(e, "detail", ""))
    assert dim_ev.verdict == Verdict.OBSERVED
    assert "user_email" in dim_ev.detail

@pytest.mark.asyncio
async def test_ga4_adapter_api_failures_graceful(mocker):
    adapter = GA4Adapter()
    connection = ConnectionInfo(
        provider="ga4", 
        scopes=["read"], 
        connected_at=datetime.now(timezone.utc),
        metadata={"resource_id": "properties/123"}
    )
    
    mock_client_class = mocker.patch("app.integrations.ga4.adapter.AuditedHTTPClient")
    mock_instance = mock_client_class.return_value
    mock_instance.get = mocker.AsyncMock()
    mock_instance.close = mocker.AsyncMock()
    
    # Simulate API errors (e.g. 404 or 403)
    mock_instance.get.side_effect = ExternalAPIError("API Error 404 Resource Not Found")
    
    result = await adapter.collect(connection, dummy_audit)
    
    # The property itself will fail because the first call is get_property
    assert len(result.errors) == 0 # we catch it and add evidence, not global error
    assert result.partial == False # Since it didn't throw an unhandled exception at top level
    
    prop_err_ev = next(e for e in result.evidence if e.source == "ga4:property_error")
    assert prop_err_ev.verdict == Verdict.CANNOT_DETERMINE
    assert "API Error 404" in prop_err_ev.detail

@pytest.mark.asyncio
async def test_ga4_adapter_signals_failure_graceful(mocker):
    adapter = GA4Adapter()
    connection = ConnectionInfo(
        provider="ga4", 
        scopes=["read"], 
        connected_at=datetime.now(timezone.utc),
        metadata={"resource_id": "properties/123"}
    )
    
    mock_client_class = mocker.patch("app.integrations.ga4.adapter.AuditedHTTPClient")
    mock_instance = mock_client_class.return_value
    mock_instance.get = mocker.AsyncMock()
    mock_instance.close = mocker.AsyncMock()
    
    async def mock_get(endpoint, **kwargs):
        if "googleSignalsSettings" in endpoint:
            raise ExternalAPIError("Resource not found")
        return {}
        
    mock_instance.get.side_effect = mock_get
    
    result = await adapter.collect(connection, dummy_audit)
    
    signals_ev = next(e for e in result.evidence if e.obligation_id == "S9.sharing.crossDevice")
    assert signals_ev.verdict == Verdict.CANNOT_DETERMINE
    assert "Resource not found" in signals_ev.detail

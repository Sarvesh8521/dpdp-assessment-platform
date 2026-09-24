import uuid
from unittest.mock import AsyncMock, patch, MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.assessment import Assessment
from app.db.models.client import Client
from app.db.models.connection import Connection, ConnectionStatus
from app.db.models.audit_log import AuditLog
from app.schemas.api import AssessmentCreate, AssessmentConfig
from app.services.assessment_service import AssessmentService
from app.integrations.base.http_client import ExternalAPIError
from app.domain.evidence import Verdict


@pytest.fixture
def mock_session():
    session = AsyncMock()
    session.flush = AsyncMock()
    
    async def mock_get(model, id):
        if model == Client:
            return Client(id=id)
        if model == Assessment:
            return Assessment(id=id, client_id=uuid.uuid4())
        return None
        
    session.get = AsyncMock(side_effect=mock_get)
    return session

@pytest.fixture
def mock_secret_manager():
    with patch("app.services.assessment_service.get_secret_manager") as mock_gsm:
        mock_sm = AsyncMock()
        mock_sm.get_secret.return_value = '{"access_token": "fake-token"}'
        mock_gsm.return_value = mock_sm
        yield mock_sm


def _setup_mock_connection(mock_session, provider, resource_id="", metadata=None):
    mock_conn = Connection(
        id=uuid.uuid4(),
        client_id=uuid.uuid4(),
        provider=provider,
        status=ConnectionStatus.CONNECTED,
        resource_id=resource_id,
        resource_name="Test Resource",
        secret_ref="fake-secret-ref",
        provider_metadata=metadata or {}
    )
    mock_result = MagicMock()
    mock_result.scalars.return_value.first.return_value = mock_conn
    mock_session.execute.return_value = mock_result
    return mock_conn


@pytest.mark.asyncio
@patch("app.services.report_generator.ReportGeneratorService.generate_report", new_callable=AsyncMock)
@patch("app.services.finding_service.FindingService.generate_findings_for_assessment", new_callable=AsyncMock)
@patch("app.services.assessment_service.run_reconciliation")
async def test_google_ads_missing_dev_token(
    mock_recon, mock_gen_findings, mock_gen_report, mock_session, mock_secret_manager
):
    """Test that missing developer token immediately results in Cannot Determine."""
    _setup_mock_connection(mock_session, "google_ads", resource_id="1234567890", metadata={})
    client_id = uuid.uuid4()
    
    class MockObligations(dict):
        def __contains__(self, key): return True
        def __getitem__(self, key): return key
        
    service = AssessmentService(mock_session, client_id)
    service._ensure_obligations_seeded = AsyncMock(return_value=MockObligations())
    
    data = AssessmentCreate(
        client_id=client_id,
        config=AssessmentConfig(integrations=["google_ads"])
    )
    
    await service.create_assessment(data, uuid.uuid4())
    
    from app.db.models.evidence import Evidence
    evidence_added = [
        call.args[0] for call in mock_session.add.call_args_list 
        if isinstance(call.args[0], Evidence)
    ]
    
    assert len(evidence_added) == 1
    ev = evidence_added[0]
    assert ev.obligation_id == "S9.sharing.audienceUpload"
    ev_val = ev.verdict.value if hasattr(ev.verdict, "value") else str(ev.verdict)
    assert ev_val == "Cannot determine"
    assert "Missing Google Ads developer token" in getattr(ev, "detail", "")


@pytest.mark.asyncio
@patch("app.services.report_generator.ReportGeneratorService.generate_report", new_callable=AsyncMock)
@patch("app.services.finding_service.FindingService.generate_findings_for_assessment", new_callable=AsyncMock)
@patch("app.services.assessment_service.run_reconciliation")
async def test_google_ads_acceptance_success(
    mock_recon, mock_gen_findings, mock_gen_report, mock_session, mock_secret_manager
):
    """Test full Google Ads flow where API endpoints return successfully."""
    class MockClient:
        def __init__(self, *args, audit_callback=None, provider="google_ads", **kwargs):
            self.audit_callback = audit_callback
            self.provider = provider

        async def post(self, endpoint, json_body=None, operation="unknown", **kwargs):
            if self.audit_callback:
                await self.audit_callback(provider=self.provider, operation=operation, endpoint=endpoint)
                
            query = json_body.get("query", "")
            if "FROM user_list" in query:
                return [{"results": [{"userList": {"name": "Test Upload List", "type": "CRM_BASED"}}]}]
            elif "FROM conversion_action" in query:
                return [{"results": [{"conversionAction": {"name": "Test Conversion", "status": "ENABLED", "type": "ENHANCED_CONVERSION"}}]}]
            elif "FROM customer" in query:
                return [{"results": [{"customer": {"trackingUrlTemplate": "http://test.com/?gclid=123"}}]}]
            elif "FROM customer_client_link" in query:
                return [{"results": [{"customerClientLink": {"clientCustomer": "999888777", "status": "ACTIVE"}}]}]
            return [{"results": []}]
            
        async def close(self):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

    _setup_mock_connection(mock_session, "google_ads", resource_id="123-456-7890", metadata={"developer_token": "valid-token"})
    client_id = uuid.uuid4()
    
    class MockObligations(dict):
        def __contains__(self, key): return True
        def __getitem__(self, key): return key
        
    service = AssessmentService(mock_session, client_id)
    service._ensure_obligations_seeded = AsyncMock(return_value=MockObligations())
    
    data = AssessmentCreate(
        client_id=client_id,
        config=AssessmentConfig(integrations=["google_ads"])
    )
    
    audit_records = []
    async def mock_audit_callback(**kwargs):
        audit_records.append(kwargs)

    with patch("app.services.assessment_service.make_audit_callback", return_value=mock_audit_callback):
        with patch("app.integrations.google_ads.adapter.AuditedHTTPClient", MockClient):
            await service.create_assessment(data, uuid.uuid4())
        
    from app.db.models.evidence import Evidence
    evidence_added = [
        call.args[0] for call in mock_session.add.call_args_list 
        if isinstance(call.args[0], Evidence)
    ]
    
    # Assert Customer Match (audience upload) observed
    audience_ev = next((e for e in evidence_added if e.obligation_id == "S9.sharing.audienceUpload" and "customer_match" in getattr(e, "source", "")), None)
    assert audience_ev is not None
    ev_val = audience_ev.verdict.value if hasattr(audience_ev.verdict, "value") else str(audience_ev.verdict)
    assert ev_val == "Observed"
    
    # Assert Conversions observed
    conv_ev = next((e for e in evidence_added if e.obligation_id == "S9.sharing.enhancedConversions"), None)
    assert conv_ev is not None
    
    # Assert no PII or user/event payload data in evidence
    for ev in evidence_added:
        metadata_str = str(ev.sanitized_metadata).lower()
        assert "user_id" not in metadata_str
        assert "event_payload" not in metadata_str

    # Assert AuditLogs were created for API calls
    assert len(audit_records) >= 4
    actions = [log.get("operation") for log in audit_records]
    assert "search_customer_match_lists" in actions
    assert "search_conversion_actions" in actions
    assert "search_customer_settings" in actions
    assert "search_customer_client_links" in actions


@pytest.mark.asyncio
@patch("app.services.report_generator.ReportGeneratorService.generate_report", new_callable=AsyncMock)
@patch("app.services.finding_service.FindingService.generate_findings_for_assessment", new_callable=AsyncMock)
@patch("app.services.assessment_service.run_reconciliation")
async def test_google_ads_forbidden(
    mock_recon, mock_gen_findings, mock_gen_report, mock_session, mock_secret_manager
):
    """Test full Google Ads flow where the API returns 403 Forbidden."""
    class MockClient:
        def __init__(self, *args, audit_callback=None, provider="google_ads", **kwargs):
            self.audit_callback = audit_callback
            self.provider = provider

        async def post(self, endpoint, json_body=None, operation="unknown", **kwargs):
            if self.audit_callback:
                await self.audit_callback(provider=self.provider, operation=operation, endpoint=endpoint)
            raise ExternalAPIError("Missing permissions", status_code=403, provider="google_ads")
            
        async def close(self):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

    _setup_mock_connection(mock_session, "google_ads", resource_id="1234567890", metadata={"developer_token": "valid-token"})
    client_id = uuid.uuid4()
    
    class MockObligations(dict):
        def __contains__(self, key): return True
        def __getitem__(self, key): return key
        
    service = AssessmentService(mock_session, client_id)
    service._ensure_obligations_seeded = AsyncMock(return_value=MockObligations())
    
    data = AssessmentCreate(
        client_id=client_id,
        config=AssessmentConfig(integrations=["google_ads"])
    )
    
    audit_records = []
    async def mock_audit_callback(**kwargs):
        audit_records.append(kwargs)

    with patch("app.services.assessment_service.make_audit_callback", return_value=mock_audit_callback):
        with patch("app.integrations.google_ads.adapter.AuditedHTTPClient", MockClient):
            await service.create_assessment(data, uuid.uuid4())
        
    from app.db.models.evidence import Evidence
    evidence_added = [
        call.args[0] for call in mock_session.add.call_args_list 
        if isinstance(call.args[0], Evidence)
    ]
    
    # Should result in Cannot determine for everything
    audience_ev = next((e for e in evidence_added if e.obligation_id == "S9.sharing.audienceUpload" and "error" in getattr(e, "source", "")), None)
    assert audience_ev is not None
    ev_val = audience_ev.verdict.value if hasattr(audience_ev.verdict, "value") else str(audience_ev.verdict)
    assert ev_val == "Cannot determine"

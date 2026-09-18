import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch, MagicMock

import pytest

from app.db.models.client import Client
from app.db.models.assessment import Assessment
from app.db.models.connection import Connection, ConnectionStatus
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
            return Assessment(id=id, client_id=id)
        return None
    session.get.side_effect = mock_get
    
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
async def test_meta_acceptance_no_resource(
    mock_recon, mock_gen_findings, mock_gen_report, mock_session, mock_secret_manager
):
    """Test Meta flow when no ad account or business id is selected."""
    _setup_mock_connection(mock_session, "meta", resource_id="")
    client_id = uuid.uuid4()
    
    class MockObligations(dict):
        def __contains__(self, key): return True
        def __getitem__(self, key): return key
        
    service = AssessmentService(mock_session, client_id)
    service._ensure_obligations_seeded = AsyncMock(return_value=MockObligations())
    
    data = AssessmentCreate(
        client_id=client_id,
        config=AssessmentConfig(integrations=["meta"])
    )
    
    await service.create_assessment(data, uuid.uuid4())
    
    from app.db.models.evidence import Evidence
    evidence_added = [
        call.args[0] for call in mock_session.add.call_args_list 
        if isinstance(call.args[0], Evidence)
    ]
    
    # We should have CANNOT_DETERMINE evidence for all meta obligations
    # Meta obligations: S9.sharing.audienceUpload, S9.sharing.advancedMatching, S9.sharing.documented, S9.sharing.serverSide
    audience_ev = next((e for e in evidence_added if e.obligation_id == "S9.sharing.audienceUpload"), None)
    assert audience_ev is not None
    ev_verdict = audience_ev.verdict.value if hasattr(audience_ev.verdict, "value") else str(audience_ev.verdict)
    assert ev_verdict == "Cannot determine"
    assert "no Business resource is selected" in audience_ev.detail


@pytest.mark.asyncio
@patch("app.services.report_generator.ReportGeneratorService.generate_report", new_callable=AsyncMock)
@patch("app.services.finding_service.FindingService.generate_findings_for_assessment", new_callable=AsyncMock)
@patch("app.services.assessment_service.run_reconciliation")
async def test_meta_acceptance_success(
    mock_recon, mock_gen_findings, mock_gen_report, mock_session, mock_secret_manager
):
    """Test full Meta flow where API endpoints return successfully."""
    class MockClient:
        def __init__(self, *args, **kwargs):
            pass

        async def get(self, endpoint, **kwargs):
            if "customaudiences" in endpoint:
                return {
                    "data": [
                        {"name": "Email List 1", "subtype": "CUSTOM", "approximate_count": 1000}
                    ]
                }
            elif "adspixels" in endpoint:
                return {
                    "data": [
                        {
                            "name": "My Pixel",
                            "automatic_matching_fields": ["em", "ph"],
                            "server_events_enabled": True
                        }
                    ]
                }
            elif "agencies" in endpoint or "client_businesses" in endpoint:
                return {"data": [{"id": "123", "name": "Marketing Agency"}]}
            elif "offline_conversion_data_sets" in endpoint:
                return {"data": [{"id": "456", "name": "Offline Sales"}]}
            return {"data": []}
            
        async def close(self):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

    _setup_mock_connection(mock_session, "meta", resource_id="act_123456", metadata={"business_id": "789"})
    client_id = uuid.uuid4()
    
    class MockObligations(dict):
        def __contains__(self, key): return True
        def __getitem__(self, key): return key
        
    service = AssessmentService(mock_session, client_id)
    service._ensure_obligations_seeded = AsyncMock(return_value=MockObligations())
    
    data = AssessmentCreate(
        client_id=client_id,
        config=AssessmentConfig(integrations=["meta"])
    )
    
    with patch("app.integrations.meta.adapter.AuditedHTTPClient", return_value=MockClient()):
        await service.create_assessment(data, uuid.uuid4())
        
    from app.db.models.evidence import Evidence
    evidence_added = [
        call.args[0] for call in mock_session.add.call_args_list 
        if isinstance(call.args[0], Evidence)
    ]
    
    # Assert Custom Audience observed
    audience_ev = next((e for e in evidence_added if e.obligation_id == "S9.sharing.audienceUpload" and "custom_audience" in getattr(e, "source", "")), None)
    assert audience_ev is not None
    ev_val = audience_ev.verdict.value if hasattr(audience_ev.verdict, "value") else str(audience_ev.verdict)
    assert ev_val == "Observed"
    
    # Assert Pixel Advanced Matching observed
    am_ev = next((e for e in evidence_added if e.obligation_id == "S9.sharing.advancedMatching"), None)
    assert am_ev is not None
    
    # Assert CAPI observed
    capi_ev = next((e for e in evidence_added if e.obligation_id == "S9.sharing.serverSide"), None)
    assert capi_ev is not None
    
    # Assert Business sharing observed
    biz_ev = next((e for e in evidence_added if e.obligation_id == "S9.sharing.documented"), None)
    assert biz_ev is not None


@pytest.mark.asyncio
@patch("app.services.report_generator.ReportGeneratorService.generate_report", new_callable=AsyncMock)
@patch("app.services.finding_service.FindingService.generate_findings_for_assessment", new_callable=AsyncMock)
@patch("app.services.assessment_service.run_reconciliation")
async def test_meta_acceptance_partial_failure(
    mock_recon, mock_gen_findings, mock_gen_report, mock_session, mock_secret_manager
):
    """Test full Meta flow where one endpoint fails but the rest succeed."""
    class MockClient:
        def __init__(self, *args, **kwargs):
            pass

        async def get(self, endpoint, **kwargs):
            if "customaudiences" in endpoint:
                raise ExternalAPIError("Rate limited", status_code=429, provider="meta")
            elif "adspixels" in endpoint:
                return {
                    "data": [
                        {
                            "name": "My Pixel",
                            "automatic_matching_fields": ["em", "ph"],
                            "server_events_enabled": True
                        }
                    ]
                }
            return {"data": []}
            
        async def close(self):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

    _setup_mock_connection(mock_session, "meta", resource_id="act_123456", metadata={"business_id": "789"})
    client_id = uuid.uuid4()
    
    class MockObligations(dict):
        def __contains__(self, key): return True
        def __getitem__(self, key): return key
        
    service = AssessmentService(mock_session, client_id)
    service._ensure_obligations_seeded = AsyncMock(return_value=MockObligations())
    
    data = AssessmentCreate(
        client_id=client_id,
        config=AssessmentConfig(integrations=["meta"])
    )
    
    with patch("app.integrations.meta.adapter.AuditedHTTPClient", return_value=MockClient()):
        await service.create_assessment(data, uuid.uuid4())
        
    from app.db.models.evidence import Evidence
    evidence_added = [
        call.args[0] for call in mock_session.add.call_args_list 
        if isinstance(call.args[0], Evidence)
    ]
    
    # Custom Audiences should gracefully fail to CANNOT_DETERMINE
    audience_ev = next((e for e in evidence_added if e.obligation_id == "S9.sharing.audienceUpload" and "error:custom_audiences" in getattr(e, "source", "")), None)
    assert audience_ev is not None
    ev_verdict = audience_ev.verdict.value if hasattr(audience_ev.verdict, "value") else str(audience_ev.verdict)
    assert ev_verdict == "Cannot determine"
    
    # But Pixels should still be OBSERVED
    am_ev = next((e for e in evidence_added if e.obligation_id == "S9.sharing.advancedMatching"), None)
    assert am_ev is not None
    am_verdict = am_ev.verdict.value if hasattr(am_ev.verdict, "value") else str(am_ev.verdict)
    assert am_verdict == "Observed"

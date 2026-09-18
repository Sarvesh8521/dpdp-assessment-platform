import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch, MagicMock

import pytest

from app.db.models.client import Client
from app.db.models.assessment import Assessment
from app.db.models.connection import Connection, ConnectionStatus
from app.schemas.api import AssessmentCreate, AssessmentConfig
from app.services.assessment_service import AssessmentService
from app.integrations.base.adapter import AdapterResult, ConnectionInfo


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


def _setup_mock_connection(mock_session, provider, resource_id="res-123"):
    mock_conn = Connection(
        id=uuid.uuid4(),
        client_id=uuid.uuid4(),
        provider=provider,
        status=ConnectionStatus.CONNECTED,
        resource_id=resource_id,
        resource_name="Test Resource",
        secret_ref="fake-secret-ref",
    )
    mock_result = MagicMock()
    mock_result.scalars.return_value.first.return_value = mock_conn
    
    # We patch execute so that when fetching connections it returns this mock_conn
    mock_session.execute.return_value = mock_result
    return mock_conn


@pytest.mark.asyncio
@patch("app.services.assessment_service.get_adapter")
@patch("app.services.report_generator.ReportGeneratorService.generate_report", new_callable=AsyncMock)
@patch("app.services.finding_service.FindingService.generate_findings_for_assessment", new_callable=AsyncMock)
@patch("app.services.assessment_service.run_reconciliation")
async def test_execution_connected_gtm_collects(
    mock_recon, mock_gen_findings, mock_gen_report, mock_get_adapter, 
    mock_session, mock_secret_manager
):
    """Test connected GTM calls adapter.collect() with correct access token."""
    mock_adapter = AsyncMock()
    mock_adapter.id = "gtm"
    mock_adapter.provider = "gtm"
    mock_adapter.collect.return_value = AdapterResult(evidence=[])
    mock_get_adapter.return_value = mock_adapter

    _setup_mock_connection(mock_session, "gtm")
    client_id = uuid.uuid4()
    service = AssessmentService(mock_session, client_id)
    
    data = AssessmentCreate(
        client_id=client_id,
        config=AssessmentConfig(integrations=["gtm"])
    )
    
    await service.create_assessment(data, uuid.uuid4())
    
    mock_adapter.collect.assert_called_once()
    kwargs = mock_adapter.collect.call_args.kwargs
    assert kwargs["access_token"] == "fake-token"


@pytest.mark.asyncio
@patch("app.services.assessment_service.get_adapter")
@patch("app.services.report_generator.ReportGeneratorService.generate_report", new_callable=AsyncMock)
@patch("app.services.finding_service.FindingService.generate_findings_for_assessment", new_callable=AsyncMock)
@patch("app.services.assessment_service.run_reconciliation")
async def test_execution_google_ads_receives_customer_id(
    mock_recon, mock_gen_findings, mock_gen_report, mock_get_adapter, 
    mock_session, mock_secret_manager
):
    """Test connected Google Ads receives customer_id (not ad_account_id) kwarg."""
    mock_adapter = AsyncMock()
    mock_adapter.id = "google_ads"
    mock_adapter.provider = "google_ads"
    mock_adapter.collect.return_value = AdapterResult(evidence=[])
    mock_get_adapter.return_value = mock_adapter

    _setup_mock_connection(mock_session, "google_ads", resource_id="cust-123")
    client_id = uuid.uuid4()
    service = AssessmentService(mock_session, client_id)
    
    data = AssessmentCreate(
        client_id=client_id,
        config=AssessmentConfig(integrations=["google_ads"])
    )
    
    await service.create_assessment(data, uuid.uuid4())
    
    mock_adapter.collect.assert_called_once()
    kwargs = mock_adapter.collect.call_args.kwargs
    assert "customer_id" in kwargs
    assert kwargs["customer_id"] == "cust-123"
    assert "ad_account_id" not in kwargs


@pytest.mark.asyncio
@patch("app.services.assessment_service.get_adapter")
@patch("app.services.report_generator.ReportGeneratorService.generate_report", new_callable=AsyncMock)
@patch("app.services.finding_service.FindingService.generate_findings_for_assessment", new_callable=AsyncMock)
@patch("app.services.assessment_service.run_reconciliation")
async def test_execution_meta_receives_ad_account_id(
    mock_recon, mock_gen_findings, mock_gen_report, mock_get_adapter, 
    mock_session, mock_secret_manager
):
    """Test connected Meta receives ad_account_id kwarg."""
    mock_adapter = AsyncMock()
    mock_adapter.id = "meta"
    mock_adapter.provider = "meta"
    mock_adapter.collect.return_value = AdapterResult(evidence=[])
    mock_get_adapter.return_value = mock_adapter

    _setup_mock_connection(mock_session, "meta", resource_id="act_123")
    client_id = uuid.uuid4()
    service = AssessmentService(mock_session, client_id)
    
    data = AssessmentCreate(
        client_id=client_id,
        config=AssessmentConfig(integrations=["meta"])
    )
    
    await service.create_assessment(data, uuid.uuid4())
    
    mock_adapter.collect.assert_called_once()
    kwargs = mock_adapter.collect.call_args.kwargs
    assert "ad_account_id" in kwargs
    assert kwargs["ad_account_id"] == "act_123"


@pytest.mark.asyncio
@patch("app.services.assessment_service.get_adapter")
@patch("app.services.report_generator.ReportGeneratorService.generate_report", new_callable=AsyncMock)
@patch("app.services.finding_service.FindingService.generate_findings_for_assessment", new_callable=AsyncMock)
@patch("app.services.assessment_service.run_reconciliation")
async def test_execution_search_console_metadata_contains_resource_id(
    mock_recon, mock_gen_findings, mock_gen_report, mock_get_adapter, 
    mock_session, mock_secret_manager
):
    """Test connected Search Console connection info metadata contains resource_id."""
    mock_adapter = AsyncMock()
    mock_adapter.id = "search_console"
    mock_adapter.provider = "search_console"
    mock_adapter.collect.return_value = AdapterResult(evidence=[])
    mock_get_adapter.return_value = mock_adapter

    _setup_mock_connection(mock_session, "search_console", resource_id="sc-prop-url")
    client_id = uuid.uuid4()
    service = AssessmentService(mock_session, client_id)
    
    data = AssessmentCreate(
        client_id=client_id,
        config=AssessmentConfig(integrations=["search_console"])
    )
    
    await service.create_assessment(data, uuid.uuid4())
    
    mock_adapter.collect.assert_called_once()
    conn_info: ConnectionInfo = mock_adapter.collect.call_args.args[0]
    assert conn_info.metadata.get("resource_id") == "sc-prop-url"


@pytest.mark.asyncio
@patch("app.services.assessment_service.get_adapter")
@patch("app.services.report_generator.ReportGeneratorService.generate_report", new_callable=AsyncMock)
@patch("app.services.finding_service.FindingService.generate_findings_for_assessment", new_callable=AsyncMock)
@patch("app.services.assessment_service.run_reconciliation")
async def test_execution_connected_no_resource_produces_cannot_determine(
    mock_recon, mock_gen_findings, mock_gen_report, mock_get_adapter, 
    mock_session, mock_secret_manager
):
    """Test connected integration with NO resource selected explicitly degrades."""
    mock_adapter = AsyncMock()
    mock_adapter.id = "ga4"
    mock_adapter.provider = "ga4"
    mock_adapter.obligations_answered = ["S8.retention"]
    mock_get_adapter.return_value = mock_adapter

    # Connected but NO resource selected
    _setup_mock_connection(mock_session, "ga4", resource_id=None)
    client_id = uuid.uuid4()
    service = AssessmentService(mock_session, client_id)
    
    data = AssessmentCreate(
        client_id=client_id,
        config=AssessmentConfig(integrations=["ga4"])
    )
    
    await service.create_assessment(data, uuid.uuid4())
    
    # Adapter should NOT be called
    mock_adapter.collect.assert_not_called()
    
    # Should produce CANNOT_DETERMINE evidence for missing resource
    from app.db.models.evidence import Evidence
    evidence_added = [
        call.args[0] for call in mock_session.add.call_args_list 
        if isinstance(call.args[0], Evidence)
    ]
    assert len(evidence_added) == 1
    ev = evidence_added[0]
    assert ev.source == "ga4:no_resource"
    ev_verdict_val = ev.verdict.value if hasattr(ev.verdict, "value") else str(ev.verdict)
    assert ev_verdict_val == "Cannot determine"
    assert "no assessment resource has been selected" in ev.detail


@pytest.mark.asyncio
@patch("app.services.assessment_service.get_adapter")
@patch("app.services.report_generator.ReportGeneratorService.generate_report", new_callable=AsyncMock)
@patch("app.services.finding_service.FindingService.generate_findings_for_assessment", new_callable=AsyncMock)
@patch("app.services.assessment_service.run_reconciliation")
async def test_execution_adapter_exception_produces_cannot_determine(
    mock_recon, mock_gen_findings, mock_gen_report, mock_get_adapter, 
    mock_session, mock_secret_manager
):
    """Test adapter exception explicitly degrades to CANNOT_DETERMINE evidence."""
    mock_adapter = AsyncMock()
    mock_adapter.id = "gtm"
    mock_adapter.provider = "gtm"
    mock_adapter.obligations_answered = ["CMP.purposes.configured"]
    mock_adapter.collect.side_effect = Exception("API rate limit exceeded")
    mock_get_adapter.return_value = mock_adapter

    _setup_mock_connection(mock_session, "gtm", resource_id="GTM-123")
    client_id = uuid.uuid4()
    service = AssessmentService(mock_session, client_id)
    
    data = AssessmentCreate(
        client_id=client_id,
        config=AssessmentConfig(integrations=["gtm"])
    )
    
    await service.create_assessment(data, uuid.uuid4())
    
    mock_adapter.collect.assert_called_once()
    
    # Should produce CANNOT_DETERMINE evidence with the error message
    from app.db.models.evidence import Evidence
    evidence_added = [
        call.args[0] for call in mock_session.add.call_args_list 
        if isinstance(call.args[0], Evidence)
    ]
    assert len(evidence_added) == 1
    ev = evidence_added[0]
    assert ev.source == "gtm:error"
    ev_verdict_val = ev.verdict.value if hasattr(ev.verdict, "value") else str(ev.verdict)
    assert ev_verdict_val == "Cannot determine"
    assert "API rate limit exceeded" in ev.detail

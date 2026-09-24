import uuid
from unittest.mock import AsyncMock, patch, MagicMock

import pytest

from app.db.models.assessment import Assessment
from app.db.models.client import Client
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
async def test_search_console_missing_property(
    mock_recon, mock_gen_findings, mock_gen_report, mock_session, mock_secret_manager
):
    """Test that missing property URL results in Cannot Determine."""
    _setup_mock_connection(mock_session, "search_console", resource_id="")
    client_id = uuid.uuid4()
    
    class MockObligations(dict):
        def __contains__(self, key): return True
        def __getitem__(self, key): return key
        
    service = AssessmentService(mock_session, client_id)
    service._ensure_obligations_seeded = AsyncMock(return_value=MockObligations())
    
    data = AssessmentCreate(
        client_id=client_id,
        config=AssessmentConfig(integrations=["search_console"], properties=[])
    )
    
    await service.create_assessment(data, uuid.uuid4())
    
    from app.db.models.evidence import Evidence
    evidence_added = [
        call.args[0] for call in mock_session.add.call_args_list 
        if isinstance(call.args[0], Evidence)
    ]
    
    assert len(evidence_added) == 1
    ev = evidence_added[0]
    assert ev.obligation_id == "S10.scope.properties"
    ev_val = ev.verdict.value if hasattr(ev.verdict, "value") else str(ev.verdict)
    assert ev_val == "Cannot determine"
    assert "was not selected" in getattr(ev, "detail", "") or "Missing property URL" in getattr(ev, "detail", "")


@pytest.mark.asyncio
@patch("app.services.report_generator.ReportGeneratorService.generate_report", new_callable=AsyncMock)
@patch("app.services.finding_service.FindingService.generate_findings_for_assessment", new_callable=AsyncMock)
@patch("app.services.assessment_service.run_reconciliation")
async def test_search_console_scan_success(
    mock_recon, mock_gen_findings, mock_gen_report, mock_session, mock_secret_manager
):
    """Test full Search Console flow where API endpoints return successfully."""
    class MockClient:
        def __init__(self, *args, audit_callback=None, provider="search_console", **kwargs):
            self.audit_callback = audit_callback
            self.provider = provider
            self.base_url = kwargs.get("base_url")

        async def get(self, endpoint, operation="unknown", **kwargs):
            if "searchAnalytics" in endpoint or "query" in endpoint:
                raise ValueError("Search Query endpoint was illegally invoked!")

            if self.audit_callback:
                await self.audit_callback(provider=self.provider, operation=operation, endpoint=endpoint)
                
            if "sitemaps" in endpoint:
                return {"sitemap": [{"path": "https://example.com/sitemap.xml", "type": "sitemap", "warnings": 0, "errors": 0}]}
            elif "sites" in endpoint:
                return {"siteEntry": [{"siteUrl": "https://example.com/", "permissionLevel": "siteOwner"}]}
            return {}
            
        async def close(self):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

    # Use "https://example.com/" as resource_id
    _setup_mock_connection(mock_session, "search_console", resource_id="https://example.com/")
    client_id = uuid.uuid4()
    
    class MockObligations(dict):
        def __contains__(self, key): return True
        def __getitem__(self, key): return key
        
    service = AssessmentService(mock_session, client_id)
    service._ensure_obligations_seeded = AsyncMock(return_value=MockObligations())
    
    data = AssessmentCreate(
        client_id=client_id,
        config=AssessmentConfig(integrations=["search_console"])
    )
    
    audit_records = []
    async def mock_audit_callback(**kwargs):
        audit_records.append(kwargs)

    with patch("app.services.assessment_service.make_audit_callback", return_value=mock_audit_callback):
        with patch("app.integrations.search_console.adapter.AuditedHTTPClient", MockClient):
            await service.create_assessment(data, uuid.uuid4())
        
    from app.db.models.evidence import Evidence
    evidence_added = [
        call.args[0] for call in mock_session.add.call_args_list 
        if isinstance(call.args[0], Evidence)
    ]
    
    # Check that we observed properties and sitemaps
    prop_ev = next((e for e in evidence_added if "verified_site" in getattr(e, "source", "")), None)
    sitemap_ev = next((e for e in evidence_added if "sitemap" in getattr(e, "source", "")), None)
    
    assert prop_ev is not None
    ev_val = prop_ev.verdict.value if hasattr(prop_ev.verdict, "value") else str(prop_ev.verdict)
    assert ev_val == "Observed"
    
    assert sitemap_ev is not None
    
    # Assert AuditLogs were created for API calls
    assert len(audit_records) == 2
    actions = [log.get("operation") for log in audit_records]
    assert "list_sites" in actions
    assert "list_sitemaps" in actions


@pytest.mark.asyncio
@patch("app.services.report_generator.ReportGeneratorService.generate_report", new_callable=AsyncMock)
@patch("app.services.finding_service.FindingService.generate_findings_for_assessment", new_callable=AsyncMock)
@patch("app.services.assessment_service.run_reconciliation")
async def test_search_console_forbidden(
    mock_recon, mock_gen_findings, mock_gen_report, mock_session, mock_secret_manager
):
    """Test full Search Console flow where the API returns 403 Forbidden."""
    class MockClient:
        def __init__(self, *args, audit_callback=None, provider="search_console", **kwargs):
            self.audit_callback = audit_callback
            self.provider = provider

        async def get(self, endpoint, operation="unknown", **kwargs):
            if self.audit_callback:
                await self.audit_callback(provider=self.provider, operation=operation, endpoint=endpoint)
            raise ExternalAPIError("Missing permissions", status_code=403, provider="search_console")
            
        async def close(self):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

    _setup_mock_connection(mock_session, "search_console", resource_id="https://example.com/")
    client_id = uuid.uuid4()
    
    class MockObligations(dict):
        def __contains__(self, key): return True
        def __getitem__(self, key): return key
        
    service = AssessmentService(mock_session, client_id)
    service._ensure_obligations_seeded = AsyncMock(return_value=MockObligations())
    
    data = AssessmentCreate(
        client_id=client_id,
        config=AssessmentConfig(integrations=["search_console"])
    )
    
    audit_records = []
    async def mock_audit_callback(**kwargs):
        audit_records.append(kwargs)

    with patch("app.services.assessment_service.make_audit_callback", return_value=mock_audit_callback):
        with patch("app.integrations.search_console.adapter.AuditedHTTPClient", MockClient):
            await service.create_assessment(data, uuid.uuid4())
        
    from app.db.models.evidence import Evidence
    evidence_added = [
        call.args[0] for call in mock_session.add.call_args_list 
        if isinstance(call.args[0], Evidence)
    ]
    
    # Since both sites and sitemaps failed, we don't return CANNOT_DETERMINE explicitly if they error,
    # wait... we return whatever the adapter generated or the assessment service defaults?
    # Actually the adapter catches it and appends to `errors` which the assessment catches.
    # We should just assert that no evidence was generated and it didn't crash.
    assert len(evidence_added) == 0

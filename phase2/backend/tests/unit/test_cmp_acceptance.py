import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch, MagicMock

import pytest

from app.db.models.client import Client
from app.db.models.assessment import Assessment
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


def _setup_mock_connection(mock_session, provider, resource_name="onetrust"):
    mock_conn = Connection(
        id=uuid.uuid4(),
        client_id=uuid.uuid4(),
        provider=provider,
        status=ConnectionStatus.CONNECTED,
        resource_id="cmp_tenant_id",
        resource_name=resource_name,  # "onetrust", "cookieyes", "securiti"
        secret_ref="fake-secret-ref",
        scopes=["onetrust:read"] if resource_name == "onetrust" else ["read"],
        connected_at=datetime.now(timezone.utc),
        provider_metadata={"business_id": "fake_business"}
    )
    
    mock_result = MagicMock()
    mock_result.scalars.return_value.first.return_value = mock_conn
    mock_result.scalars.return_value.all.return_value = []
    mock_session.execute.return_value = mock_result
    return mock_conn


@pytest.mark.asyncio
@patch("app.services.report_generator.ReportGeneratorService.generate_report", new_callable=AsyncMock)
@patch("app.services.finding_service.FindingService.generate_findings_for_assessment", new_callable=AsyncMock)
@patch("app.services.assessment_service.run_reconciliation")
async def test_cmp_acceptance_onetrust_success(mock_recon, mock_gen_findings, mock_gen_report, mock_session, mock_secret_manager):
    # Setup mock connection for OneTrust CMP
    _setup_mock_connection(mock_session, "cmp", resource_name="onetrust")

    client_id = uuid.uuid4()
    
    class MockObligations(dict):
        def __contains__(self, key): return True
        def __getitem__(self, key): return key
        
    service = AssessmentService(mock_session, client_id)
    service._ensure_obligations_seeded = AsyncMock(return_value=MockObligations())

    data = AssessmentCreate(
        client_id=client_id,
        config=AssessmentConfig(
            integrations=["cmp"]
        )
    )

    class MockClient:
        def __init__(self, provider, audit_callback=None, **kwargs):
            self.audit_callback = audit_callback
            self.provider = provider

        async def get(self, endpoint, operation="unknown", **kwargs):
            if self.audit_callback:
                await self.audit_callback(
                    actor=f"adapter:{self.provider}",
                    integration=self.provider,
                    operation=operation,
                    endpoint=endpoint,
                    status="success",
                    status_code=200,
                    duration_ms=10)
                
            if "/purposes" in endpoint:
                return {"content": [{"id": "1", "name": "Strictly Necessary"}]}
            if "/cookie-categories" in endpoint:
                return {"content": [{"id": "C0001", "name": "Essential"}]}
            if "/geolocation-rules" in endpoint:
                return {"content": [{"regions": ["Asia", "IN"]}]}
            if "/banner-configurations" in endpoint:
                return {"content": [{"id": "B1"}]}
            if "/consent-receipts/schema" in endpoint:
                return {"fields": [{"name": "receiptId"}]}
            if "/integrations" in endpoint:
                return {"content": [{"id": "I1"}]}
            return {}
            
        async def close(self):
            pass

    with patch("app.services.assessment_service.make_audit_callback") as mock_make_audit:
        audit_records = []
        async def mock_audit_callback(**kwargs):
            audit_records.append(kwargs)
        mock_make_audit.return_value = mock_audit_callback

        with patch("app.integrations.cmp.adapter.AuditedHTTPClient", MockClient):
            # Execute Assessment
            assessment = await service.create_assessment(data, uuid.uuid4())
    
            # Verify evidence was saved via session.add
            added_objects = [call.args[0] for call in mock_session.add.call_args_list]
            from app.db.models.evidence import Evidence
            evidence_objects = [obj for obj in added_objects if isinstance(obj, Evidence)]
    
            # We expect 6 pieces of evidence from the OneTrust adapter
            # (purposes, categories, geolocation, banners, schema, integrations)
            assert len(evidence_objects) == 6
    
            # Check for specific evidence outcomes
            purposes = next((e for e in evidence_objects if "purposes" in e.source), None)
            assert purposes is not None
            assert purposes.verdict == Verdict.OBSERVED
            assert purposes.obligation_id == "CMP.purposes.configured"
    
            geo = next((e for e in evidence_objects if "geolocation" in e.source), None)
            assert geo is not None
            assert geo.verdict == Verdict.OBSERVED
            assert geo.obligation_id == "CMP.geolocation.india"
    
            # Verify Audit Logs were created
            assert len(audit_records) >= 6
            for log in audit_records:
                assert log.get("integration") == "onetrust"
                assert "unknown" in log.get("operation") or log.get("operation") != ""


@pytest.mark.asyncio
@patch("app.services.report_generator.ReportGeneratorService.generate_report", new_callable=AsyncMock)
@patch("app.services.finding_service.FindingService.generate_findings_for_assessment", new_callable=AsyncMock)
@patch("app.services.assessment_service.run_reconciliation")
async def test_cmp_acceptance_cookieyes_placeholder(mock_recon, mock_gen_findings, mock_gen_report, mock_session, mock_secret_manager):
    # Setup mock connection for CookieYes CMP
    _setup_mock_connection(mock_session, "cmp", resource_name="cookieyes")

    client_id = uuid.uuid4()

    class MockObligations(dict):
        def __contains__(self, key): return True
        def __getitem__(self, key): return key
        
    service = AssessmentService(mock_session, client_id)
    service._ensure_obligations_seeded = AsyncMock(return_value=MockObligations())

    data = AssessmentCreate(
        client_id=client_id,
        config=AssessmentConfig(
            integrations=["cmp"]
        )
    )

    assessment = await service.create_assessment(data, uuid.uuid4())

    added_objects = [call.args[0] for call in mock_session.add.call_args_list]
    from app.db.models.evidence import Evidence
    evidence_objects = [obj for obj in added_objects if isinstance(obj, Evidence)]

    # CookieYes is unsupported, so it should return CANNOT_DETERMINE gracefully
    assert len(evidence_objects) == 1
    evidence = evidence_objects[0]
    
    assert evidence.verdict == Verdict.CANNOT_DETERMINE
    assert "CookieYes API is not currently supported" in evidence.detail
    assert "cookieyes" in evidence.source


@pytest.mark.asyncio
@patch("app.services.report_generator.ReportGeneratorService.generate_report", new_callable=AsyncMock)
@patch("app.services.finding_service.FindingService.generate_findings_for_assessment", new_callable=AsyncMock)
@patch("app.services.assessment_service.run_reconciliation")
async def test_cmp_acceptance_securiti_placeholder(mock_recon, mock_gen_findings, mock_gen_report, mock_session, mock_secret_manager):
    # Setup mock connection for Securiti CMP
    _setup_mock_connection(mock_session, "cmp", resource_name="securiti")

    client_id = uuid.uuid4()

    class MockObligations(dict):
        def __contains__(self, key): return True
        def __getitem__(self, key): return key
        
    service = AssessmentService(mock_session, client_id)
    service._ensure_obligations_seeded = AsyncMock(return_value=MockObligations())

    data = AssessmentCreate(
        client_id=client_id,
        config=AssessmentConfig(
            integrations=["cmp"]
        )
    )

    assessment = await service.create_assessment(data, uuid.uuid4())

    added_objects = [call.args[0] for call in mock_session.add.call_args_list]
    from app.db.models.evidence import Evidence
    evidence_objects = [obj for obj in added_objects if isinstance(obj, Evidence)]

    # Securiti is unsupported, so it should return CANNOT_DETERMINE gracefully
    assert len(evidence_objects) == 1
    evidence = evidence_objects[0]
    
    assert evidence.verdict == Verdict.CANNOT_DETERMINE
    assert "Securiti API is not currently supported" in evidence.detail
    assert "securiti" in evidence.source

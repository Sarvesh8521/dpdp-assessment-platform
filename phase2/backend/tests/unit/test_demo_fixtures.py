import pytest
import uuid
from unittest.mock import AsyncMock, patch, MagicMock

from app.services.assessment_service import AssessmentService
from app.schemas.api import AssessmentCreate, AssessmentConfig
from app.db.models.client import Client
from app.domain.evidence import Verdict


@pytest.fixture
def mock_session():
    session = AsyncMock()
    # Ensure flush doesn't break
    session.flush = AsyncMock()
    
    # We will need to return a mock client when awaited
    async def mock_get(model, id):
        if model == Client:
            return Client(id=id)
        if model == Assessment:
            # We don't care about the fields too much for the report generator, just that it returns an assessment with the right client
            # The report generator expects client_id matching its own.
            # But wait, how do we know the client_id? It's just a mock anyway.
            # Let's just create a generic Assessment with the ID.
            # Wait, report generator checks: `if not assessment or assessment.client_id != self.client_id`
            # To handle this, we can patch ReportGeneratorService completely since we are not testing report generation.
            pass
        return None
    session.get.side_effect = mock_get
    
    # We will need to return no connection
    mock_result = MagicMock()
    mock_result.scalars().first.return_value = None
    session.execute.return_value = mock_result
    
    return session


@pytest.mark.asyncio
@patch("app.services.report_generator.ReportGeneratorService.generate_report", new_callable=AsyncMock)
async def test_normal_assessment_contains_no_fixtures(mock_generate, mock_session):
    client_id = uuid.uuid4()
    service = AssessmentService(mock_session, client_id)
    
    data = AssessmentCreate(
        client_id=client_id,
        config=AssessmentConfig(
            integrations=["gtm"],
            is_demo=False
        )
    )
    
    assessment = await service.create_assessment(data, uuid.uuid4())
    
    # Extract evidence added to session
    from app.db.models.evidence import Evidence
    evidence_items = [
        call.args[0] for call in mock_session.add.call_args_list 
        if isinstance(call.args[0], Evidence)
    ]
    
    # Verify no phase1_scanner was injected
    has_phase1 = any("phase1" in ev.source.lower() for ev in evidence_items)
    assert not has_phase1
    
    # Verify GTM is "not connected" / "Cannot determine"
    gtm_evidence = [ev for ev in evidence_items if "gtm" in ev.source.lower()]
    assert len(gtm_evidence) > 0
    assert all(ev.verdict == Verdict.CANNOT_DETERMINE for ev in gtm_evidence)


@pytest.mark.asyncio
@patch("app.services.report_generator.ReportGeneratorService.generate_report", new_callable=AsyncMock)
async def test_explicit_demo_assessment_contains_fixtures(mock_generate, mock_session):
    client_id = uuid.uuid4()
    service = AssessmentService(mock_session, client_id)
    
    data = AssessmentCreate(
        client_id=client_id,
        config=AssessmentConfig(
            integrations=["gtm"],
            is_demo=True
        )
    )
    
    with patch("app.services.assessment_service.get_settings") as mock_settings:
        mock_settings.return_value.demo_mode = False # Global demo mode off
        
        assessment = await service.create_assessment(data, uuid.uuid4())
        
        # Extract evidence added to session
        from app.db.models.evidence import Evidence
        evidence_items = [
            call.args[0] for call in mock_session.add.call_args_list 
            if isinstance(call.args[0], Evidence)
        ]
        
        # Verify phase1_scanner WAS injected
        has_phase1 = any("phase1" in ev.source.lower() for ev in evidence_items)
        assert has_phase1
        
        # Verify GTM used the fixture (has some observed evidence rather than CANNOT_DETERMINE)
        gtm_evidence = [ev for ev in evidence_items if "gtm" in ev.source.lower()]
        assert len(gtm_evidence) > 0
        has_observed = any(ev.verdict == Verdict.OBSERVED for ev in gtm_evidence)
        assert has_observed


@pytest.mark.asyncio
@patch("app.services.report_generator.ReportGeneratorService.generate_report", new_callable=AsyncMock)
async def test_missing_integration_produces_cannot_determine(mock_generate, mock_session):
    client_id = uuid.uuid4()
    service = AssessmentService(mock_session, client_id)
    
    data = AssessmentCreate(
        client_id=client_id,
        config=AssessmentConfig(
            integrations=["google_ads"],
            is_demo=False
        )
    )
    
    assessment = await service.create_assessment(data, uuid.uuid4())
    
    from app.db.models.evidence import Evidence
    evidence_items = [
        call.args[0] for call in mock_session.add.call_args_list 
        if isinstance(call.args[0], Evidence)
    ]
    
    ads_evidence = [ev for ev in evidence_items if "google_ads" in ev.source.lower()]
    assert len(ads_evidence) > 0
    assert all(ev.verdict == Verdict.CANNOT_DETERMINE for ev in ads_evidence)
    assert all("not connected" in ev.detail.lower() for ev in ads_evidence)

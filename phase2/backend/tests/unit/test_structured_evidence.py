import uuid
from datetime import datetime, timezone
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from app.domain.evidence import Confidence, EvidenceItem, Verdict, StructuredEvidence
from app.db.models.client import Client
from app.db.models.assessment import Assessment, AssessmentStatus
from app.db.models.evidence import Evidence
from app.db.models.finding import Finding
from app.db.models.obligation import Obligation, ObligationSeverity
from app.schemas.api import AssessmentConfig, AssessmentCreate
from app.services.assessment_service import AssessmentService
from app.services.finding_service import FindingService
from app.services.report_generator import ReportGeneratorService


@pytest.fixture
def mock_session():
    session = AsyncMock()
    session.add = MagicMock()
    
    # Needs to handle different gets
    async def mock_get(model, id):
        if model == Client:
            return Client(id=id)
        if model == Assessment:
            return Assessment(id=id, client_id=uuid.UUID(int=1), configuration={"is_demo": False})
        return None
    session.get.side_effect = mock_get
    
    return session


@pytest.fixture
def mock_db_evidence():
    return Evidence(
        id=uuid.uuid4(),
        client_id=uuid.UUID(int=1),
        assessment_id=uuid.uuid4(),
        obligation_id=uuid.uuid4(),
        verdict=Verdict.OBSERVED,
        confidence=Confidence.HIGH,
        source="gtm:tags",
        observed_at=datetime.now(timezone.utc),
        detail="Tag detail",
        sanitized_metadata={"some": "raw"},
        structured_detail={
            "source": "gtm",
            "integration": "gtm",
            "operation": "tags.get",
            "resource_type": "tag",
            "resource_id": "tag_123",
            "resource_name": "Google Analytics Tag",
            "location": "GTM-XXXX / Google Analytics Tag / All Pages",
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "observed_state": "consentSettings missing",
            "expected_state": "consentSettings configured",
            "detail": "The GTM configuration does not contain consent settings for this tag.",
            "reason": "Missing required configuration",
            "metadata": {"safe": "metadata"}
        }
    )

@pytest.fixture
def mock_obligation():
    return Obligation(
        id=uuid.uuid4(),
        code="OBL_1",
        title="Test Obligation",
        severity=ObligationSeverity.HIGH)


@pytest.mark.asyncio
async def test_structured_evidence_survives_to_finding(mock_session, mock_db_evidence, mock_obligation):
    """Proves structured_detail from Evidence makes it to Finding's evidence_details."""
    client_id = uuid.UUID(int=1)
    service = FindingService(mock_session, client_id)
    assessment_id = uuid.uuid4()
    
    # Mock the DB queries finding_service makes
    # 1. select(Evidence)
    evidence_scalar_mock = MagicMock()
    evidence_scalar_mock.all.return_value = [mock_db_evidence]
    
    # 2. select(Obligation)
    obl_scalar_mock = MagicMock()
    obl_scalar_mock.scalar_one_or_none.return_value = mock_obligation
    
    # 3. select(ReconciliationResult)
    recon_scalar_mock = MagicMock()
    recon_scalar_mock.all.return_value = []
    
    # Configure the execute mock to return the right scalar mocks in order
    mock_session.execute.side_effect = [
        # 1. db_evidence execution
        MagicMock(scalars=MagicMock(return_value=evidence_scalar_mock)),
        # 2. delete(Finding) execution
        MagicMock(),
        # 3. obligation execution
        MagicMock(scalar_one_or_none=MagicMock(return_value=mock_obligation)),
        # 4. recon records execution
        MagicMock(scalars=MagicMock(return_value=recon_scalar_mock)),
    ]
    
    await service.generate_findings_for_assessment(assessment_id)
    
    # Verify a Finding was created and added to the session
    mock_session.add.assert_called()
    added_finding = mock_session.add.call_args[0][0]
    
    assert isinstance(added_finding, Finding)
    # Important verification: the evidence_details was populated
    assert added_finding.evidence_details is not None
    assert len(added_finding.evidence_details) == 1
    assert added_finding.evidence_details[0]["resource_id"] == "tag_123"
    assert added_finding.evidence_details[0]["location"] == "GTM-XXXX / Google Analytics Tag / All Pages"


@pytest.mark.asyncio
async def test_finding_evidence_details_survives_to_report(mock_session, mock_obligation):
    """Proves finding.evidence_details makes it into the generated report JSON."""
    client_id = uuid.UUID(int=1)
    service = ReportGeneratorService(mock_session, client_id)
    assessment_id = uuid.uuid4()
    
    # Create a finding with structured evidence details
    db_finding = Finding(
        id=uuid.uuid4(),
        client_id=client_id,
        assessment_id=assessment_id,
        obligation_id=mock_obligation.id,
        verdict=Verdict.OBSERVED,
        confidence=Confidence.HIGH,
        severity="high",
        detail="Combined detail",
        evidence_details=[{
            "source": "gtm",
            "integration": "gtm",
            "resource_id": "tag_123",
            "location": "GTM-XXXX / Google Analytics Tag / All Pages"
        }]
    )
    
    # Mock the DB queries report_generator makes
    
    # 1. select(Finding)
    finding_scalar_mock = MagicMock()
    finding_scalar_mock.all.return_value = [db_finding]
    
    # 2. select(ReconciliationResult)
    recon_scalar_mock = MagicMock()
    recon_scalar_mock.all.return_value = []
    
    # 3. select(Obligation)
    obl_scalar_mock = MagicMock()
    obl_scalar_mock.all.return_value = [mock_obligation]
    
    # 4. select(Connection)
    conn_scalar_mock = MagicMock()
    conn_scalar_mock.all.return_value = []
    
    # 5. select(Report) (existing report)
    report_scalar_mock = MagicMock()
    report_scalar_mock.scalar_one_or_none.return_value = None
    
    mock_session.execute.side_effect = [
        MagicMock(scalars=MagicMock(return_value=finding_scalar_mock)),
        MagicMock(scalars=MagicMock(return_value=recon_scalar_mock)),
        MagicMock(scalars=MagicMock(return_value=obl_scalar_mock)),
        MagicMock(scalars=MagicMock(return_value=conn_scalar_mock)),
        MagicMock(scalar_one_or_none=MagicMock(return_value=None)), # existing JSON report
        MagicMock(scalar_one_or_none=MagicMock(return_value=None)), # existing PDF report
    ]
    
    # Run the generator
    with patch("app.services.report_generator.Path.write_text") as mock_write:
        report = await service.generate_report(assessment_id)
        
        # Verify the report generation wrote JSON with the evidence_details
        assert mock_write.called
        
        # The first call should be the combined report
        call_args = mock_write.call_args_list[0]
        json_output = call_args[0][0]
        
        import json
        parsed_report = json.loads(json_output)
        
        findings = parsed_report["findings"]
        assert len(findings) == 1
        assert "evidence_details" in findings[0]
        assert len(findings[0]["evidence_details"]) == 1
        assert findings[0]["evidence_details"][0]["resource_id"] == "tag_123"


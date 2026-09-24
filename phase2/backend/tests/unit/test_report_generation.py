import pytest
import uuid
from typing import Any
from sqlalchemy.ext.asyncio import AsyncSession
from unittest.mock import AsyncMock, MagicMock
from app.services.report_generator import ReportGeneratorService
from app.db.models.assessment import Assessment
from app.db.models.finding import Finding
from app.db.models.report import Report
from app.db.models.reconciliation_result import ReconciliationResult, ReconciliationOutcome
from app.db.models.obligation import Obligation
from app.db.models.connection import Connection
from app.db.models.evidence import Verdict, Confidence
from app.db.models.finding import FindingStatus, FindingSeverity


@pytest.fixture
def mock_session():
    return AsyncMock(spec=AsyncSession)


@pytest.fixture
def client_id():
    return uuid.uuid4()


@pytest.fixture
def assessment_id():
    return uuid.uuid4()


def mock_scalar_result(items):
    mock = MagicMock()
    scalars_mock = MagicMock()
    scalars_mock.all.return_value = items
    mock.scalars.return_value = scalars_mock
    mock.scalar_one_or_none.return_value = items[0] if items else None
    return mock


@pytest.mark.asyncio
async def test_report_generation_counts_and_structure(mock_session, client_id, assessment_id):
    """
    Test that total findings and verdict counts exactly match underlying data,
    without synthetic additions, and that phase1_scanner and scope_sheet
    are not turned into integrations.
    """
    service = ReportGeneratorService(mock_session, client_id)

    # 1. Setup mock assessment
    mock_assessment = Assessment(
        id=assessment_id,
        client_id=client_id,
        selected_integrations=["ga4", "gtm"]
    )
    mock_session.get.return_value = mock_assessment

    # 2. Setup mock findings
    f1 = Finding(
        id=uuid.uuid4(),
        assessment_id=assessment_id,
        client_id=client_id,
        obligation_id=uuid.uuid4(),
        verdict=Verdict.OBSERVED,
        confidence=Confidence.HIGH,
        severity=FindingSeverity.HIGH,
        status=FindingStatus.OPEN,
        human_review_required=False,
        detail="A real issue in GA4",
        sources=["ga4:settings"],
        evidence_details=[{"source": "ga4:settings", "detail": "foo"}]
    )
    f2 = Finding(
        id=uuid.uuid4(),
        assessment_id=assessment_id,
        client_id=client_id,
        obligation_id=uuid.uuid4(),
        verdict=Verdict.CANNOT_DETERMINE,
        confidence=Confidence.LOW,
        severity=FindingSeverity.LOW,
        status=FindingStatus.OPEN,
        human_review_required=True,
        detail="Missing data in GTM",
        sources=["gtm:workspaces"],
        evidence_details=[{"source": "gtm:workspaces", "detail": "foo"}]
    )
    f3_reconciliation = Finding(
        id=uuid.uuid4(),
        assessment_id=assessment_id,
        client_id=client_id,
        obligation_id=uuid.uuid4(),
        verdict=Verdict.OBSERVED,
        confidence=Confidence.HIGH,
        severity=FindingSeverity.MEDIUM,
        status=FindingStatus.OPEN,
        human_review_required=True,
        detail="Contradiction finding",
        sources=["phase1_scanner:cookies", "scope_sheet:marketing_platforms"],
        evidence_details=[{"source": "phase1_scanner:cookies", "detail": "Phase 1"}, {"source": "scope_sheet:marketing_platforms", "detail": "Declared"}]
    )

    mock_findings = [f1, f2, f3_reconciliation]

    # 3. Setup mock reconciliations
    r1 = ReconciliationResult(
        id=uuid.uuid4(),
        assessment_id=assessment_id,
        rule_id="test_rule",
        source_a="scope_sheet",
        source_b="phase1_scanner",
        outcome=ReconciliationOutcome.CONTRADICTION,
        confidence=Confidence.HIGH,
        human_review_required=True,
        detail="Phase 1 disagrees with scope sheet",
        obligation_id=f3_reconciliation.obligation_id,
        reconciliation_metadata={"evidence_a_detail": "Declared", "evidence_b_detail": "Phase 1"}
    )
    mock_reconciliations = [r1]

    # 4. Setup obligations
    o1 = Obligation(id=f1.obligation_id, code="OBL-1", title="GA4 Rule")
    o2 = Obligation(id=f2.obligation_id, code="OBL-2", title="GTM Rule")
    o3 = Obligation(id=f3_reconciliation.obligation_id, code="OBL-3", title="Cross Rule")
    mock_obligations = [o1, o2, o3]

    # Setup execute returns
    def mock_execute(stmt):
        stmt_str = str(stmt).lower()
        if "from findings" in stmt_str or "finding" in stmt_str:
            return mock_scalar_result(mock_findings)
        elif "from reconciliation_results" in stmt_str or "reconciliationresult" in stmt_str:
            return mock_scalar_result(mock_reconciliations)
        elif "from obligations" in stmt_str or "obligation" in stmt_str:
            return mock_scalar_result(mock_obligations)
        elif "from connections" in stmt_str or "connection" in stmt_str:
            return mock_scalar_result([])  # No connections
        elif "from reports" in stmt_str or "report" in stmt_str:
            return mock_scalar_result([])
        return mock_scalar_result([])
        
    mock_session.execute = AsyncMock(side_effect=mock_execute)
    
    # Mock PDFRenderer to avoid filesystem issues
    import unittest.mock
    with unittest.mock.patch("app.services.report_generator.PDFReportRenderer"):
        # 5. Generate Report
        report = await service.generate_report(assessment_id)
        assert report is not None
        assert report.assessment_id == assessment_id
        
        # Check the JSON payload written to disk
        import json
        from pathlib import Path
        
        reports_dir = Path("reports") / str(client_id)
        report_file = reports_dir / f"{assessment_id}.json"
        
        assert report_file.exists()
        
        data = json.loads(report_file.read_text(encoding="utf-8"))
        
        # 6. Verify Findings Collections and Counts
        assert len(data["findings"]) == 3, "Report renderer must not invent nor drop findings."
        
        # Summary counts must equal exactly what was provided
        summary = data["summary"]
        assert summary["total_findings"] == 3
        assert summary["verdicts"]["observed"] == 2
        assert summary["verdicts"]["cannot_determine"] == 1
        assert summary["verdicts"]["not_observed"] == 0
        assert summary["requires_human_review"] == 2
        
        # 7. Verify no synthetic missing-obligation findings exist
        for f in data["findings"]:
            assert not f["finding_id"].startswith("synth-")
            
        # 8. Verify Phase 1 and Scope Sheet are NOT integration reports
        integration_keys = list(data["individual_reports"].keys())
        assert "ga4" in integration_keys
        assert "gtm" in integration_keys
        assert "phase1_scanner" not in integration_keys, "phase1_scanner is context, not a Phase 2 integration."
        assert "scope_sheet" not in integration_keys, "scope_sheet is context, not a Phase 2 integration."
        
        # 9. Verify Google Ads/CMP without credentials logic
        ga4_report = data["individual_reports"]["ga4"]
        assert ga4_report["summary"]["assessment_outcome"] == "Integration not connected; no provider evidence collected."
        
        gtm_report = data["individual_reports"]["gtm"]
        assert gtm_report["summary"]["assessment_outcome"] == "Integration not connected; no provider evidence collected."
        
        # Verify exact matches for finding details in integration reports without mutating
        # The f1 finding belongs to ga4
        assert len(ga4_report["findings"]) == 1
        assert ga4_report["findings"][0]["finding_id"] == str(f1.id)
        assert ga4_report["findings"][0]["verdict"] == "observed" or ga4_report["findings"][0]["verdict"] == "Observed"
        
        # The f3_reconciliation finding has sources phase1_scanner and scope_sheet. 
        # It should not appear in ga4 or gtm reports!
        assert len([f for f in ga4_report["findings"] if f["finding_id"] == str(f3_reconciliation.id)]) == 0
        
        # 10. Verify cross source reconciliation
        assert len(data["reconciliations"]) == 1
        assert data["reconciliations"][0]["source_a"] == "scope_sheet"
        assert data["reconciliations"][0]["source_b"] == "phase1_scanner"

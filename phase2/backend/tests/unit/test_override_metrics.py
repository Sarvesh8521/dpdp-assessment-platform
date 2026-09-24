import pytest
import uuid
from unittest.mock import AsyncMock

from sqlalchemy.ext.asyncio import AsyncSession
from app.services.report_generator import ReportGeneratorService
from app.db.models.assessment import Assessment
from app.db.models.finding import FindingStatus, FindingSeverity
from app.db.models.evidence import Verdict, Confidence
from app.db.models.obligation import ObligationCategory

class MockAssessment:
    def __init__(self, id, client_id):
        self.id = id
        self.client_id = client_id
        self.selected_integrations = ["gtm", "ga4", "search_console"]
        self.configuration = {}

class MockObligation:
    def __init__(self, obl_id, code, title, severity):
        self.id = obl_id
        self.code = code
        self.title = title
        self.dpdp_section = "section 1"
        self.severity = severity
        self.description = "Compliance info"

class MockFinding:
    def __init__(self, f_id, obl, client_id, ass_id, verdict, status, sources):
        self.id = f_id
        self.client_id = client_id
        self.assessment_id = ass_id
        self.obligation_id = obl.id
        self.obligation_code = obl.code
        self.verdict = verdict
        self.confidence = Confidence.HIGH
        self.severity = FindingSeverity.HIGH
        self.detail = "detail"
        self.reason = "reason"
        self.status = status
        self.sources = sources
        self.human_review_required = False
        self.evidence_details = []
        self.overrides = []

@pytest.mark.asyncio
async def test_override_metrics_calculation():
    client_id = uuid.uuid4()
    assessment_id = uuid.uuid4()
    
    mock_session = AsyncMock(spec=AsyncSession)
    mock_assessment = MockAssessment(assessment_id, client_id)
    
    obl1 = MockObligation(uuid.uuid4(), "POL-1", "Privacy Policy", "high")
    obl2 = MockObligation(uuid.uuid4(), "TRK-1", "Restrict Tracking", "medium")
    
    # We have 3 actual findings. 2 are overridden.
    f1 = MockFinding(uuid.uuid4(), obl1, client_id, assessment_id, Verdict.OBSERVED, FindingStatus.OVERRIDDEN, ["gtm:123"])
    f2 = MockFinding(uuid.uuid4(), obl2, client_id, assessment_id, Verdict.OBSERVED, FindingStatus.OVERRIDDEN, ["ga4:456"])
    f3 = MockFinding(uuid.uuid4(), obl2, client_id, assessment_id, Verdict.NOT_OBSERVED, FindingStatus.OPEN, ["ga4:789"])
    
    def mock_execute(stmt, *args, **kwargs):
        class MockResult:
            def __init__(self, data):
                self.data = data
            def scalar_one_or_none(self):
                return self.data[0] if self.data else None
            def scalars(self):
                class ScalarProxy:
                    def all(self_proxy):
                        return self.data
                    def first(self_proxy):
                        return self.data[0] if self.data else None
                    def scalar_one_or_none(self_proxy):
                        return self.data[0] if self.data else None
                return ScalarProxy()
                
        stmt_str = str(stmt).lower()
        if "from findings" in stmt_str:
            return MockResult([f1, f2, f3])
        elif "from obligations" in stmt_str:
            return MockResult([obl1, obl2])
        elif "from connections" in stmt_str:
            return MockResult([])
        elif "from reconciliation_results" in stmt_str:
            return MockResult([])
            
        return MockResult([])

    mock_session.execute.side_effect = mock_execute
    mock_session.get.return_value = mock_assessment
    
    generator = ReportGeneratorService(mock_session, client_id)
    report = await generator.generate_report(assessment_id)
    
    import json
    metadata = json.loads(open(report.artifact_ref, "r").read())
    overall_summary = metadata["summary"]
    metrics = overall_summary["override_metrics"]
    
    # Check overall metrics
    assert metrics["total_actual_findings"] == 3
    assert metrics["overridden_count"] == 2
    assert metrics["override_rate"] == 0.6667
    assert metrics["health_warning"] is True
    
    # Check obligation breakdown
    by_obl = metrics["by_obligation"]
    
    assert "POL-1" in by_obl
    assert by_obl["POL-1"]["total"] == 1
    assert by_obl["POL-1"]["overridden"] == 1
    assert by_obl["POL-1"]["override_rate"] == 1.0
    assert by_obl["POL-1"]["health_warning"] is True
    
    assert "TRK-1" in by_obl
    assert by_obl["TRK-1"]["total"] == 2
    assert by_obl["TRK-1"]["overridden"] == 1
    assert by_obl["TRK-1"]["override_rate"] == 0.5
    assert by_obl["TRK-1"]["health_warning"] is True
    
    # Integration level
    ga4_report = None
    for r in metadata["individual_reports"].values():
        if r["integration"] == "ga4":
            ga4_report = r
            break
            
    assert ga4_report is not None
    ga4_metrics = ga4_report["summary"]["override_metrics"]
    assert ga4_metrics["total_actual_findings"] == 2
    assert ga4_metrics["overridden_count"] == 1
    assert ga4_metrics["override_rate"] == 0.5
    assert ga4_metrics["health_warning"] is True

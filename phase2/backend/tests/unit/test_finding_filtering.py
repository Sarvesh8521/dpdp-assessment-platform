import pytest
import uuid
from typing import Any
from unittest.mock import AsyncMock

from sqlalchemy.ext.asyncio import AsyncSession
from app.services.report_generator import ReportGeneratorService
from app.db.models.assessment import Assessment
from app.db.models.finding import Finding
from app.db.models.report import Report
from app.domain.evidence import Verdict, Confidence

# Mocks
class MockAssessment:
    def __init__(self, id, client_id):
        self.id = id
        self.client_id = client_id
        self.selected_integrations = ["google_ads", "meta", "gtm", "ga4"]
        self.configuration = {}

class MockFinding:
    def __init__(self, f_id, obligation_id, verdict, confidence, severity, detail, human_review_required, sources):
        self.id = f_id
        self.assessment_id = uuid.uuid4()
        self.client_id = uuid.uuid4()
        self.obligation_id = obligation_id
        self.verdict = verdict
        self.confidence = confidence
        self.severity = severity
        self.detail = detail
        self.human_review_required = human_review_required
        self.sources = sources
        self.evidence_details = []
        self.reason = "Mock reason"


@pytest.mark.asyncio
async def test_finding_filtering_by_source():
    """Test that findings are filtered strictly by source, avoiding cross-contamination."""
    
    mock_session = AsyncMock(spec=AsyncSession)
    client_id = uuid.uuid4()
    assessment_id = uuid.uuid4()
    obl_id = uuid.uuid4()
    
    # 1. Setup mock assessment
    mock_assessment = MockAssessment(assessment_id, client_id)
    
    # 2. Setup mock findings
    # Google Ads only finding
    finding_gads = MockFinding(
        uuid.uuid4(), obl_id, Verdict.OBSERVED, Confidence.HIGH, "high", "Gads finding", False, ["google_ads:customer"]
    )
    # Meta only finding
    finding_meta = MockFinding(
        uuid.uuid4(), obl_id, Verdict.NOT_OBSERVED, Confidence.HIGH, "low", "Meta finding", False, ["meta:pixel"]
    )
    # Shared obligation but GTM only
    finding_gtm = MockFinding(
        uuid.uuid4(), obl_id, Verdict.OBSERVED, Confidence.MEDIUM, "medium", "GTM finding", False, ["gtm:tag"]
    )
    # Multi-source finding (Reconciliation)
    finding_multi = MockFinding(
        uuid.uuid4(), obl_id, Verdict.NOT_OBSERVED, Confidence.LOW, "high", "Multi finding", True, ["gtm:tag", "ga4:property"]
    )
    
    findings = [finding_gads, finding_meta, finding_gtm, finding_multi]
    
    # Mocking session.execute to return our data
    def mock_execute(stmt, *args, **kwargs):
        class MockResult:
            def __init__(self, data):
                self.data = data
            def scalars(self):
                class ScalarProxy:
                    def all(self_proxy):
                        return self.data
                    def first(self_proxy):
                        return self.data[0] if self.data else None
                return ScalarProxy()
            def scalar_one_or_none(self):
                return self.data[0] if self.data else None
                
        stmt_str = str(stmt).lower()
        
        if "reconciliation" in stmt_str:
            return MockResult([]) # No recon objects for this test
        elif "reports" in stmt_str:
            return MockResult([]) # For the existing report check
        elif "findings" in stmt_str:
            return MockResult(findings)
        elif "assessments" in stmt_str:
            return MockResult([mock_assessment])
        elif "obligations" in stmt_str:
            return MockResult([]) # Empty obl map
        elif "connections" in stmt_str:
            return MockResult([]) # No active connections
        elif "reports" in stmt_str:
            return MockResult([]) # For the existing report check
            
        return MockResult([])
        
    mock_session.execute.side_effect = mock_execute
    mock_session.get.return_value = mock_assessment

    service = ReportGeneratorService(mock_session, client_id)
    
    # Generate report doesn't actually return the payload, it saves it and returns Report object
    # We will just patch Path.write_text to capture the JSON
    with pytest.MonkeyPatch.context() as m:
        captured_data = {}
        def mock_write_text(self, text, encoding=None):
            # Capture the JSON written
            captured_data[self.name] = text
            
        m.setattr("pathlib.Path.write_text", mock_write_text)
        
        await service.generate_report(assessment_id)
        
        import json
        
        # Verify the primary combined report
        combined_filename = f"{assessment_id}.json"
        assert combined_filename in captured_data
        
        report_json = json.loads(captured_data[combined_filename])
        
        ind_reports = report_json.get("individual_reports", {})
        
        # 1. Google Ads finding must ONLY appear in Google Ads report
        gads_report = ind_reports.get("google_ads", {})
        gads_finding_ids = [f["finding_id"] for f in gads_report.get("findings", [])]
        assert str(finding_gads.id) in gads_finding_ids
        assert str(finding_meta.id) not in gads_finding_ids # Meta finding does not appear in Google Ads report
        
        # 2. Meta finding must ONLY appear in Meta report
        meta_report = ind_reports.get("meta", {})
        meta_finding_ids = [f["finding_id"] for f in meta_report.get("findings", [])]
        assert str(finding_meta.id) in meta_finding_ids
        assert str(finding_gads.id) not in meta_finding_ids # Google Ads finding does not appear in Meta report
        
        # 3. GTM finding in GTM report
        gtm_report = ind_reports.get("gtm", {})
        gtm_finding_ids = [f["finding_id"] for f in gtm_report.get("findings", [])]
        assert str(finding_gtm.id) in gtm_finding_ids
        
        # 4. Multi-source findings should NOT appear in individual reports
        assert str(finding_multi.id) not in gtm_finding_ids
        assert str(finding_multi.id) not in [f["finding_id"] for f in ind_reports.get("ga4", {}).get("findings", [])]
        
        # But all findings MUST be in the combined report findings array
        all_finding_ids = [f["finding_id"] for f in report_json.get("findings", [])]
        assert str(finding_multi.id) in all_finding_ids
        assert str(finding_gads.id) in all_finding_ids
        assert str(finding_meta.id) in all_finding_ids

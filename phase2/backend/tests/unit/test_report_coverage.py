import pytest
import uuid
import json
from unittest.mock import AsyncMock

from sqlalchemy.ext.asyncio import AsyncSession
from app.services.report_generator import ReportGeneratorService
from app.db.models.assessment import Assessment
from app.db.models.obligation import Obligation

class MockAssessment:
    def __init__(self, id, client_id):
        self.id = id
        self.client_id = client_id
        # Both GTM and Meta are selected, but imagine no findings are found
        self.selected_integrations = ["gtm", "meta"]
        self.configuration = {}

class MockObligation:
    def __init__(self, obl_id, code, title, severity):
        self.id = obl_id
        self.code = code
        self.title = title
        self.dpdp_section = "section 1"
        self.severity = severity
        self.description = "Compliance info"

@pytest.mark.asyncio
async def test_zero_findings_shows_complete_coverage():
    """Test that an assessment with zero database findings returns exactly 0 findings."""
    
    mock_session = AsyncMock(spec=AsyncSession)
    client_id = uuid.uuid4()
    assessment_id = uuid.uuid4()
    
    mock_assessment = MockAssessment(assessment_id, client_id)
    
    obl_1 = MockObligation(uuid.uuid4(), "S6.consentGating", "Consent Gating", "high")
    obl_2 = MockObligation(uuid.uuid4(), "S9.sharing.audienceUpload", "Audience Upload", "medium")
    
    # Mocking session.execute
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
            return MockResult([]) 
        elif "reports" in stmt_str:
            return MockResult([]) 
        elif "findings" in stmt_str:
            # ZERO FINDINGS
            return MockResult([])
        elif "assessments" in stmt_str:
            return MockResult([mock_assessment])
        elif "obligations" in stmt_str:
            return MockResult([obl_1, obl_2])
        elif "connections" in stmt_str:
            return MockResult([]) # No active connections
            
        return MockResult([])
        
    mock_session.execute.side_effect = mock_execute
    mock_session.get.return_value = mock_assessment

    service = ReportGeneratorService(mock_session, client_id)
    
    with pytest.MonkeyPatch.context() as m:
        captured_data = {}
        def mock_write_text(self, text, encoding=None):
            captured_data[self.name] = text
            
        m.setattr("pathlib.Path.write_text", mock_write_text)
        
        # We also need to patch OBLIGATION_INTEGRATION_MAP so our test obligations are mapped
        from app.domain.obligations import OBLIGATION_INTEGRATION_MAP
        original_map = OBLIGATION_INTEGRATION_MAP.copy()
        try:
            OBLIGATION_INTEGRATION_MAP["S6.consentGating"] = ["gtm"]
            OBLIGATION_INTEGRATION_MAP["S9.sharing.audienceUpload"] = ["meta"]
            
            await service.generate_report(assessment_id)
        finally:
            # restore map
            OBLIGATION_INTEGRATION_MAP.clear()
            OBLIGATION_INTEGRATION_MAP.update(original_map)
        
        combined_filename = f"{assessment_id}.json"
        assert combined_filename in captured_data
        report_json = json.loads(captured_data[combined_filename])
        
        # Ensure findings array has exactly 0 records according to the new requirements
        assert len(report_json["findings"]) == 0
        
        verdicts = report_json["summary"]["verdicts"]
        assert verdicts["cannot_determine"] == 0
        assert verdicts["observed"] == 0
        assert verdicts["not_observed"] == 0
        
        # Verify assessment_outcome accurately reflects the zero findings
        outcome = report_json["summary"]["assessment_outcome"]
        assert "No issues found" in outcome
        
        # Individual report check: Unconnected integrations should be in the report but empty
        # wait! Are they in the report?
        # detected_ints = set(configured_ints). So gtm and meta WILL be in the report.
        gtm_rep = report_json["individual_reports"].get("gtm")
        assert gtm_rep is not None
        assert len(gtm_rep["findings"]) == 0
        assert "not connected" in gtm_rep["summary"]["assessment_outcome"]

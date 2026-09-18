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
    """Test that an assessment with zero database findings still shows all expected obligations."""
    
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
        
        # Ensure findings array has exactly 2 records despite db returning 0
        assert len(report_json["findings"]) == 2
        
        verdicts = report_json["summary"]["verdicts"]
        assert verdicts["cannot_determine"] == 2
        
        # Check output structure
        f_codes = [f["obligation_code"] for f in report_json["findings"]]
        assert "S6.consentGating" in f_codes
        assert "S9.sharing.audienceUpload" in f_codes
        
        for f in report_json["findings"]:
            assert f["verdict"] == "Cannot determine"
            assert "unassessed" in f["sources"][0]
            assert f["reason"] == "Required provider evidence unavailable. Connect the integration or verify resource access to resolve this."
            assert len(f["evidence_details"]) == 1
            assert f["evidence_details"][0]["operation"] == "unassessed"
            assert f["evidence_details"][0]["integration"] in ("gtm", "meta", "unknown")
            
        # Individual report check: GTM should have the consent gating obligation
        gtm_rep = report_json["individual_reports"].get("gtm")
        assert gtm_rep is not None
        assert len(gtm_rep["findings"]) == 1
        assert gtm_rep["findings"][0]["obligation_code"] == "S6.consentGating"

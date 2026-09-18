import os
import uuid
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app.services.pdf_renderer import PDFReportRenderer
from app.api.v1.assessments import get_assessment_report_pdf

def test_pdf_renderer_generates_valid_pdf(tmp_path):
    renderer = PDFReportRenderer()
    
    report_data = {
        "assessment_id": str(uuid.uuid4()),
        "generated_at": "2026-09-17T12:00:00Z",
        "connections": {
            "gtm": {
                "provider": "gtm",
                "resource_name": "Test Container",
                "resource_id": "GTM-TEST1234"
            }
        },
        "findings": [
            {
                "finding_id": str(uuid.uuid4()),
                "obligation_code": "S6.consentGating",
                "obligation_title": "Tags gated on consent",
                "verdict": "observed",
                "confidence": "high",
                "severity": "high",
                "detail": "Tag fired without consent.",
                "compliance_implication": "Pending legal review.",
                "evidence_details": [
                    {
                        "source": "gtm",
                        "operation": "fetch_tags",
                        "detail": "Meta Pixel tag",
                        "observed_state": "No consent settings",
                        "expected_state": "Requires ad_storage"
                    }
                ]
            }
        ]
    }
    
    output_file = tmp_path / "test_report.pdf"
    
    # Generate the PDF
    result_path = renderer.generate(report_data, str(output_file))
    
    # Assert physical file exists
    assert os.path.exists(result_path)
    
    # Assert it's a PDF
    with open(result_path, "rb") as f:
        header = f.read(4)
        assert header == b"%PDF"
        
        # Read the rest to check if our evidence string is inside (in PDF syntax, text might be compressed or encoded, but simple strings sometimes appear, or we just check size)
        f.seek(0)
        content = f.read()
        assert len(content) > 1000  # Should be a decent sized file

@pytest.mark.asyncio
async def test_pdf_download_endpoint_mocked():
    # Simple test to verify the endpoint returns FileResponse when PDF exists
    from fastapi.responses import FileResponse
    from app.db.models.report import Report, ReportStatus
    from unittest.mock import MagicMock
    
    # Mock dependencies
    mock_session = AsyncMock()
    
    mock_report = Report(
        assessment_id=uuid.uuid4(),
        status=ReportStatus.COMPLETED,
        format="pdf",
        artifact_ref="dummy_path.pdf"
    )
    
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_report
    mock_session.execute.return_value = mock_result
    
    mock_auth = MagicMock()
    mock_auth.get_accessible_client_ids.return_value = [str(uuid.uuid4())]
    
    with patch('pathlib.Path') as mock_path:
        mock_path_inst = mock_path.return_value
        mock_path_inst.exists.return_value = True
        
        # We also mock the Assessment check
        mock_ass_result = MagicMock()
        mock_ass = MagicMock()
        mock_ass.client_id = uuid.uuid4()
        mock_ass_result.scalar_one_or_none.return_value = mock_ass
        
        # We need to distinguish session.execute calls
        async def mock_execute(stmt):
            stmt_str = str(stmt).lower()
            if "assessment " in stmt_str or "assessments" in stmt_str:
                return mock_ass_result
            return mock_result
            
        mock_session.execute.side_effect = mock_execute
        
        response = await get_assessment_report_pdf(
            assessment_id=uuid.uuid4(),
            integration=None,
            autoprint=False,
            auth=mock_auth,
            session=mock_session
        )
        
        assert isinstance(response, FileResponse)
        assert response.media_type == "application/pdf"

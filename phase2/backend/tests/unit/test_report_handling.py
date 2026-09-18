import pytest
import uuid
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

from fastapi import HTTPException
from fastapi.responses import JSONResponse, FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.assessments import get_assessment_report_json, download_assessment_report
from app.auth.dependencies import AuthContext
from app.db.models.assessment import Assessment
from app.db.models.report import Report, ReportStatus


@pytest.fixture
def mock_auth():
    client_id = uuid.uuid4()
    return AuthContext(
        user_id=uuid.uuid4(),
        identity="test@example.com",
        client_roles={str(client_id): "admin"}
    ), client_id


@pytest.fixture
def temp_report_file(tmp_path):
    assessment_id = uuid.uuid4()
    report_data = {
        "combined_report": {"title": "Combined"},
        "individual_reports": {
            "gtm": {"title": "GTM Report"}
        }
    }
    report_file = tmp_path / f"{assessment_id}.json"
    report_file.write_text(json.dumps(report_data))
    
    gtm_file = tmp_path / f"{assessment_id}_gtm.json"
    gtm_file.write_text(json.dumps(report_data["individual_reports"]["gtm"]))
    
    return assessment_id, str(report_file)


@pytest.mark.asyncio
async def test_get_assessment_report_json_existing_artifact(mock_auth, temp_report_file):
    """Test reading the report JSON via API uses the existing artifact_ref."""
    auth_ctx, client_id = mock_auth
    mock_session = AsyncMock(spec=AsyncSession)
    
    assessment_id, report_file_path = temp_report_file
    
    # Mock Assessment Query
    mock_assessment = Assessment(id=assessment_id, client_id=client_id)
    
    # Mock Report Query
    mock_report = Report(
        id=uuid.uuid4(),
        assessment_id=assessment_id,
        status=ReportStatus.COMPLETED,
        artifact_ref=report_file_path
    )
    
    # Setup session.execute to return Assessment then Report
    def mock_execute_side_effect(stmt, *args, **kwargs):
        mock_result = MagicMock()
        if "assessments" in str(stmt):
            mock_result.scalar_one_or_none.return_value = mock_assessment
        elif "reports" in str(stmt):
            mock_result.scalar_one_or_none.return_value = mock_report
            mock_result.scalars().first.return_value = mock_report
        return mock_result
        
    mock_session.execute.side_effect = mock_execute_side_effect

    # Call the json endpoint
    response = await get_assessment_report_json(
        assessment_id=assessment_id,
        integration=None,
        auth=auth_ctx,
        session=mock_session
    )
    
    assert isinstance(response, JSONResponse)
    body = json.loads(response.body)
    assert body["title"] == "Combined"
    

@pytest.mark.asyncio
async def test_download_assessment_report_returns_file(mock_auth, temp_report_file):
    """Test the download endpoint returns a FileResponse directly from artifact_ref."""
    auth_ctx, client_id = mock_auth
    mock_session = AsyncMock(spec=AsyncSession)
    
    assessment_id, report_file_path = temp_report_file
    
    mock_assessment = Assessment(id=assessment_id, client_id=client_id)
    mock_report = Report(
        id=uuid.uuid4(),
        assessment_id=assessment_id,
        status=ReportStatus.COMPLETED,
        artifact_ref=report_file_path
    )
    
    def mock_execute_side_effect(stmt, *args, **kwargs):
        mock_result = MagicMock()
        if "assessments" in str(stmt):
            mock_result.scalar_one_or_none.return_value = mock_assessment
        elif "reports" in str(stmt):
            mock_result.scalar_one_or_none.return_value = mock_report
            mock_result.scalars().first.return_value = mock_report
        return mock_result
        
    mock_session.execute.side_effect = mock_execute_side_effect

    # Call the download endpoint for a specific integration
    response = await download_assessment_report(
        assessment_id=assessment_id,
        integration="gtm",
        auth=auth_ctx,
        session=mock_session
    )
    
    assert isinstance(response, FileResponse)
    assert str(Path(response.path)) == str(Path(report_file_path).parent / f"{assessment_id}_gtm.json")
    assert response.filename == f"dpdp_report_{assessment_id}_gtm.json"


@pytest.mark.asyncio
async def test_report_generation_triggered_if_missing(mock_auth, tmp_path):
    """Test generation is triggered if artifact_ref is missing or file doesn't exist."""
    auth_ctx, client_id = mock_auth
    mock_session = AsyncMock(spec=AsyncSession)
    
    assessment_id = uuid.uuid4()
    mock_assessment = Assessment(id=assessment_id, client_id=client_id)
    
    # Initial query returns a Report with no artifact_ref
    mock_report_missing = Report(
        id=uuid.uuid4(),
        assessment_id=assessment_id,
        status=ReportStatus.COMPLETED,
        artifact_ref=None
    )
    
    # After generation, we mock a successful report file
    report_file = tmp_path / "generated_report.json"
    report_file.write_text(json.dumps({"combined_report": {"title": "Newly Generated"}}))
    
    mock_report_generated = Report(
        id=uuid.uuid4(),
        assessment_id=assessment_id,
        status=ReportStatus.COMPLETED,
        artifact_ref=str(report_file)
    )
    
    execute_calls = 0
    def mock_execute_side_effect(stmt, *args, **kwargs):
        nonlocal execute_calls
        execute_calls += 1
        mock_result = MagicMock()
        if "assessments" in str(stmt):
            mock_result.scalar_one_or_none.return_value = mock_assessment
        elif "reports" in str(stmt):
            if execute_calls <= 2:
                mock_result.scalar_one_or_none.return_value = mock_report_missing
                mock_result.scalars().first.return_value = mock_report_missing
            else:
                mock_result.scalar_one_or_none.return_value = mock_report_generated
                mock_result.scalars().first.return_value = mock_report_generated
        return mock_result
        
    mock_session.execute.side_effect = mock_execute_side_effect

    with patch("app.services.report_generator.ReportGeneratorService.generate_report") as mock_gen:
        # Call the endpoint
        response = await get_assessment_report_json(
            assessment_id=assessment_id,
            integration=None,
            auth=auth_ctx,
            session=mock_session
        )
        
        mock_gen.assert_called_once_with(assessment_id)
        
    assert isinstance(response, JSONResponse)
    body = json.loads(response.body)
    assert body["title"] == "Newly Generated"

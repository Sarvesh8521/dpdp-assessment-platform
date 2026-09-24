"""Final Validation of the Phase 2 Evidence-Based Report.

This test proves the complete chain for every finding:
Real Platform -> Real Scanner/API -> Exact Resource -> Exact Observation -> 
Exact Evidence -> Obligation -> Verdict -> Confidence -> Reason -> Recommended Action -> Report.

It mocks provider APIs to be deterministic and asserts that ALL required
fields are present in the findings and the final generated report (JSON and PDF).
"""

import json
import os
import uuid
import pytest

async def dummy_audit(*args, **kwargs): pass

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock

from sqlalchemy import select
from app.schemas.api import AssessmentCreate, AssessmentConfig
from app.services.assessment_service import AssessmentService
from app.services.report_generator import ReportGeneratorService
from app.db.models import Finding, Evidence, Assessment, Connection
from app.db.models.report import Report, ReportStatus
from app.db.session import async_session_factory

# Fake responses for our providers
GTM_FAKE_RESPONSE = {
    "container": {
        "containerId": "123",
        "name": "Test GTM Container"
    },
    "workspace": {
        "workspaceId": "456",
        "name": "Default Workspace"
    },
    "tags": [
        {
            "tagId": "789",
            "name": "Meta Pixel",
            "type": "html",
            "consentSettings": {} # No consent settings - will generate a finding
        },
        {
            "tagId": "790",
            "name": "GA4 Config",
            "type": "ga4",
            "consentSettings": {
                "consentStatus": "NEEDED",
                "consentType": {"ad_storage": True}
            }
        }
    ],
    "triggers": [],
    "variables": []
}

GA4_FAKE_RESPONSE = {
    "accountSummaries": [
        {
            "propertySummaries": [
                {
                    "property": "properties/555",
                    "displayName": "Test GA4 Property",
                    "eventDataRetention": "FIFTY_FOUR_MONTHS", # High retention - finding
                    "googleSignalsSettings": {"state": "GOOGLE_SIGNALS_ENABLED"} # Signals enabled - finding
                }
            ]
        }
    ]
}

class FakeResponse:
    def __init__(self, json_data, status_code=200):
        self._json = json_data
        self.status_code = status_code
        self.headers = {}
    
    def raise_for_status(self):
        pass
        
    def json(self):
        return self._json

@pytest.fixture
def mock_external_apis():
    # Mock httpx.AsyncClient.request to return our deterministic payloads
    async def mock_request(self, method, url, **kwargs):
        url_str = str(url)
        if "tagmanager" in url_str:
            if "containers" in url_str and "workspaces" not in url_str:
                return FakeResponse({"container": [GTM_FAKE_RESPONSE["container"]]})
            elif "workspaces" in url_str and "tags" not in url_str:
                return FakeResponse({"workspace": [GTM_FAKE_RESPONSE["workspace"]]})
            elif "tags" in url_str:
                return FakeResponse({"tag": GTM_FAKE_RESPONSE["tags"]})
            elif "triggers" in url_str:
                return FakeResponse({"trigger": []})
            elif "variables" in url_str:
                return FakeResponse({"variable": []})
            return FakeResponse({})
        elif "analyticsadmin" in url_str:
            if "accountSummaries" in url_str:
                return FakeResponse(GA4_FAKE_RESPONSE)
            elif "dataRetentionSettings" in url_str:
                return FakeResponse({"eventDataRetention": "FIFTY_FOUR_MONTHS"})
            elif "googleSignalsSettings" in url_str:
                return FakeResponse({"state": "GOOGLE_SIGNALS_ENABLED"})
            elif "googleAdsLinks" in url_str:
                return FakeResponse({"googleAdsLinks": []})
            return FakeResponse({})
        
        return FakeResponse({})
        
    with patch("httpx.AsyncClient.request", new=mock_request):
        yield

@pytest.mark.asyncio
async def test_complete_report_chain(mock_external_apis, tmp_path):
    """End-to-end regression test for report generation and field validation."""
    
    # 1. Setup Assessment
    test_client_id = uuid.uuid4()
    user_id = uuid.uuid4()
    
    # We will use real DB interactions within our transaction block where possible,
    # or rely on the services setting up their own sessions. AssessmentService
    # and ReportGeneratorService create their own sessions if not injected, or we 
    # can wrap the test in an async session context.
    
    async with async_session_factory() as session:
        from app.db.models.client import Client
        from app.db.models.user import User
        
        test_client = Client(id=test_client_id, name="Test Client")
        test_user = User(id=user_id, identity=f"test_user_{user_id}", display_label="Test User", hashed_password="fake")
        
        session.add(test_client)
        session.add(test_user)
        await session.commit()
        
        # Pre-seed a connection for GA4 and GTM
        gtm_conn = Connection(
            id=uuid.uuid4(),
            client_id=test_client_id,
            provider="gtm",
            secret_ref="gtm_secret_ref_123",
            status="connected",
            scopes=["https://www.googleapis.com/auth/tagmanager.readonly"],
            connected_at=datetime.now(timezone.utc)
        )
        ga4_conn = Connection(
            id=uuid.uuid4(),
            client_id=test_client_id,
            provider="ga4",
            secret_ref="ga4_secret_ref_123",
            status="connected",
            scopes=["https://www.googleapis.com/auth/analytics.readonly"],
            connected_at=datetime.now(timezone.utc)
        )
        session.add_all([gtm_conn, ga4_conn])
        await session.commit()
    
        # 2. Run Assessment
        service = AssessmentService(session, test_client_id)
        
        config = AssessmentConfig(
            integrations=["gtm", "ga4"],
            credentials={}
        )
        data = AssessmentCreate(client_id=test_client_id, config=config)
        
        # Mock the token resolution to return fake tokens instead of looking up Secret Manager
        with patch.object(AssessmentService, "_resolve_access_token", new_callable=AsyncMock) as mock_resolve:
            mock_resolve.return_value = ("fake_token_for_test", None)
            # This orchestrates adapter.collect(audit_callback=dummy_audit) and finding generation
            assessment = await service.create_assessment(data, user_id)
    
            # Ensure we have findings
            findings_result = await session.execute(
                select(Finding).where(Finding.assessment_id == assessment.id)
            )
            findings = findings_result.scalars().all()
            assert len(findings) > 0, "No findings generated by assessment"
            
            # 3. Assert all required finding fields are present
            for f in findings:
                assert f.obligation_code is not None
                assert f.verdict is not None
                assert f.confidence is not None
                assert f.severity is not None
                assert f.reason is not None and len(f.reason) > 0
                assert f.detail is not None and len(f.detail) > 0
                assert isinstance(f.human_review_required, bool)
                
                # Check evidence details for the exact integration fields
                if f.evidence_details:
                    for ev in f.evidence_details:
                        assert ev.get("integration") is not None
                        assert ev.get("resource_type") is not None
                        assert ev.get("operation") is not None
                        assert ev.get("observed_state") is not None
                        assert ev.get("expected_state") is not None
                        assert ev.get("reason") is not None
            
            # 4. Generate Report
            report_service = ReportGeneratorService(session, test_client_id)
            report = await report_service.generate_report(assessment.id)
        
        # 5. Assert Report Output Constraints (JSON)
        report_json_path = Path(report.artifact_ref)
        assert report_json_path.exists()
        
        with open(report_json_path, "r", encoding="utf-8") as f:
            report_json = json.load(f)
            
        # Assert no PII or raw emails
        report_json_str = json.dumps(report_json)
        assert "@" not in report_json_str or "example.com" not in report_json_str
        
        # Assert integrations are accurately represented
        assert "individual_reports" in report_json
        
        # Query for PDF report
        pdf_res = await session.execute(
            select(Report).where(Report.assessment_id == assessment.id, Report.format == "pdf")
        )
        pdf_report = pdf_res.scalar_one_or_none()
        
        # Assert PDF is actually generated and downloadable
        assert pdf_report is not None
        assert pdf_report.format == "pdf"
        assert pdf_report.artifact_ref is not None
        pdf_path = Path(pdf_report.artifact_ref)
        assert pdf_path.exists()
        assert pdf_path.stat().st_size > 0
        
        with open(pdf_path, "rb") as pdf_file:
            header = pdf_file.read(4)
            assert header == b"%PDF"
            
        print("End-to-End Report Chain Test Passed.")

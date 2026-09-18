import asyncio
import json
import uuid
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch, AsyncMock

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db.session import async_session_factory
from app.schemas.api import AssessmentCreate, AssessmentConfig
from app.services.assessment_service import AssessmentService
from app.services.report_generator import ReportGeneratorService
from app.db.models import Finding, Evidence, Assessment, Connection
from app.db.models.report import Report
from sqlalchemy import select

from tests.acceptance.test_final_report_chain import mock_external_apis, GTM_FAKE_RESPONSE, GA4_FAKE_RESPONSE, FakeResponse

async def run():
    test_client_id = uuid.uuid4()
    user_id = uuid.uuid4()
    
    async with async_session_factory() as session:
        from app.db.models.client import Client
        from app.db.models.user import User
        
        test_client = Client(id=test_client_id, name="Test Client")
        test_user = User(id=user_id, identity=f"test_user_{user_id}", display_label="Test User", hashed_password="fake")
        
        session.add(test_client)
        session.add(test_user)
        await session.commit()
        
        # Add connections for all 7 integrations to simulate a full scan
        integrations = ["gtm", "ga4", "google_ads", "meta", "search_console", "github", "cmp"]
        conns = []
        for i in integrations:
            conns.append(Connection(
                id=uuid.uuid4(),
                client_id=test_client_id,
                provider=i,
                secret_ref=f"{i}_secret",
                status="connected",
                scopes=["fake_scope"],
                connected_at=datetime.now(timezone.utc)
            ))
        session.add_all(conns)
        await session.commit()

        service = AssessmentService(session, test_client_id)
        config = AssessmentConfig(
            integrations=integrations,
            credentials={},
            github_repos=["test/repo"],
            search_console_properties=["example.com"],
            marketing_platforms=["Meta", "Google Ads"]
        )
        data = AssessmentCreate(client_id=test_client_id, config=config)
        
        # Patch external requests. Use a wide-net mock that returns mostly empty to avoid hanging,
        # but uses the GTM/GA4 mocks from the test suite where available.
        async def wide_mock_request(self, method, url, **kwargs):
            url_str = str(url)
            if "tagmanager" in url_str:
                if "containers" in url_str and "workspaces" not in url_str: return FakeResponse({"container": [GTM_FAKE_RESPONSE["container"]]})
                if "workspaces" in url_str and "tags" not in url_str: return FakeResponse({"workspace": [GTM_FAKE_RESPONSE["workspace"]]})
                if "tags" in url_str: return FakeResponse({"tag": GTM_FAKE_RESPONSE["tags"]})
                if "triggers" in url_str: return FakeResponse({"trigger": []})
                if "variables" in url_str: return FakeResponse({"variable": []})
            elif "analyticsadmin" in url_str:
                if "accountSummaries" in url_str: return FakeResponse(GA4_FAKE_RESPONSE)
                if "dataRetentionSettings" in url_str: return FakeResponse({"eventDataRetention": "FIFTY_FOUR_MONTHS"})
                if "googleSignalsSettings" in url_str: return FakeResponse({"state": "GOOGLE_SIGNALS_ENABLED"})
                if "googleAdsLinks" in url_str: return FakeResponse({"googleAdsLinks": []})
            return FakeResponse({})

        with patch("httpx.AsyncClient.request", new=wide_mock_request):
            with patch.object(AssessmentService, "_resolve_access_token", new_callable=AsyncMock) as mock_resolve:
                mock_resolve.return_value = ("fake_token", None)
                assessment = await service.create_assessment(data, user_id)
        
        # Output Metrics
        print(f"--- ASSESSMENT METRICS ---")
        
        # Report & PDF
        report_service = ReportGeneratorService(session, test_client_id)
        report = await report_service.generate_report(assessment.id)
        
        pdf_res = await session.execute(select(Report).where(Report.assessment_id == assessment.id, Report.format == "pdf"))
        pdf_report = pdf_res.scalar_one_or_none()
        print(f"Report JSON Path: {report.artifact_ref}")
        print(f"Report PDF Path: {pdf_report.artifact_ref if pdf_report else 'MISSING'}")
        
        # Load JSON to get the exact counts mapped in the final report
        with open(report.artifact_ref, "r") as f:
            rdata = json.load(f)
            
        print("\n--- FINAL REPORT DATA ---")
        print(f"Total Integrations Configured: {len(rdata.get('configured_integrations', []))}")
        print(f"Reconciliations Run: {len(rdata.get('reconciliations', []))}")
        
        findings = rdata.get('findings', [])
        cannot_determine = [f for f in findings if f['verdict'].lower() == 'cannot_determine']
        
        print(f"\nTotal Findings: {len(findings)}")
        print(f"Cannot Determine Items: {len(cannot_determine)}")
        
        by_int = {}
        for f in findings:
            for s in f.get('sources', []):
                prefix = s.split(':')[0]
                by_int[prefix] = by_int.get(prefix, 0) + 1
                
        print("\nFindings by Source Integration:")
        for k, v in by_int.items():
            print(f"- {k}: {v}")

        # Evidences
        ev_result = await session.execute(select(Evidence).where(Evidence.assessment_id == assessment.id))
        evidences = ev_result.scalars().all()
        
        ev_by_int = {}
        for ev in evidences:
            prefix = ev.source.split(':')[0]
            ev_by_int[prefix] = ev_by_int.get(prefix, 0) + 1
            
        print("\nEvidence Items by Source:")
        for k, v in ev_by_int.items():
            print(f"- {k}: {v}")
            
if __name__ == "__main__":
    asyncio.run(run())

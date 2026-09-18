import json
import uuid
import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock

from app.db.models.assessment import Assessment, AssessmentStatus
from app.db.models.client import Client
from app.db.models.connection import Connection, ConnectionStatus
from app.db.models.finding import Finding, FindingSeverity, FindingStatus
from app.db.models.obligation import Obligation
from app.db.models.reconciliation_result import ReconciliationResult, ReconciliationOutcome
from app.services.report_generator import ReportGeneratorService
from app.domain.evidence import Verdict, Confidence


@pytest.fixture
def mock_session():
    session = AsyncMock()
    return session


def _create_mock_assessment():
    return Assessment(
        id=uuid.uuid4(),
        client_id=uuid.uuid4(),
        status=AssessmentStatus.COMPLETED,
        selected_integrations=["gtm", "meta", "google_ads", "github"],
    )


def _create_mock_obligation(code, title):
    return Obligation(
        id=uuid.uuid4(),
        code=code,
        title=title,
        severity=FindingSeverity.MEDIUM,
    )


def _create_mock_finding(assessment, obligation, verdict, source, detail="test"):
    return Finding(
        id=uuid.uuid4(),
        assessment_id=assessment.id,
        client_id=assessment.client_id,
        obligation_id=obligation.id,
        verdict=verdict,
        confidence=Confidence.HIGH,
        severity=FindingSeverity.MEDIUM,
        detail=detail,
        status=FindingStatus.OPEN,
        human_review_required=False,
        sources=[source]
    )


def _create_mock_reconciliation(assessment):
    return ReconciliationResult(
        id=uuid.uuid4(),
        assessment_id=assessment.id,
        rule_id="R1",
        source_a="gtm:container",
        source_b="google_ads:customer",
        outcome=ReconciliationOutcome.CONTRADICTION,
        detail="GTM has pixel, Google Ads does not",
    )


@pytest.mark.asyncio
async def test_combined_report_groups_by_integration(mock_session, monkeypatch):
    """
    Test that the combined report groups findings by integration:
    - GTM findings only in GTM section
    - Ads findings only in Ads section
    - Issues are separated from Evaluated Obligations
    """
    assessment = _create_mock_assessment()
    client_id = assessment.client_id
    
    gtm_obl = _create_mock_obligation("S6.consentGating", "Consent Gating")
    meta_obl = _create_mock_obligation("S9.sharing.audienceUpload", "Audience Upload")
    ads_obl = _create_mock_obligation("S9.sharing.googleAds", "Ads Sharing")
    github_obl = _create_mock_obligation("S1.inventory", "GitHub SDKs")
    
    # GTM has an issue (OBSERVED)
    gtm_finding = _create_mock_finding(assessment, gtm_obl, Verdict.OBSERVED, "gtm:container_123")
    # Meta has an evaluated obligation, no issue (NOT_OBSERVED)
    meta_finding = _create_mock_finding(assessment, meta_obl, Verdict.NOT_OBSERVED, "meta:business_456")
    # Ads has CANNOT_DETERMINE (No issue but cannot determine)
    ads_finding = _create_mock_finding(assessment, ads_obl, Verdict.CANNOT_DETERMINE, "google_ads:customer_789")
    # GitHub has OBSERVED
    github_finding = _create_mock_finding(assessment, github_obl, Verdict.OBSERVED, "github:repo_1")
    
    recon = _create_mock_reconciliation(assessment)

    class MockResult:
        def __init__(self, items):
            self.items = items
        def scalars(self):
            class ScalarResult:
                def all(self_inner):
                    return self.items
            return ScalarResult()
        def scalar_one_or_none(self):
            return self.items[0] if self.items else None

    def mock_execute(stmt, *args, **kwargs):
        s = str(stmt).lower()
        if "reconciliation" in s:
            return MockResult([recon])
        if "reports" in s:
            return MockResult([])
        if "findings" in s:
            return MockResult([gtm_finding, meta_finding, ads_finding, github_finding])
        if "connections" in s:
            return MockResult([
                Connection(provider="gtm", resource_id="GTM-123", status=ConnectionStatus.CONNECTED, client_id=client_id),
                Connection(provider="meta", resource_id="BUS-456", status=ConnectionStatus.CONNECTED, client_id=client_id),
                Connection(provider="google_ads", resource_id="ADS-789", status=ConnectionStatus.CONNECTED, client_id=client_id),
                Connection(provider="github", resource_id="test/repo", status=ConnectionStatus.CONNECTED, client_id=client_id),
            ])
        if "obligations" in s:
            return MockResult([gtm_obl, meta_obl, ads_obl, github_obl])
        return MockResult([])

    mock_session.execute.side_effect = mock_execute
    mock_session.get.return_value = assessment
    
    service = ReportGeneratorService(mock_session, client_id)
    
    captured_files = {}
    def mock_write_text(self, text, encoding=None):
        captured_files[self.name] = text
        
    monkeypatch.setattr("pathlib.Path.write_text", mock_write_text)
    
    # Render PDF mock
    with pytest.MonkeyPatch.context() as m:
        m.setattr("app.services.report_generator.PDFReportRenderer.generate", lambda self, data, path: path)
        await service.generate_report(assessment.id)
        
    combined_file = f"{assessment.id}.json"
    assert combined_file in captured_files
    data = json.loads(captured_files[combined_file])
    
    combined = data["combined_report"]
    integration_reports = {r["integration"]: r for r in combined["integration_reports"]}
    
    # 1. GTM Assertions
    gtm_rep = integration_reports["gtm"]
    assert len(gtm_rep["issues"]) == 1
    assert len(gtm_rep["evaluated_obligations"]) == 0
    assert gtm_rep["issues"][0]["obligation_code"] == "S6.consentGating"
    assert "gtm:container_123" in gtm_rep["issues"][0]["sources"]
    
    # 2. Meta Assertions (No issues, 1 evaluated)
    meta_rep = integration_reports["meta"]
    assert len(meta_rep["issues"]) == 0
    assert len(meta_rep["evaluated_obligations"]) == 1
    assert meta_rep["evaluated_obligations"][0]["obligation_code"] == "S9.sharing.audienceUpload"
    assert "meta:business_456" in meta_rep["evaluated_obligations"][0]["sources"]
    
    # 3. Google Ads Assertions (Cannot determine -> evaluated)
    ads_rep = integration_reports["google_ads"]
    assert len(ads_rep["issues"]) == 0
    assert len(ads_rep["evaluated_obligations"]) == 1
    assert ads_rep["evaluated_obligations"][0]["obligation_code"] == "S9.sharing.googleAds"
    assert "google_ads:customer_789" in ads_rep["evaluated_obligations"][0]["sources"]
    
    # 4. GitHub Assertions
    github_rep = integration_reports["github"]
    assert len(github_rep["issues"]) == 1
    assert len(github_rep["evaluated_obligations"]) == 0
    assert github_rep["issues"][0]["obligation_code"] == "S1.inventory"
    
    # 5. Shared Reconciliation Assertions
    recon_out = combined["cross_source_reconciliation"][0]
    assert recon_out["source_a"] == "gtm:container"
    assert recon_out["source_b"] == "google_ads:customer"

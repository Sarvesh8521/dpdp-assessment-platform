import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch, MagicMock

import pytest

from app.db.models.client import Client
from app.db.models.assessment import Assessment
from app.db.models.connection import Connection, ConnectionStatus
from app.schemas.api import AssessmentCreate, AssessmentConfig
from app.services.assessment_service import AssessmentService
from app.integrations.base.http_client import AuditedHTTPClient, ExternalAPIError
from app.domain.evidence import Verdict


@pytest.fixture
def mock_session():
    session = AsyncMock()
    session.flush = AsyncMock()
    
    async def mock_get(model, id):
        if model == Client:
            return Client(id=id)
        if model == Assessment:
            return Assessment(id=id, client_id=id)
        return None
    session.get.side_effect = mock_get
    
    return session


@pytest.fixture
def mock_secret_manager():
    with patch("app.services.assessment_service.get_secret_manager") as mock_gsm:
        mock_sm = AsyncMock()
        mock_sm.get_secret.return_value = '{"access_token": "fake-token"}'
        mock_gsm.return_value = mock_sm
        yield mock_sm


def _setup_mock_connection(mock_session, provider, resource_id="owner/repo"):
    mock_conn = Connection(
        id=uuid.uuid4(),
        client_id=uuid.uuid4(),
        provider=provider,
        status=ConnectionStatus.CONNECTED,
        resource_id=resource_id,
        resource_name="Test Repository",
        secret_ref="fake-secret-ref",
    )
    mock_result = MagicMock()
    mock_result.scalars.return_value.first.return_value = mock_conn
    mock_session.execute.return_value = mock_result
    return mock_conn


@pytest.mark.asyncio
@patch("app.services.report_generator.ReportGeneratorService.generate_report", new_callable=AsyncMock)
@patch("app.services.finding_service.FindingService.generate_findings_for_assessment", new_callable=AsyncMock)
@patch("app.services.assessment_service.run_reconciliation")
async def test_github_acceptance_success(
    mock_recon, mock_gen_findings, mock_gen_report, mock_session, mock_secret_manager
):
    """Test full GitHub flow where repository is successfully scanned."""
    # We must patch the HTTP client in the GitHub adapter
    import tarfile
    import tempfile
    from pathlib import Path
    
    class MockClient:
        async def download_file(self, endpoint, target_path, **kwargs):
            assert "owner/repo" in endpoint
            with tempfile.TemporaryDirectory() as tmpdir:
                repo_root = Path(tmpdir) / "test-repo"
                repo_root.mkdir()
                
                # Form field
                (repo_root / "Form.jsx").write_text('<input type="email" name="email" />')
                
                with tarfile.open(target_path, "w:gz") as tar:
                    tar.add(repo_root, arcname="test-repo")
                    
        async def close(self):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass
            
    _setup_mock_connection(mock_session, "github", resource_id="owner/repo")
    client_id = uuid.uuid4()
    class MockObligations(dict):
        def __contains__(self, key): return True
        def __getitem__(self, key): return key
    service = AssessmentService(mock_session, client_id)
    service._ensure_obligations_seeded = AsyncMock(return_value=MockObligations())
    
    data = AssessmentCreate(
        client_id=client_id,
        config=AssessmentConfig(integrations=["github"])
    )
    
    with patch("app.integrations.github_adapter.adapter.AuditedHTTPClient", return_value=MockClient()):
        await service.create_assessment(data, uuid.uuid4())
    
    from app.db.models.evidence import Evidence
    evidence_added = [
        call.args[0] for call in mock_session.add.call_args_list 
        if isinstance(call.args[0], Evidence)
    ]
    
    print(f"EVIDENCE ADDED SUCCESS: {[e.obligation_id for e in evidence_added]}")
    
    # Check that we found the form field
    form_evidence = next((e for e in evidence_added if e.obligation_id == "APP.forms.disclosure"), None)
    assert form_evidence is not None
    assert form_evidence.source == "github:owner/repo:Form.jsx"
    
    # Verdict is OBSERVED
    ev_verdict_val = form_evidence.verdict.value if hasattr(form_evidence.verdict, "value") else str(form_evidence.verdict)
    assert ev_verdict_val == "Observed"


@pytest.mark.asyncio
@patch("app.services.report_generator.ReportGeneratorService.generate_report", new_callable=AsyncMock)
@patch("app.services.finding_service.FindingService.generate_findings_for_assessment", new_callable=AsyncMock)
@patch("app.services.assessment_service.run_reconciliation")
async def test_github_acceptance_failure(
    mock_recon, mock_gen_findings, mock_gen_report, mock_session, mock_secret_manager
):
    """Test full GitHub flow where repository access fails (404/403)."""
    class MockFailingClient:
        async def download_file(self, endpoint, target_path, **kwargs):
            raise ExternalAPIError("Not Found", status_code=404, provider="github")
            
        async def close(self):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

    _setup_mock_connection(mock_session, "github", resource_id="owner/secret-repo")
    client_id = uuid.uuid4()
    class MockObligations(dict):
        def __contains__(self, key): return True
        def __getitem__(self, key): return key
    service = AssessmentService(mock_session, client_id)
    service._ensure_obligations_seeded = AsyncMock(return_value=MockObligations())
    
    data = AssessmentCreate(
        client_id=client_id,
        config=AssessmentConfig(integrations=["github"])
    )
    
    with patch("app.integrations.github_adapter.adapter.AuditedHTTPClient", return_value=MockFailingClient()):
        await service.create_assessment(data, uuid.uuid4())
        
    from app.db.models.evidence import Evidence
    evidence_added = [
        call.args[0] for call in mock_session.add.call_args_list 
        if isinstance(call.args[0], Evidence)
    ]
    
    print(f"EVIDENCE ADDED FAILURE: {[e.obligation_id for e in evidence_added]}")
    
    # We should have CANNOT_DETERMINE evidence for every obligation
    # Let's check for one like APP.sdk.consent
    sdk_evidence = next((e for e in evidence_added if e.obligation_id == "APP.sdk.consent"), None)
    assert sdk_evidence is not None
    assert sdk_evidence.source == "github:owner/secret-repo:error"
    
    ev_verdict_val = sdk_evidence.verdict.value if hasattr(sdk_evidence.verdict, "value") else str(sdk_evidence.verdict)
    assert ev_verdict_val == "Cannot determine"
    assert "Failed to access repository owner/secret-repo" in sdk_evidence.detail
    assert "Not Found" in sdk_evidence.detail

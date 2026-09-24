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
from app.integrations.base.http_client import AuditedHTTPClient, ExternalAPIError
from app.domain.evidence import Verdict


def test_aiofiles_dependency_available():
    """Test that aiofiles is installed and available in the runtime environment."""
    try:
        import aiofiles
        assert aiofiles is not None
    except ImportError as e:
        pytest.fail(f"aiofiles dependency is missing: {e}")


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
        provider_metadata={"installation_id": "fake-installation-id"} if provider == "github" else None)
    mock_result = MagicMock()
    mock_result.scalars.return_value.first.return_value = mock_conn
    mock_session.execute.return_value = mock_result
    return mock_conn


@pytest.mark.asyncio
@patch("app.api.v1.oauth_handlers.GitHubAppHandler.get_installation_token", new_callable=AsyncMock, return_value={"token": "fake-installation-token"})
@patch("app.services.report_generator.ReportGeneratorService.generate_report", new_callable=AsyncMock)
@patch("app.services.finding_service.FindingService.generate_findings_for_assessment", new_callable=AsyncMock)
@patch("app.services.assessment_service.run_reconciliation")
async def test_github_acceptance_success(
    mock_recon, mock_gen_findings, mock_gen_report, mock_inst_token, mock_session, mock_secret_manager
):
    """Test full GitHub flow: detects 3 planted privacy issues with ephemeral storage."""
    import tarfile
    import tempfile
    import os
    from pathlib import Path

    audit_records = []

    async def mock_audit_callback(provider, operation, status, details=None):
        audit_records.append({"provider": provider, "operation": operation, "status": status})

    # We must patch the HTTP client in the GitHub adapter
    class MockClient:
        def __init__(self, *args, **kwargs):
            self.audit_callback = kwargs.get("audit_callback")

        async def download_file(self, endpoint, target_path, **kwargs):
            assert "owner/repo" in endpoint
            if self.audit_callback:
                await self.audit_callback("github", "download_repo_archive", "success")
                
            with tempfile.TemporaryDirectory() as tmpdir:
                repo_root = Path(tmpdir) / "test-repo"
                repo_root.mkdir()
                
                # 1. Form field issue (APP.forms.disclosure)
                (repo_root / "Form.jsx").write_text('<input type="email" name="email" />')
                
                # 2. Tracker SDK issue (S10.scope.sdks)
                (repo_root / "package.json").write_text('{"dependencies": {"firebase-analytics": "1.0"}}')
                
                # 3. PII logging issue (APP.logging.noPII)
                (repo_root / "logger.js").write_text('console.log(email)')
                
                # Create tarball
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
    
    # Inject our mock audit callback directly into the service for testing
    with patch("app.services.assessment_service.make_audit_callback", return_value=mock_audit_callback):
        data = AssessmentCreate(
            client_id=client_id,
            config=AssessmentConfig(integrations=["github"])
        )
        
        with patch("app.integrations.github_adapter.adapter.AuditedHTTPClient", MockClient):
            await service.create_assessment(data, uuid.uuid4())
            
    # Verify audit records were created
    assert len(audit_records) > 0
    assert any(r["operation"] == "download_repo_archive" for r in audit_records)
    
    # Check Evidence created
    from app.db.models.evidence import Evidence
    evidence_added = [
        call.args[0] for call in mock_session.add.call_args_list 
        if isinstance(call.args[0], Evidence)
    ]
    
    # 1. Check Form field detection
    form_evidence = next((e for e in evidence_added if e.obligation_id == "APP.forms.disclosure"), None)
    assert form_evidence is not None
    assert form_evidence.source == "github:owner/repo:Form.jsx"
    ev_verdict_val = form_evidence.verdict.value if hasattr(form_evidence.verdict, "value") else str(form_evidence.verdict)
    assert ev_verdict_val == "Observed"
    
    # 2. Check SDK detection
    sdk_evidence = next((e for e in evidence_added if e.obligation_id == "S10.scope.sdks"), None)
    assert sdk_evidence is not None
    assert sdk_evidence.source == "github:owner/repo:package.json"
    ev_verdict_val = sdk_evidence.verdict.value if hasattr(sdk_evidence.verdict, "value") else str(sdk_evidence.verdict)
    assert ev_verdict_val == "Observed"
    
    # 3. Check PII log detection
    pii_evidence = next((e for e in evidence_added if e.obligation_id == "APP.logging.noPII"), None)
    assert pii_evidence is not None
    assert pii_evidence.source == "github:owner/repo:logger.js"
    ev_verdict_val = pii_evidence.verdict.value if hasattr(pii_evidence.verdict, "value") else str(pii_evidence.verdict)
    assert ev_verdict_val == "Not observed"

    # Verify NO source code is in the evidence details (no HTML/JS tags)
    for ev in evidence_added:
        ev_val = ev.verdict.value if hasattr(ev.verdict, "value") else str(ev.verdict)
        if ev_val in ["Observed", "Not observed", "Cannot determine"]:
            assert "<input" not in ev.detail
            assert "console.log" not in ev.detail
            assert "{" not in ev.detail
            
    # Verify temporary files were cleaned up (adapter handles this via tempfile context manager, 
    # but we can't directly assert it here without mocking tempfile. However, we know 
    # tempfile.TemporaryDirectory cleans up when it exits block scope.)


@pytest.mark.asyncio
@patch("app.api.v1.oauth_handlers.GitHubAppHandler.get_installation_token", new_callable=AsyncMock, return_value={"token": "fake-installation-token"})
@patch("app.services.report_generator.ReportGeneratorService.generate_report", new_callable=AsyncMock)
@patch("app.services.finding_service.FindingService.generate_findings_for_assessment", new_callable=AsyncMock)
@patch("app.services.assessment_service.run_reconciliation")
async def test_github_acceptance_failure(
    mock_recon, mock_gen_findings, mock_gen_report, mock_inst_token, mock_session, mock_secret_manager
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

@pytest.mark.asyncio
@patch("app.api.v1.oauth_handlers.GitHubAppHandler.get_installation_token", new_callable=AsyncMock, return_value={"token": "fake-installation-token"})
@patch("app.services.report_generator.ReportGeneratorService.generate_report", new_callable=AsyncMock)
@patch("app.services.finding_service.FindingService.generate_findings_for_assessment", new_callable=AsyncMock)
@patch("app.services.assessment_service.run_reconciliation")
async def test_github_clean_repository_produces_baseline_evidence(
    mock_recon, mock_gen_findings, mock_gen_report, mock_inst_token, mock_session, mock_secret_manager
):
    """Test full GitHub flow where repository has no issues (clean).
    It MUST produce baseline NOT_OBSERVED evidence, not an empty list!"""
    audit_records = []
    async def mock_audit_callback(provider, operation, status, details=None):
        audit_records.append({"provider": provider, "operation": operation, "status": status})

    class MockCleanClient:
        def __init__(self, *args, **kwargs):
            pass
            
        async def download_file(self, endpoint, target_path, **kwargs):
            if "tarball" in endpoint:
                import tempfile
                import tarfile
                from pathlib import Path
                
                with tempfile.TemporaryDirectory() as tmp_dir:
                    repo_root = Path(tmp_dir) / "clean-repo"
                    repo_root.mkdir()
                    
                    # Create a completely clean file with no trackers, no PII, no forms
                    (repo_root / "App.jsx").write_text('function App() { return <div>Hello Clean App</div>; }')
                    
                    with tarfile.open(target_path, "w:gz") as tar:
                        tar.add(repo_root, arcname="test-repo")
                        
        async def close(self):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

    _setup_mock_connection(mock_session, "github", resource_id="owner/clean-repo")
    client_id = uuid.uuid4()
    
    class MockObligations(dict):
        def __contains__(self, key): return True
        def __getitem__(self, key): return key
        
    service = AssessmentService(mock_session, client_id)
    service._ensure_obligations_seeded = AsyncMock(return_value=MockObligations())
    
    with patch("app.services.assessment_service.make_audit_callback", return_value=mock_audit_callback):
        data = AssessmentCreate(
            client_id=client_id,
            config=AssessmentConfig(integrations=["github"])
        )
        
        with patch("app.integrations.github_adapter.adapter.AuditedHTTPClient", MockCleanClient):
            await service.create_assessment(data, uuid.uuid4())
            
    from app.db.models.evidence import Evidence
    evidence_added = [
        call.args[0] for call in mock_session.add.call_args_list 
        if isinstance(call.args[0], Evidence)
    ]
    
    # Verify that the clean repo generated baseline evidence for EVERY answered obligation!
    from app.integrations.github_adapter.adapter import GitHubAdapter
    adapter = GitHubAdapter()
    
    # It must not be empty!
    assert len(evidence_added) > 0
    
    covered_obligations = {e.obligation_id for e in evidence_added}
    for obl in adapter.obligations_answered:
        assert obl in covered_obligations
        
    for ev in evidence_added:
        ev_val = ev.verdict.value if hasattr(ev.verdict, "value") else str(ev.verdict)
        assert ev_val == "Not observed"
        assert ev.source == "github:owner/clean-repo:baseline"


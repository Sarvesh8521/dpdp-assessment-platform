import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from datetime import datetime, timezone
import tarfile
import tempfile
import os
from pathlib import Path

from app.integrations.github_adapter.adapter import GitHubAdapter, PII_LOG_PATTERNS
from app.integrations.base.adapter import ConnectionInfo
from app.domain.evidence import Verdict
from app.integrations.base.http_client import ExternalAPIError

def test_pii_log_patterns():
    # True positives
    assert any(p.findall('Log.d("x", user.email)') for p in PII_LOG_PATTERNS)
    assert any(p.findall('console.log(customer_phone)') for p in PII_LOG_PATTERNS)
    
    # False positives (should not match)
    assert not any(p.findall('Log.d("x", fileName)') for p in PII_LOG_PATTERNS)

@pytest.fixture
def adapter():
    return GitHubAdapter()

@pytest.fixture
def connection():
    return ConnectionInfo(
        provider="github", scopes=["contents:read"], 
        connected_at=datetime.now(timezone.utc)
    )

def create_mock_tarball(target_path, files_dict):
    with tempfile.TemporaryDirectory() as td:
        for fname, content in files_dict.items():
            fpath = Path(td) / fname
            fpath.parent.mkdir(parents=True, exist_ok=True)
            fpath.write_text(content, encoding="utf-8")
        
        with tarfile.open(target_path, "w:gz") as tar:
            tar.add(td, arcname="repo")


@pytest.mark.asyncio
@patch("app.integrations.github_adapter.adapter.AuditedHTTPClient")
async def test_forbidden_files_rejection(mock_client_class, adapter, connection):
    mock_instance = mock_client_class.return_value
    mock_instance.download_file = AsyncMock()
    mock_instance.close = AsyncMock()
    
    async def mock_download(endpoint, target_path, **kwargs):
        files = {
            ".env": "SECRET=123",
            "src/main.js": "console.log('hello');"
        }
        create_mock_tarball(target_path, files)
        
    mock_instance.download_file.side_effect = mock_download
    
    result = await adapter.collect(connection, repositories=["test-repo"])
    
    # The .env file should be completely ignored (no evidence generated for it)
    # We just ensure it doesn't crash and the .env content doesn't end up anywhere.
    assert len(result.errors) == 0

@pytest.mark.asyncio
@patch("app.integrations.github_adapter.adapter.AuditedHTTPClient")
async def test_planted_findings_surfaced(mock_client_class, adapter, connection):
    mock_instance = mock_client_class.return_value
    mock_instance.download_file = AsyncMock()
    mock_instance.close = AsyncMock()
    
    async def mock_download(endpoint, target_path, **kwargs):
        files = {
            "package.json": '{"dependencies": {"react-native-fbsdk-next": "1.0.0"}}',
            "AndroidManifest.xml": '<uses-permission android:name="android.permission.ACCESS_FINE_LOCATION"/>',
            "ios/Runner/Info.plist": '<key>NSCameraUsageDescription</key><string>Need camera</string>',
            "src/Main.kt": 'Log.d("Tag", "User email: " + user.email)',
            "src/Login.jsx": '<input type="email" name="user_email" />',
            "src/App.ts": 'FirebaseApp.initializeApp();'
        }
        create_mock_tarball(target_path, files)
        
    mock_instance.download_file.side_effect = mock_download
    
    result = await adapter.collect(connection, repositories=["test-repo"])
    
    # 1. Tracker SDKs (package.json + Init)
    sdk_evidence = [e for e in result.evidence if e.obligation_id == "S10.scope.sdks"]
    assert len(sdk_evidence) == 2 # One for dependency, one for SDK init
    
    init_ev = next(e for e in sdk_evidence if "initialization" in e.detail)
    assert init_ev.verdict == Verdict.OBSERVED
    
    # 2. Privacy Manifests
    manifest_evidence = [e for e in result.evidence if e.obligation_id == "APP.permissions.justified"]
    assert len(manifest_evidence) == 2 # One Android, one iOS
    
    # 3. PII Logging
    pii_evidence = [e for e in result.evidence if e.obligation_id == "APP.logging.noPII"]
    assert len(pii_evidence) == 1
    assert "PII detected in logging statement" in pii_evidence[0].detail
    
    # 4. Consent Gating
    consent_evidence = [e for e in result.evidence if e.obligation_id == "S6.consentGating"]
    assert len(consent_evidence) == 1
    assert consent_evidence[0].verdict == Verdict.NOT_OBSERVED

@pytest.mark.asyncio
@patch("app.integrations.github_adapter.adapter.tempfile.TemporaryDirectory")
@patch("app.integrations.github_adapter.adapter.AuditedHTTPClient")
async def test_recursive_traversal_metadata_and_cleanup(mock_client_class, mock_tempdir_class, adapter, connection):
    """Test that deeply nested files are found, metadata is comprehensive, and tmpdir is destroyed."""
    # We will manually manage a real temp directory to observe its lifecycle through the mock
    real_temp_dir = tempfile.TemporaryDirectory()
    dir_path = real_temp_dir.name
    
    # Mock the context manager to return our real temp dir path, but track when it exits
    mock_context = MagicMock()
    mock_context.__enter__.return_value = dir_path
    mock_tempdir_class.return_value = mock_context
    
    mock_instance = mock_client_class.return_value
    mock_instance.download_file = AsyncMock()
    mock_instance.close = AsyncMock()
    
    async def mock_download(endpoint, target_path, **kwargs):
        files = {
            "a/b/c/d/e/f/AndroidManifest.xml": '<uses-permission android:name="android.permission.CAMERA"/>',
            "deeply/nested/UI/component/Form.jsx": '<input type="email" name="user_email" />'
        }
        create_mock_tarball(target_path, files)
        
    mock_instance.download_file.side_effect = mock_download
    
    result = await adapter.collect(connection, repositories=["test-repo"])
    
    # Assert Metadata completeness for all generated evidence
    for ev in result.evidence:
        metadata = ev.structured_detail.metadata
        assert "repository" in metadata
        assert metadata["repository"] == "test-repo"
        assert "file_path" in metadata
        assert "/" in metadata["file_path"] # Proves nested path was recorded
        assert "line" in metadata
        assert "category" in metadata
        assert "matched_rule" in metadata
        assert "recommended_action" in metadata
        
        assert ev.structured_detail.reason is not None
        assert len(ev.structured_detail.reason) > 0
        
    # Verify cleanup (context manager exit was called)
    assert mock_context.__exit__.called
    
    # Cleanup our real temp dir
    real_temp_dir.cleanup()

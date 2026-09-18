import asyncio
import base64
import os
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest
from httpx import Response, Request

from app.domain.evidence import Verdict
from app.integrations.github_adapter.adapter import GitHubAdapter
from app.integrations.base.http_client import AuditedHTTPClient, ExternalAPIError

@pytest.mark.asyncio
async def test_github_recursive_analysis():
    # Setup a mock tarball containing issues in nested directories
    adapter = GitHubAdapter()
    
    # We will mock the `download_file` method of AuditedHTTPClient
    class MockClient:
        async def download_file(self, endpoint, target_path, **kwargs):
            # Create a mock tarball at target_path
            with tempfile.TemporaryDirectory() as tmpdir:
                # Root dir of repo in tarball
                repo_root = Path(tmpdir) / "test-repo-12345"
                repo_root.mkdir()
                
                # File 1: Tracker SDK (React Native FB SDK)
                (repo_root / "package.json").write_text('{"dependencies": {"react-native-fbsdk": "^3.0.0"}}')
                
                # File 2: Android permissions
                android_dir = repo_root / "app" / "src" / "main"
                android_dir.mkdir(parents=True)
                (android_dir / "AndroidManifest.xml").write_text('<uses-permission android:name="android.permission.READ_CONTACTS"/>')
                
                # File 3: iOS permissions
                ios_dir = repo_root / "ios" / "Runner"
                ios_dir.mkdir(parents=True)
                (ios_dir / "Info.plist").write_text('<key>NSContactsUsageDescription</key><string>Need contacts</string>')
                
                # File 4: PII logging deeply nested
                nested_dir = repo_root / "src" / "components" / "auth"
                nested_dir.mkdir(parents=True)
                (nested_dir / "Login.tsx").write_text('console.log("User email:", user.email);')
                
                # File 5: Form fields
                (nested_dir / "Signup.tsx").write_text('<input type="email" />')
                
                # File 6: Tracker SDK Init without gating
                (nested_dir / "App.tsx").write_text('import firebase from "firebase";\\nFirebaseApp.initializeApp(config);')
                
                # File 7: Forbidden directory (should be ignored)
                forbidden_dir = repo_root / "node_modules" / "some-package"
                forbidden_dir.mkdir(parents=True)
                (forbidden_dir / "malicious.js").write_text('console.log("email");') # Should NOT trigger a finding
                
                # File 8: Forbidden file
                (repo_root / ".env").write_text('SECRET=123')
                
                # Create tarball
                with tarfile.open(target_path, "w:gz") as tar:
                    tar.add(repo_root, arcname="test-repo-12345")
                    
        async def close(self):
            pass

    # Monkeypatch AuditedHTTPClient to return our MockClient
    import app.integrations.github_adapter.adapter as adapter_module
    
    original_client = adapter_module.AuditedHTTPClient
    
    class PatchedAuditedHTTPClient(original_client):
        def __new__(cls, *args, **kwargs):
            return MockClient()
            
    adapter_module.AuditedHTTPClient = PatchedAuditedHTTPClient
    
    try:
        now = datetime.now(timezone.utc)
        evidence = []
        errors = []
        warnings = []
        
        await adapter._analyze_repository(
            client=MockClient(),
            repo="test-org/test-repo",
            evidence=evidence,
            errors=errors,
            warnings=warnings,
            now=now
        )
        
        # Verify findings
        assert len(errors) == 0
        assert len(warnings) == 0
        
        # Verify package.json SDK
        assert any(e.source == "github:test-org/test-repo:package.json" and "meta" in e.detail.lower() for e in evidence)
        
        # Verify Android Manifest
        assert any(e.source == "github:test-org/test-repo:app/src/main/AndroidManifest.xml:android.permission.READ_CONTACTS" for e in evidence)
        
        # Verify Info.plist
        assert any(e.source == "github:test-org/test-repo:ios/Runner/Info.plist:NSContactsUsageDescription" for e in evidence)
        
        # Verify PII logging in nested dir
        pii_finding = next((e for e in evidence if e.obligation_id == "APP.logging.noPII"), None)
        assert pii_finding is not None
        assert pii_finding.source == "github:test-org/test-repo:src/components/auth/Login.tsx"
        assert pii_finding.structured_detail.metadata["line"] == 1
        
        # Verify Form field
        form_finding = next((e for e in evidence if e.obligation_id == "APP.forms.disclosure"), None)
        assert form_finding is not None
        assert form_finding.source == "github:test-org/test-repo:src/components/auth/Signup.tsx"
        
        # Verify SDK Init without gating
        init_finding = next((e for e in evidence if e.obligation_id == "S10.scope.sdks" and "App.tsx" in e.source), None)
        assert init_finding is not None
        
        gating_finding = next((e for e in evidence if e.obligation_id == "S6.consentGating" and "App.tsx" in e.source), None)
        assert gating_finding is not None
        assert gating_finding.verdict == Verdict.NOT_OBSERVED # No gating found
        
        # Verify ignored files
        # Check that no evidence came from node_modules
        assert not any("node_modules" in e.source for e in evidence)
        # Check that no evidence came from .env
        assert not any(".env" in e.source for e in evidence)
        
    finally:
        adapter_module.AuditedHTTPClient = original_client

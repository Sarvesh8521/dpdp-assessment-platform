import pytest
from unittest.mock import AsyncMock, patch
from datetime import datetime, timezone
import base64

from app.integrations.github_adapter.adapter import GitHubAdapter, PII_LOG_PATTERNS
from app.integrations.base.adapter import ConnectionInfo
from app.domain.evidence import Verdict
from app.integrations.base.http_client import ExternalAPIError

def test_pii_log_patterns():
    # True positives
    assert any(p.findall('Log.d("x", user.email)') for p in PII_LOG_PATTERNS)
    assert any(p.findall('console.log(customer_phone)') for p in PII_LOG_PATTERNS)
    assert any(p.findall('print("Address: " + address)') for p in PII_LOG_PATTERNS)
    
    # False positives (should not match)
    assert not any(p.findall('Log.d("x", fileName)') for p in PII_LOG_PATTERNS)
    assert not any(p.findall('console.log(className)') for p in PII_LOG_PATTERNS)
    assert not any(p.findall('logger.info(rename_file)') for p in PII_LOG_PATTERNS)

@pytest.fixture
def adapter():
    return GitHubAdapter()

@pytest.fixture
def connection():
    return ConnectionInfo(
        provider="github", scopes=["contents:read"], 
        connected_at=datetime.now(timezone.utc)
    )

@pytest.mark.asyncio
@patch("app.integrations.github_adapter.adapter.AuditedHTTPClient")
async def test_forbidden_files_rejection(mock_client_class, adapter, connection):
    mock_instance = mock_client_class.return_value
    mock_instance.get = AsyncMock()
    mock_instance.close = AsyncMock()
    
    async def mock_get(endpoint, **kwargs):
        if "contents/src" in endpoint:
            return [{"name": ".env", "path": "src/.env", "type": "file", "url": "https://api.github.com/repos/test-repo/contents/src/.env"}]
        raise ExternalAPIError(status_code=404, message="Not found")
        
    mock_instance.get.side_effect = mock_get
    
    result = await adapter.collect(connection, repositories=["test-repo"])
    
    assert any("Blocked attempt to read forbidden file" in w and ".env" in w for w in result.warnings)
    get_calls = [call.args[0] for call in mock_instance.get.call_args_list]
    assert not any(".env" in call and "get_source_file" in call for call in get_calls)

@pytest.mark.asyncio
@patch("app.integrations.github_adapter.adapter.AuditedHTTPClient")
async def test_planted_findings_surfaced(mock_client_class, adapter, connection):
    mock_instance = mock_client_class.return_value
    mock_instance.get = AsyncMock()
    mock_instance.close = AsyncMock()
    
    async def mock_get(endpoint, **kwargs):
        if endpoint.endswith("contents/package.json"):
            content = '{"dependencies": {"react-native-fbsdk-next": "1.0.0"}}'
            return {"content": base64.b64encode(content.encode()).decode()}
        elif endpoint.endswith("AndroidManifest.xml"):
            content = '<uses-permission android:name="android.permission.ACCESS_FINE_LOCATION"/>'
            return {"content": base64.b64encode(content.encode()).decode()}
        elif endpoint.endswith("ios/Runner/Info.plist"):
            content = '<key>NSCameraUsageDescription</key><string>Need camera</string>'
            return {"content": base64.b64encode(content.encode()).decode()}
        elif endpoint.endswith("contents/src"):
            return [
                {"name": "Main.kt", "path": "src/Main.kt", "type": "file", "url": "https://api.github.com/repos/test-repo/contents/src/Main.kt"},
                {"name": "Login.jsx", "path": "src/Login.jsx", "type": "file", "url": "https://api.github.com/repos/test-repo/contents/src/Login.jsx"},
                {"name": "App.ts", "path": "src/App.ts", "type": "file", "url": "https://api.github.com/repos/test-repo/contents/src/App.ts"}
            ]
        elif endpoint.endswith("src/Main.kt"):
            # Has PII log
            content = 'Log.d("Tag", "User email: " + user.email)'
            return {"content": base64.b64encode(content.encode()).decode()}
        elif endpoint.endswith("src/Login.jsx"):
            # Has Form Field
            content = '<input type="email" name="user_email" />'
            return {"content": base64.b64encode(content.encode()).decode()}
        elif endpoint.endswith("src/App.ts"):
            # Has SDK Init, but NO consent gating
            content = 'FirebaseApp.initializeApp();'
            return {"content": base64.b64encode(content.encode()).decode()}
        raise ExternalAPIError(status_code=404, message="Not found")
        
    mock_instance.get.side_effect = mock_get
    
    result = await adapter.collect(connection, repositories=["test-repo"])
    
    # 1. Tracker SDKs (package.json + Init)
    sdk_evidence = [e for e in result.evidence if e.obligation_id == "S10.scope.sdks"]
    assert len(sdk_evidence) == 2 # One for dependency, one for SDK init
    
    init_ev = next(e for e in sdk_evidence if e.source.endswith("sdk_init_analysis"))
    assert init_ev.verdict == Verdict.OBSERVED
    
    # 2. Consent Gating (since we planted SDK Init without gating)
    consent_evidence = next(e for e in result.evidence if e.obligation_id == "S6.consentGating")
    assert consent_evidence.verdict == Verdict.NOT_OBSERVED
    assert "without detectable consent gating" in consent_evidence.detail
    
    # 3. Sensitive Permissions (Android + iOS)
    perm_evidence = [e for e in result.evidence if e.obligation_id == "APP.permissions.justified"]
    assert len(perm_evidence) == 2
    assert any("ACCESS_FINE_LOCATION" in e.detail for e in perm_evidence)
    assert any("NSCameraUsageDescription" in e.detail for e in perm_evidence)
    
    # 4. PII in Logs
    pii_evidence = next(e for e in result.evidence if e.obligation_id == "APP.logging.noPII")
    assert pii_evidence.verdict == Verdict.NOT_OBSERVED
    assert "src/Main.kt" in pii_evidence.detail
    
    # 5. Form Fields
    form_evidence = next(e for e in result.evidence if e.obligation_id == "APP.forms.disclosure")
    assert form_evidence.verdict == Verdict.OBSERVED
    assert form_evidence.raw_metadata["files_with_forms"] == 1

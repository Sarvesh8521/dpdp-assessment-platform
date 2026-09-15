"""GitHub adapter — build last, scope narrowly. PHASE2_SPEC Section 4.6.

GitHub App (NOT personal access token), scoped to named repositories.
Static analysis only. Ephemeral container/tmpfs.

NEVER: clone to persistent storage, store source code, read .env/secrets, write operations.
Evidence store gets findings + file references, never source code.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.logging import get_logger
from app.domain.evidence import Confidence, EvidenceItem, Verdict
from app.domain.obligations import get_obligations_for_integration
from app.integrations.base.adapter import (
    AdapterResult, ConnectionInfo, HealthStatus, IntegrationAdapter,
)
from app.integrations.base.http_client import AuditedHTTPClient, ExternalAPIError

logger = get_logger(__name__)

GITHUB_FORBIDDEN_ENDPOINTS = [
    # Secrets / credentials
    "/repos/*/actions/secrets",
    "/repos/*/environments/*/secrets",
    "/repos/*/dependabot/secrets",
    "actions/secrets",
    # Write operations
    "/repos/*/contents",  # PUT/POST on contents = write
    "/repos/*/git/refs",
    "/repos/*/git/commits",
    "/repos/*/merges",
    "/repos/*/pulls",  # Creating PRs
    "/repos/*/issues",  # Creating issues
    "/repos/*/dispatches",
    "/repos/*/hooks",
]

# Files that must NEVER be read
FORBIDDEN_FILES = {
    ".env", ".env.local", ".env.production", ".env.staging",
    ".env.development", ".env.test",
    "credentials.json", "service-account.json",
    "id_rsa", "id_ed25519", ".npmrc",  # May contain tokens
}

# Tracker SDKs to detect
TRACKER_SDKS = {
    "firebase": ["com.google.firebase", "firebase-analytics", "@react-native-firebase"],
    "branch": ["io.branch.sdk", "branch-sdk", "react-native-branch"],
    "appsflyer": ["com.appsflyer", "react-native-appsflyer", "appsflyer-flutter"],
    "clevertap": ["com.clevertap", "clevertap-react-native"],
    "moengage": ["com.moengage", "react-native-moengage"],
    "meta": ["com.facebook.sdk", "react-native-fbsdk", "facebook-android-sdk"],
    "google_analytics": ["com.google.android.gms:play-services-analytics"],
    "google_ads": ["com.google.android.gms:play-services-ads"],
}

# Sensitive Android permissions
SENSITIVE_PERMISSIONS = {
    "android.permission.READ_CONTACTS",
    "android.permission.READ_PHONE_STATE",
    "android.permission.ACCESS_FINE_LOCATION",
    "android.permission.ACCESS_COARSE_LOCATION",
    "android.permission.CAMERA",
    "android.permission.RECORD_AUDIO",
    "android.permission.READ_CALL_LOG",
    "android.permission.READ_SMS",
    "android.permission.READ_CALENDAR",
    "android.permission.BODY_SENSORS",
}

# Sensitive iOS permissions (Info.plist keys)
IOS_SENSITIVE_PERMISSIONS = {
    "NSLocationWhenInUseUsageDescription",
    "NSLocationAlwaysUsageDescription",
    "NSLocationAlwaysAndWhenInUseUsageDescription",
    "NSCameraUsageDescription",
    "NSMicrophoneUsageDescription",
    "NSContactsUsageDescription",
    "NSCalendarsUsageDescription",
    "NSPhotoLibraryUsageDescription",
}

# Tracker SDK initialization patterns
TRACKER_SDK_INIT_PATTERNS = [
    re.compile(r"FirebaseApp\.initializeApp", re.IGNORECASE),
    re.compile(r"branch\.initSession", re.IGNORECASE),
    re.compile(r"appsFlyer\.initSdk", re.IGNORECASE),
    re.compile(r"CleverTapAPI\.createInstance", re.IGNORECASE),
    re.compile(r"MoEngage\.Builder", re.IGNORECASE),
    re.compile(r"AppEventsLogger\.activateApp", re.IGNORECASE),
    re.compile(r"GoogleAnalytics\.getInstance", re.IGNORECASE),
]

# Consent gating patterns
CONSENT_GATING_PATTERNS = [
    re.compile(r"if\s*\([^)]*(?:consent|granted|allow)[^)]*\)", re.IGNORECASE),
]

# Personal data form fields patterns
FORM_FIELD_PATTERNS = [
    re.compile(r'<input[^>]*type=[\'"](?:email|password|tel)[\'"]', re.IGNORECASE),
    re.compile(r'secureTextEntry', re.IGNORECASE),
    re.compile(r'<TextInput[^>]*textContentType=[\'"](?:emailAddress|password|telephoneNumber)[\'"]', re.IGNORECASE),
    re.compile(r'android:inputType=[\'"]text(?:EmailAddress|Password|Phonetic)[\'"]', re.IGNORECASE),
]

# PII logging patterns
PII_LOG_PATTERNS = [
    re.compile(r"Log\.\w+\(.*(?<![a-z])(?:email|phone|name|address|ssn|aadhaar)(?![a-z]).*\)", re.IGNORECASE),
    re.compile(r"console\.log\(.*(?<![a-z])(?:email|phone|name|address)(?![a-z]).*\)", re.IGNORECASE),
    re.compile(r"print\(.*(?<![a-z])(?:email|phone|name|address)(?![a-z]).*\)", re.IGNORECASE),
    re.compile(r"logger\..*\(.*(?<![a-z])(?:email|phone|name|address)(?![a-z]).*\)", re.IGNORECASE),
    re.compile(r"NSLog\(.*(?<![a-z])(?:email|phone|name|address)(?![a-z]).*\)", re.IGNORECASE),
]

class GitHubAdapter(IntegrationAdapter):
    @property
    def id(self) -> str:
        return "github"

    @property
    def provider(self) -> str:
        return "github"

    @property
    def required_scopes(self) -> list[str]:
        return ["contents:read", "metadata:read"]

    @property
    def obligations_answered(self) -> list[str]:
        return get_obligations_for_integration("github")

    @property
    def scope_obligation_map(self) -> dict[str, list[str]]:
        return {
            "contents:read": [
                "S6.consentGating", "APP.sdk.consent",
                "APP.permissions.justified", "APP.logging.noPII",
                "S10.scope.sdks", "S10.scope.platforms",
                "APP.forms.disclosure",
            ],
        }

    @property
    def forbidden_endpoints(self) -> list[str]:
        return GITHUB_FORBIDDEN_ENDPOINTS

    async def connect(self, credentials: dict[str, Any]) -> ConnectionInfo:
        return ConnectionInfo(
            provider=self.provider, scopes=self.required_scopes,
            connected_at=datetime.now(timezone.utc),
            metadata={"installation_id": credentials.get("installation_id", "")},
        )

    async def health_check(self, connection: ConnectionInfo) -> HealthStatus:
        """Check the health of a GitHub App connection.

        Makes a real API call to verify the installation token works.
        Does NOT blindly return healthy=True.
        """
        installation_id = connection.metadata.get("installation_id", "")

        if not installation_id:
            return HealthStatus(
                healthy=False, provider=self.provider,
                message="No installation_id — GitHub App may not be installed",
                checked_at=datetime.now(timezone.utc),
            )

        try:
            # Try to list repos with the provided access token
            # (should be an installation token)
            client = AuditedHTTPClient(
                provider=self.provider,
                base_url="https://api.github.com",
                access_token=connection.metadata.get("access_token", ""),
                forbidden_endpoints=self.forbidden_endpoints,
                obligations_served=self.obligations_answered,
            )
            try:
                resp = await client.get(
                    "/installation/repositories",
                    params={"per_page": 1},
                    operation="health_check_repos",
                )
                total = resp.get("total_count", 0)
                return HealthStatus(
                    healthy=True, provider=self.provider,
                    message=f"GitHub App installed, {total} repo(s) accessible",
                    checked_at=datetime.now(timezone.utc),
                    details={"total_repos": total, "installation_id": installation_id},
                )
            except ExternalAPIError as exc:
                return HealthStatus(
                    healthy=False, provider=self.provider,
                    message=f"GitHub API error: {exc.status_code}",
                    checked_at=datetime.now(timezone.utc),
                    details={"error": str(exc)},
                )
            finally:
                await client.close()
        except Exception as e:
            return HealthStatus(
                healthy=False, provider=self.provider,
                message=f"Health check failed: {str(e)}",
                checked_at=datetime.now(timezone.utc),
            )

    async def collect(
        self, connection: ConnectionInfo, *,
        assessment_id: str | None = None, client_id: str | None = None,
        access_token: str = "", audit_callback: Any = None,
        repositories: list[str] | None = None,
    ) -> AdapterResult:
        """Static analysis of repositories. Ephemeral — no code persisted."""
        evidence: list[EvidenceItem] = []
        errors: list[str] = []
        warnings: list[str] = []
        now = datetime.now(timezone.utc)

        if not repositories:
            evidence.append(EvidenceItem(
                obligation_id="APP.sdk.consent",
                verdict=Verdict.CANNOT_DETERMINE, confidence=Confidence.LOW,
                source="github:no_repos", observed_at=now,
                detail="No repositories configured for analysis.",
            ))
            return AdapterResult(evidence=evidence)

        client = AuditedHTTPClient(
            provider=self.provider, base_url="https://api.github.com",
            access_token=access_token,
            forbidden_endpoints=self.forbidden_endpoints,
            obligations_served=self.obligations_answered,
            client_id=client_id, assessment_id=assessment_id,
            audit_callback=audit_callback,
        )

        try:
            for repo in repositories:
                try:
                    await self._analyze_repository(
                        client, repo, evidence, errors, warnings, now,
                    )
                except ExternalAPIError as exc:
                    errors.append(f"Failed to analyze {repo}: {exc}")

            return AdapterResult(evidence=evidence, errors=errors, warnings=warnings)
        finally:
            await client.close()

    async def _analyze_repository(
        self,
        client: AuditedHTTPClient,
        repo: str,
        evidence: list[EvidenceItem],
        errors: list[str],
        warnings: list[str],
        now: datetime,
    ) -> None:
        """Analyze a single repository — static analysis, no persistent storage."""
        # Check dependency files for SDK inventory
        sdk_files = [
            ("package.json", "npm"),
            ("Podfile", "cocoapods"),
            ("build.gradle", "gradle"),
            ("app/build.gradle", "gradle"),
            ("pubspec.yaml", "flutter"),
        ]

        found_sdks: list[dict[str, str]] = []

        for filepath, pkg_manager in sdk_files:
            if Path(filepath).name in FORBIDDEN_FILES:
                warnings.append(f"Blocked attempt to read forbidden file: {filepath}")
                continue
            try:
                content_resp = await client.get(
                    f"/repos/{repo}/contents/{filepath}",
                    operation=f"get_{filepath.replace('/', '_')}",
                )
                # Content comes base64 encoded
                import base64
                content = base64.b64decode(content_resp.get("content", "")).decode("utf-8", errors="replace")

                # Check for tracker SDKs — DO NOT store the content
                for sdk_name, patterns in TRACKER_SDKS.items():
                    for pattern in patterns:
                        if pattern.lower() in content.lower():
                            found_sdks.append({
                                "sdk": sdk_name,
                                "file": filepath,
                                "manager": pkg_manager,
                            })

            except ExternalAPIError as exc:
                if exc.status_code in (401, 403):
                    warnings.append(f"Access denied reading {filepath} in {repo}")
                pass  # File doesn't exist in this repo

        if found_sdks:
            evidence.append(EvidenceItem(
                obligation_id="S10.scope.sdks",
                verdict=Verdict.OBSERVED, confidence=Confidence.HIGH,
                source=f"github:{repo}:dependency_analysis", observed_at=now,
                detail=f"Found {len(found_sdks)} tracker SDKs: {', '.join(s['sdk'] for s in found_sdks)}",
                raw_metadata={"sdks": found_sdks},
            ))

        # Check AndroidManifest.xml for permissions
        try:
            manifest_path = "app/src/main/AndroidManifest.xml"
            if Path(manifest_path).name in FORBIDDEN_FILES:
                warnings.append(f"Blocked attempt to read forbidden file: {manifest_path}")
            else:
                manifest_resp = await client.get(
                    f"/repos/{repo}/contents/{manifest_path}",
                    operation="get_android_manifest",
                )
                import base64
                manifest = base64.b64decode(manifest_resp.get("content", "")).decode("utf-8", errors="replace")
    
                found_permissions = []
                for perm in SENSITIVE_PERMISSIONS:
                    if perm in manifest:
                        found_permissions.append(perm)
    
                if found_permissions:
                    evidence.append(EvidenceItem(
                        obligation_id="APP.permissions.justified",
                        verdict=Verdict.OBSERVED, confidence=Confidence.HIGH,
                        source=f"github:{repo}:android_manifest", observed_at=now,
                        detail=f"Sensitive Android permissions found: {', '.join(found_permissions)}",
                        raw_metadata={"permissions": found_permissions},
                    ))
        except ExternalAPIError as exc:
            if exc.status_code in (401, 403):
                warnings.append(f"Access denied reading AndroidManifest in {repo}")
            pass

        # Check Info.plist for iOS permissions
        try:
            plist_path = "ios/Runner/Info.plist" # common path for flutter, but could be ios/App/Info.plist etc. Let's just try ios/Runner for now as a representative check
            if Path(plist_path).name in FORBIDDEN_FILES:
                warnings.append(f"Blocked attempt to read forbidden file: {plist_path}")
            else:
                plist_resp = await client.get(
                    f"/repos/{repo}/contents/{plist_path}",
                    operation="get_info_plist",
                )
                import base64
                plist_content = base64.b64decode(plist_resp.get("content", "")).decode("utf-8", errors="replace")
                
                found_ios_permissions = []
                for perm in IOS_SENSITIVE_PERMISSIONS:
                    if perm in plist_content:
                        found_ios_permissions.append(perm)
                        
                if found_ios_permissions:
                    evidence.append(EvidenceItem(
                        obligation_id="APP.permissions.justified",
                        verdict=Verdict.OBSERVED, confidence=Confidence.HIGH,
                        source=f"github:{repo}:info_plist", observed_at=now,
                        detail=f"Sensitive iOS permissions found: {', '.join(found_ios_permissions)}",
                        raw_metadata={"ios_permissions": found_ios_permissions},
                    ))
        except ExternalAPIError as exc:
            if exc.status_code in (401, 403):
                warnings.append(f"Access denied reading Info.plist in {repo}")
            pass

        # Check for PII in logs, Form fields, and SDK initialization (sample key source files)
        source_patterns = [
            "app/src/main/java", "app/src/main/kotlin",
            "src", "lib",
        ]

        pii_in_logs_found: list[dict[str, Any]] = []
        forms_found: list[dict[str, Any]] = []
        sdk_inits_found: list[dict[str, Any]] = []
        
        for src_dir in source_patterns:
            try:
                dir_resp = await client.get(
                    f"/repos/{repo}/contents/{src_dir}",
                    operation=f"list_{src_dir.replace('/', '_')}",
                )
                if isinstance(dir_resp, list):
                    for item in dir_resp[:20]:  # Limit scope
                        if item.get("type") == "file":
                            if Path(item.get("path", "")).name in FORBIDDEN_FILES:
                                warnings.append(f"Blocked attempt to read forbidden file: {item.get('path', '')}")
                                continue
                            if any(
                                item.get("name", "").endswith(ext)
                                for ext in [".java", ".kt", ".swift", ".js", ".ts", ".dart", ".tsx", ".jsx", ".html"]
                            ):
                                try:
                                    file_resp = await client.get(
                                        item["url"].replace("https://api.github.com", ""),
                                        operation="get_source_file",
                                    )
                                    import base64
                                    file_content = base64.b64decode(
                                        file_resp.get("content", "")
                                    ).decode("utf-8", errors="replace")
    
                                    # Check PII logs
                                    for pattern in PII_LOG_PATTERNS:
                                        matches = pattern.findall(file_content)
                                        if matches:
                                            pii_in_logs_found.append({
                                                "file": item.get("path", ""),
                                                "match_count": len(matches),
                                            })
                                            break  # One match per file is enough
                                            
                                    # Check Form Fields
                                    for pattern in FORM_FIELD_PATTERNS:
                                        matches = pattern.findall(file_content)
                                        if matches:
                                            forms_found.append({
                                                "file": item.get("path", ""),
                                                "match_count": len(matches),
                                            })
                                            break
                                            
                                    # Check Tracker SDK Initialization
                                    for pattern in TRACKER_SDK_INIT_PATTERNS:
                                        matches = pattern.findall(file_content)
                                        if matches:
                                            # Check if there is consent gating nearby
                                            is_gated = any(p.search(file_content) for p in CONSENT_GATING_PATTERNS)
                                            sdk_inits_found.append({
                                                "file": item.get("path", ""),
                                                "sdk_init": matches[0],
                                                "is_gated": is_gated,
                                            })
                                            break
    
                                    # DO NOT store file_content — ephemeral only
    
                                except ExternalAPIError as exc:
                                    if exc.status_code in (401, 403):
                                        warnings.append(f"Access denied reading {item.get('path', '')} in {repo}")
                                    pass
            except ExternalAPIError as exc:
                if exc.status_code in (401, 403):
                    warnings.append(f"Access denied reading dir {src_dir} in {repo}")
                pass

        if pii_in_logs_found:
            evidence.append(EvidenceItem(
                obligation_id="APP.logging.noPII",
                verdict=Verdict.NOT_OBSERVED, confidence=Confidence.MEDIUM,
                source=f"github:{repo}:log_analysis", observed_at=now,
                detail=(
                    f"PII detected in logging statements across {len(pii_in_logs_found)} files. "
                    f"Files: {', '.join(f['file'] for f in pii_in_logs_found[:5])}"
                ),
                raw_metadata={"files_with_pii_logs": len(pii_in_logs_found)},
            ))

        if forms_found:
            evidence.append(EvidenceItem(
                obligation_id="APP.forms.disclosure",
                verdict=Verdict.OBSERVED, confidence=Confidence.HIGH,
                source=f"github:{repo}:form_analysis", observed_at=now,
                detail=f"Personal data form fields detected in {len(forms_found)} files.",
                raw_metadata={"files_with_forms": len(forms_found)},
            ))
            
        if sdk_inits_found:
            evidence.append(EvidenceItem(
                obligation_id="S10.scope.sdks",
                verdict=Verdict.OBSERVED, confidence=Confidence.HIGH,
                source=f"github:{repo}:sdk_init_analysis", observed_at=now,
                detail=f"Tracker SDK initialization detected in {len(sdk_inits_found)} files.",
                raw_metadata={"sdk_inits": sdk_inits_found},
            ))
            
            # Check consent gating based on init finding
            ungated_inits = [i for i in sdk_inits_found if not i["is_gated"]]
            if ungated_inits:
                evidence.append(EvidenceItem(
                    obligation_id="S6.consentGating",
                    verdict=Verdict.NOT_OBSERVED, confidence=Confidence.MEDIUM,
                    source=f"github:{repo}:consent_gating", observed_at=now,
                    detail=f"Found {len(ungated_inits)} SDK initializations without detectable consent gating.",
                    raw_metadata={"ungated_inits": ungated_inits},
                ))
            else:
                evidence.append(EvidenceItem(
                    obligation_id="S6.consentGating",
                    verdict=Verdict.OBSERVED, confidence=Confidence.MEDIUM,
                    source=f"github:{repo}:consent_gating", observed_at=now,
                    detail="All detected SDK initializations appear to have consent gating logic.",
                    raw_metadata={"gated_inits_count": len(sdk_inits_found)},
                ))
